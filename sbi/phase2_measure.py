#!/usr/bin/env python3
"""Phase 2 consolidated defect measurement on the Phase-1 CLIENT-prompt run.

Every candidate prompt change must be justified by a defect COUNTED here and
located in the PDF, not by a hunch. Writes phase2_measured.json.

The PDF is the adjudicator throughout: `sbi_pdf_evidence.txn_rows` reconstructs
printed rows geometrically (date token + narration + money token + the right-hand
C/D marker grouped on one baseline), which is the only way to attribute SBI's
C/D column to the right row -- in plain text order that marker lands on its own line.
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sbi_lib as L          # noqa: E402
import sbi_pdf_evidence as E  # noqa: E402
import score_lib_sbi as S    # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
ARM = os.path.join(ROOT, "run_p1_client")


def dt(v):
    """-> comparable (y, m, d) or None."""
    s = S.date_norm(v)
    if not s or not re.match(r"^\d{2}/\d{2}/\d{4}$", str(s)):
        return None
    d, m, y = str(s).split("/")
    return (int(y), int(m), int(d))


def leading_band(path):
    """Rows printed on page 1 ABOVE the 'TRANSACTIONS FOR <name>' header.

    SBI prints statement-level credits (PAYMENT RECEIVED, FUEL SURCHARGE WAIVER)
    in this band, BEFORE the per-cardholder transaction header, and those rows are
    frequently dated LATER than the rows that follow. A model that assumes one
    chronologically-ordered table starting at the header can miss the whole band.
    """
    doc = fitz.open(path)
    hdr = [r.y0 for r in (doc[0].search_for("TRANSACTIONS FOR") or [])]
    doc.close()
    rows = E.txn_rows(path)
    if not hdr:
        return None, rows
    y = min(hdr)
    return [g for g in rows if g["page"] == 1 and g["y"] < y], rows


def main():
    sample = json.load(open(os.path.join(ROOT, "phase1_sample.json")))
    ids = sample["sample_ids"]
    corpus = {s: p for s, f, p in L.discover_pdfs()}
    csvref, _ = S.load_csv_incumbent()

    pat = Counter()
    out = {"arm": ARM, "n": len(ids), "statements": {}}

    for sid in ids:
        rec = json.load(open(os.path.join(ARM, "json", f"{sid}.json")))
        p = rec.get("parsed_json") or {}
        ltx = p.get("transactions") or []
        inc = csvref.get(sid, {})
        itx = inc.get("transactions") or []
        path = corpus[sid]
        band, geom = leading_band(path)

        ps = dt(S.dig(p, "statementMeta.statementPeriodStart"))
        pe = dt(S.dig(p, "statementMeta.statementPeriodEnd"))
        sd = dt(S.dig(p, "statementMeta.statementDate"))

        # ---- key each side by (date, amount) for COMPLETENESS diagnosis only.
        # (The scored numbers use the description-only 1:1 matcher; this composite
        # key is used here purely to ask "is the printed row present at all".)
        lk = Counter((S.date_norm(t.get("date")),
                      round(float(t["amount"]), 2) if t.get("amount") is not None else None)
                     for t in ltx if isinstance(t, dict))
        dropped = []
        for g in geom:
            k = (S.date_norm(g["date_norm"]),
                 round(g["amount"], 2) if g["amount"] is not None else None)
            if lk[k] > 0:
                lk[k] -= 1
            else:
                dropped.append(g)

        st = {"outcome": rec.get("outcome"), "finish_reason": rec.get("finish_reason"),
              "usage": rec.get("usage_raw"),
              "n_luna": len(ltx), "n_incumbent": len(itx), "n_pdf_geom": len(geom),
              "period": [S.dig(p, "statementMeta.statementPeriodStart"),
                         S.dig(p, "statementMeta.statementPeriodEnd")],
              "statementDate": S.dig(p, "statementMeta.statementDate"),
              "leading_band_rows": len(band) if band is not None else None,
              "dropped": [], "defects": []}

        def d(kind, note, ev):
            st["defects"].append({"kind": kind, "note": note, "evidence": ev})
            pat[kind] += 1

        # ---- D1: printed rows absent from Luna's output, bucketed by CAUSE
        for g in dropped:
            gd = dt(g["date_norm"])
            why = "unclassified"
            if ps and gd and gd < ps:
                why = "BEFORE_periodStart"
            elif pe and gd and gd > pe:
                why = "AFTER_periodEnd"
            elif sd and gd and gd > sd:
                why = "AFTER_statementDate"
            elif band is not None and any(b["y"] == g["y"] and b["page"] == g["page"]
                                          for b in band):
                why = "LEADING_BAND"
            st["dropped"].append({"page": g["page"], "date": g["date"],
                                  "amount": g["amount"], "marker": g["marker"],
                                  "desc": g["description_geom"][:60], "cause": why})
            d("LUNA_DROPPED_PRINTED_ROW__" + why,
              f"printed row absent from Luna output ({why})",
              {"page": g["page"], "y": g["y"], "date": g["date"],
               "amount": g["amount"], "marker": g["marker"],
               "desc": g["description_geom"][:70]})

        # ---- D2: amount magnitude errors (1000x / 100x scaling) against the print
        for t in ltx:
            a = t.get("amount")
            if a is None:
                continue
            a = float(a)
            for g in geom:
                if g["amount"] is None:
                    continue
                if S.date_norm(g["date_norm"]) != S.date_norm(t.get("date")):
                    continue
                if abs(g["amount"] - a) < 0.005:
                    break
                for f, nm in ((1000.0, "DIV1000"), (100.0, "DIV100")):
                    if abs(g["amount"] / f - a) < 0.005 and S.desc_sim(
                            t.get("description"), g["description_geom"]) > 0.55:
                        d("LUNA_AMOUNT_SCALED__" + nm,
                          "Luna amount is the printed amount divided by "
                          f"{int(f)} (thousands separator read as a decimal point)",
                          {"page": g["page"], "printed": g["amount_printed"],
                           "pdf": g["amount"], "luna": a,
                           "desc": (t.get("description") or "")[:60]})
                        break
                break

        # ---- D3: direction vs the printed C/D marker (SBI's own authority).
        # Matched on (date, amount) and used ONLY for diagnosis, never for scoring.
        bym = defaultdict(list)
        for g in geom:
            if g["marker"] in ("C", "D") and g["amount"] is not None:
                bym[(S.date_norm(g["date_norm"]), round(g["amount"], 2))].append(g["marker"])
        ok = bad = unres = 0
        for t in ltx:
            a = t.get("amount")
            k = (S.date_norm(t.get("date")), round(float(a), 2) if a is not None else None)
            ms = set(bym.get(k) or [])
            if len(ms) != 1:
                unres += 1
                continue
            want = "CREDIT" if ms.pop() == "C" else "DEBIT"
            got = (t.get("direction") or "").upper()
            if got == want:
                ok += 1
            else:
                bad += 1
                d("LUNA_DIRECTION_VS_CD_MARKER",
                  "Luna direction contradicts the printed C/D column",
                  {"date": t.get("date"), "amount": a, "expected": want, "luna": got,
                   "desc": (t.get("description") or "")[:60]})
        st["direction"] = {"agrees": ok, "contradicts": bad, "unresolvable": unres}

        # ---- D4: PAYMENT RECEIVED direction, the prompt's explicit self-conflict.
        # The client prompt says "Payments TO the bank -> DEBIT", but SBI prints
        # 'C' on the PAYMENT RECEIVED row and the incumbent calls it CREDIT.
        for t in ltx:
            if "PAYMENT RECEIVED" in (t.get("description") or "").upper():
                st.setdefault("payment_received", []).append(
                    {"luna": t.get("direction"), "amount": t.get("amount"),
                     "date": t.get("date")})
                if (t.get("direction") or "").upper() == "DEBIT":
                    d("LUNA_PAYMENT_RECEIVED_AS_DEBIT",
                      "PAYMENT RECEIVED marked DEBIT; PDF prints 'C' and the "
                      "incumbent says CREDIT",
                      {"date": t.get("date"), "amount": t.get("amount")})

        # ---- D5: network -- the schema asks for it, the client prompt never mentions it
        for c in (p.get("cards") or []):
            nw = (c.get("cardMeta") or {}).get("network")
            st.setdefault("network", []).append(nw)
            if nw:
                hits = E.find_value_on_page(path, nw)
                if not hits:
                    d("LUNA_NETWORK_HALLUCINATED",
                      "network value appears NOWHERE in the PDF text",
                      {"luna": nw})

        # ---- D6: issuerName consistency (single-issuer corpus -> non-discriminating)
        st["issuer"] = {"luna": S.dig(p, "statementMeta.issuerName"),
                        "incumbent": S.dig(inc, "statementMeta.issuerName")}

        # ---- D7: rawStatementId. SBI DOES print 'STMT No. : H26072450588'.
        rid = S.dig(p, "statementMeta.rawStatementId")
        stmt_hits = E.find_value_on_page(path, "STMT No")
        st["rawStatementId"] = {"luna": rid, "pdf_prints_STMT_No": bool(stmt_hits),
                                "pdf_line": stmt_hits[0]["line"] if stmt_hits else None}
        if stmt_hits and not rid:
            d("LUNA_RAWSTATEMENTID_NULL_BUT_PRINTED",
              "PDF prints a 'STMT No.' label but Luna returned null",
              {"pdf_line": stmt_hits[0]["line"]})

        # ---- D8: closingPoints -- VALIDATING the client prompt's existing SBI rule
        lcp = S.dig(p, "rewards.closingPoints")
        icp = S.dig(inc, "rewards.closingPoints")
        cyc, life = _points_evidence(path)
        st["points"] = {"luna_closing": lcp, "incumbent_closing": icp,
                        "pdf_cycle_strip": cyc, "pdf_lifetime_row": life}
        if lcp is not None and cyc and abs(float(lcp) - cyc[-1]) < 0.01:
            pat["LUNA_CLOSINGPOINTS_IS_CYCLE_CLOSING"] += 1
        elif lcp is not None and life and any(abs(float(lcp) - v) < 0.01 for v in life[1:]):
            d("LUNA_CLOSINGPOINTS_FROM_LIFETIME_OR_YTD",
              "closingPoints equals a 'For this year' / 'From the card issue date' "
              "figure -- the exact trap the client prompt's SBI clause warns about",
              {"luna": lcp, "lifetime_row": life})

        # ---- D9: description fidelity vs the printed narration
        st["desc"] = _desc_audit(ltx, geom)

        # ---- D10: summary label binding, geometrically adjudicated
        ev = E.summary_evidence(path)
        st["summary"] = {}
        for f in ("totalAmountDue", "totalMinimumAmountDue", "totalCreditLimit",
                  "availableCreditLimit"):
            lv = S.dig(p, "statementLevelSummary." + f)
            iv = S.dig(inc, "statementLevelSummary." + f)
            cands = ev.get(f) or []
            pv = cands[0]["value"] if cands else None
            st["summary"][f] = {"luna": lv, "incumbent": iv, "pdf": pv}
            if pv is None:
                continue
            lok = lv is not None and abs(float(lv) - pv) < 0.01
            iok = iv is not None and abs(float(iv) - pv) < 0.01
            others = {k: (ev[k][0]["value"] if ev.get(k) else None) for k in ev}
            if not lok:
                mis = [k for k, v in others.items()
                       if v is not None and lv is not None and abs(float(lv) - v) < 0.01]
                d("LUNA_SUMMARY_MISBIND" if mis else "LUNA_SUMMARY_WRONG",
                  f"{f}: Luna={lv} but the PDF prints {pv}"
                  + (f" (Luna's value is the {mis} figure)" if mis else ""),
                  {"field": f, "luna": lv, "pdf": pv, "matches_labels": mis,
                   "value_rect": cands[0]["value_rect"]})
            if not iok:
                mis = [k for k, v in others.items()
                       if v is not None and iv is not None and abs(float(iv) - v) < 0.01]
                d("CSV_SUMMARY_MISBIND" if mis else "CSV_SUMMARY_WRONG",
                  f"{f}: incumbent={iv} but the PDF prints {pv}"
                  + (f" (incumbent's value is the {mis} figure)" if mis else ""),
                  {"field": f, "incumbent": iv, "pdf": pv, "matches_labels": mis})

        out["statements"][sid] = st

    out["patterns"] = dict(pat.most_common())
    json.dump(out, open(os.path.join(ROOT, "phase2_measured.json"), "w"),
              indent=1, default=str)

    print("=== PATTERN TALLY (10-statement Phase-1 sample, CLIENT prompt) ===")
    for k, v in pat.most_common():
        print(f"  {v:>4}  {k}")
    print("\n=== PER STATEMENT ===")
    for sid, st in out["statements"].items():
        print(f"\n{sid}  {st['outcome']} fr={st['finish_reason']}  "
              f"luna={st['n_luna']} inc={st['n_incumbent']} pdf={st['n_pdf_geom']}  "
              f"band={st['leading_band_rows']} period={st['period']}")
        print(f"    dir={st['direction']}  desc={st['desc']}")
        print(f"    points={st['points']}")
        print(f"    network={st.get('network')} rawId={st['rawStatementId']['luna']!r}"
              f" (printed={st['rawStatementId']['pdf_prints_STMT_No']})")
        for x in st["dropped"]:
            print(f"    DROP[{x['cause']}] p{x['page']} {x['date']} {x['amount']} "
                  f"{x['marker']} {x['desc'][:44]}")
        for x in st["defects"]:
            if not x["kind"].startswith("LUNA_DROPPED"):
                print(f"    ! {x['kind']}: {x['note'][:110]}")


def _points_evidence(path):
    """-> (cycle_strip_numbers, lifetime_row_numbers).

    SBI prints TWO point tables and conflating them is the documented SBI trap:
      * a 4-cell CYCLE strip  Previous Balance | Earned | Redeemed/Expired/Forfeited
        | Closing Balance   -> the CURRENT-cycle numbers (labels are printed AFTER
        the values in text order, so the values are the 4 integers preceding the
        'Previous Balance' label).
      * a SAVINGS AND BENEFITS row with three columns 'For this statement | For this
        year | From the card issue date'  -> per-cycle, YTD and LIFETIME.
    """
    doc = fitz.open(path)
    txt = "".join(doc[i].get_text() for i in range(doc.page_count))
    doc.close()
    cyc = None
    m = re.search(r"((?:-?[\d,]+\s*\n){4})Previous Balance\s*\n\s*Earned", txt)
    if m:
        try:
            cyc = [float(x.replace(",", "")) for x in m.group(1).split()]
        except ValueError:
            cyc = None
    life = None
    m = re.search(r"From the card issue date(.{0,400}?)(?:Petrol Surcharge Waiver|"
                  r"Reward Points|NeuCoins|Cash Back)", txt, re.S)
    if m:
        nums = [float(x.replace(",", "")) for x in
                re.findall(r"^\s*(-?[\d,]+(?:\.\d{2})?)\s*$", m.group(1), re.M)]
        if len(nums) >= 3:
            life = nums[-3:]
    return cyc, life


def _desc_audit(ltx, geom):
    """SBI pads narration into fixed-width columns ('FLIPKART INTERNET PVT      IN').
    Does the model preserve the internal run of spaces and the trailing country
    column, or silently normalise them?"""
    n = len(ltx)
    dbl = sum(1 for t in ltx if "  " in str(t.get("description") or ""))
    tin = sum(1 for t in ltx if str(t.get("description") or "").rstrip().endswith(" IN"))
    gdbl = sum(1 for g in geom if "  " in g["description_geom"])
    gtin = sum(1 for g in geom if g["description_geom"].rstrip().endswith(" IN"))
    exact = 0
    for t in ltx:
        dsc = (t.get("description") or "").strip()
        if any(g["description_geom"].strip() == dsc for g in geom):
            exact += 1
    return {"n": n, "exact_match_printed": exact,
            "luna_double_space": dbl, "pdf_double_space": gdbl,
            "luna_ends_country": tin, "pdf_ends_country": gtin}


if __name__ == "__main__":
    main()
