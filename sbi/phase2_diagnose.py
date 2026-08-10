#!/usr/bin/env python3
"""Phase 2: diagnose SBI-specific failure PATTERNS in the Phase-1 CLIENT-prompt run (SYSTEM PROMPT.txt).

Every claimed defect is adjudicated against the PDF itself with PyMuPDF
page/coordinate evidence, so a rule added to SBI_PROMPT.txt is justified by an
observed, located defect and not by a hunch.
"""
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sbi_pdf_evidence as E
import sbi_lib as L
import score_lib_sbi as S

ROOT = os.path.dirname(os.path.abspath(__file__))
SUMMARY_FIELDS = ["totalAmountDue", "totalMinimumAmountDue",
                  "totalCreditLimit", "availableCreditLimit"]


def main():
    sample = json.load(open(os.path.join(ROOT, "phase1_sample.json")))
    ids = sample["sample_ids"]
    corpus = {s: p for s, f, p in L.discover_pdfs()}
    csvref, _ = S.load_csv_incumbent()
    luna = S.load_arm(os.path.join(ROOT, "run_p1_client"))

    report = {"n": len(ids), "statements": {}, "patterns": Counter()}
    for sid in ids:
        rec = luna.get(sid)
        if rec is None:
            report["statements"][sid] = {"outcome": "NOT_RUN"}
            continue
        pred = S.parsed_of(rec)
        inc = csvref.get(sid, {})
        pdf = corpus[sid]
        ev = E.summary_evidence(pdf)
        geom = E.txn_rows(pdf)
        rw = E.reward_evidence(pdf)

        st = {"outcome": rec.get("outcome"), "finish_reason": rec.get("finish_reason"),
              "usage": rec.get("usage_raw"), "n_txn_luna": rec.get("n_transactions"),
              "n_txn_incumbent": len(inc.get("transactions") or []),
              "n_txn_pdf_geom": len(geom), "issues": []}

        def issue(kind, field, luna_v, inc_v, pdf_v, note):
            st["issues"].append({"kind": kind, "field": field, "luna": luna_v,
                                 "incumbent": inc_v, "pdf_evidence": pdf_v, "note": note})
            report["patterns"][kind] += 1

        # ---- statementLevelSummary vs geometric PDF evidence
        for f in SUMMARY_FIELDS:
            lv = S.dig(pred, "statementLevelSummary." + f)
            iv = S.dig(inc, "statementLevelSummary." + f)
            cands = ev.get(f) or []
            pv = cands[0]["value"] if cands else None
            pvr = cands[0] if cands else None
            lok = lv is not None and pv is not None and abs(float(lv) - pv) < 0.01
            iok = iv is not None and pv is not None and abs(float(iv) - pv) < 0.01
            if pv is None:
                continue
            if not lok and not iok:
                issue("SUMMARY_BOTH_DISAGREE_PDF", f, lv, iv, pvr, "neither matches the "
                      "geometrically-bound printed value")
            elif not lok:
                # is Luna's value one of the OTHER summary labels? (mis-binding)
                other = {k: (ev[k][0]["value"] if ev.get(k) else None) for k in ev}
                mis = [k for k, v in other.items()
                       if v is not None and lv is not None and abs(float(lv) - v) < 0.01]
                issue("LUNA_SUMMARY_MISBIND" if mis else "LUNA_SUMMARY_WRONG", f, lv, iv,
                      pvr, f"luna value matches label(s) {mis}" if mis else "no label match")
            elif not iok:
                other = {k: (ev[k][0]["value"] if ev.get(k) else None) for k in ev}
                mis = [k for k, v in other.items()
                       if v is not None and iv is not None and abs(float(iv) - v) < 0.01]
                issue("CSV_SUMMARY_MISBIND" if mis else "CSV_SUMMARY_WRONG", f, lv, iv,
                      pvr, f"incumbent value matches label(s) {mis}" if mis else "no label match")

        # ---- rewards.closingPoints: the documented SBI trap
        lcp = S.dig(pred, "rewards.closingPoints")
        icp = S.dig(inc, "rewards.closingPoints")
        lifet = []
        for sec in rw["savings_section"]:
            for n in sec["numbers"]:
                lifet.append(n)
        # numbers printed under 'From the card issue date' / 'For this year' are the
        # lifetime / YTD figures the brief warns closingPoints must NOT come from.
        life_vals = set()
        for sec in rw["savings_section"]:
            labs = sec["labels"]
            for key in ("From the card issue date", "For this year"):
                for lb in labs.get(key, []):
                    for n in sec["numbers"]:
                        if abs(n["x"] - lb["x"]) < 70 and n["y"] > lb["y"] - 5:
                            life_vals.add(n["v"])
        if lcp is not None and lcp in life_vals:
            issue("LUNA_CLOSINGPOINTS_FROM_LIFETIME", "rewards.closingPoints", lcp, icp,
                  sorted(life_vals)[:8], "luna value equals a lifetime/YTD figure")
        if icp is not None and icp in life_vals:
            issue("CSV_CLOSINGPOINTS_FROM_LIFETIME", "rewards.closingPoints", lcp, icp,
                  sorted(life_vals)[:8], "incumbent value equals a lifetime/YTD figure")
        if lcp is not None and icp is not None and abs(float(lcp) - float(icp)) > 0.01:
            issue("CLOSINGPOINTS_DISAGREE", "rewards.closingPoints", lcp, icp,
                  sorted(life_vals)[:8], "luna and incumbent disagree")

        # ---- transactions: completeness against the GEOMETRIC row count
        ltx = pred.get("transactions") or []
        itx = inc.get("transactions") or []
        st["txn_recall_vs_geom"] = (len(ltx) / len(geom)) if geom else None
        if geom and len(ltx) < len(geom) - 1:
            missing = _missing_rows(ltx, geom)
            issue("LUNA_TXN_INCOMPLETE", "transactions", len(ltx), len(itx),
                  {"pdf_rows": len(geom), "examples": missing[:6]},
                  "luna emitted fewer rows than the PDF geometrically contains")
        if geom and len(ltx) > len(geom) + 1:
            issue("LUNA_TXN_EXTRA", "transactions", len(ltx), len(itx),
                  {"pdf_rows": len(geom)}, "luna emitted MORE rows than the PDF contains")

        # ---- direction vs the printed C/D marker (SBI's own authority)
        st["direction_vs_marker"] = _direction_audit(ltx, geom)
        if st["direction_vs_marker"]["luna_contradicts_marker"]:
            issue("LUNA_DIRECTION_VS_CD_MARKER", "transactions[].direction",
                  st["direction_vs_marker"]["luna_contradicts_marker"], None,
                  st["direction_vs_marker"]["examples"][:5],
                  "luna direction contradicts the printed C/D column")

        # ---- description fidelity: SBI pads narration with column whitespace
        st["desc"] = _desc_audit(ltx, geom)

        # ---- network hallucination
        for i, c in enumerate(pred.get("cards") or []):
            nw = (c.get("cardMeta") or {}).get("network")
            if nw:
                hits = E.find_value_on_page(pdf, nw)
                # SBI prints all four network names in boilerplate disclaimer text
                real = [h for h in hits if "Network" not in h["line"]
                        and "Guidelines" not in h["line"]]
                if not real:
                    issue("LUNA_NETWORK_HALLUCINATED", "cards[].cardMeta.network", nw,
                          ((inc.get("cards") or [{}])[0].get("cardMeta") or {}).get("network"),
                          {"literal_hits": hits[:3]},
                          "network not printed on the statement except in boilerplate")

        # ---- lastFourDigit mask depth
        lp = ((pred.get("cards") or [{}])[0].get("cardMeta") or {}).get("lastFourDigit")
        ip = ((inc.get("cards") or [{}])[0].get("cardMeta") or {}).get("lastFourDigit")
        st["last_four"] = {"luna": lp, "incumbent": ip}

        # ---- issuerName
        st["issuer"] = {"luna": S.dig(pred, "statementMeta.issuerName"),
                        "incumbent": S.dig(inc, "statementMeta.issuerName")}

        # ---- statement period
        st["period"] = {"luna": [S.dig(pred, "statementMeta.statementPeriodStart"),
                                 S.dig(pred, "statementMeta.statementPeriodEnd")],
                        "incumbent": [S.dig(inc, "statementMeta.statementPeriodStart"),
                                      S.dig(inc, "statementMeta.statementPeriodEnd")]}
        st["rawStatementId"] = {"luna": S.dig(pred, "statementMeta.rawStatementId"),
                                "incumbent": S.dig(inc, "statementMeta.rawStatementId"),
                                "pdf_has_STMT_No": bool(E.find_value_on_page(pdf, "STMT No"))}
        report["statements"][sid] = st

    report["patterns"] = dict(report["patterns"])
    with open(os.path.join(ROOT, "phase2_diagnosis.json"), "w") as f:
        json.dump(report, f, indent=1, default=str)
    print(json.dumps(report["patterns"], indent=1))
    for sid, st in report["statements"].items():
        print(f"\n{sid}: {st.get('outcome')} luna_txn={st.get('n_txn_luna')} "
              f"inc_txn={st.get('n_txn_incumbent')} pdf_geom={st.get('n_txn_pdf_geom')} "
              f"issuer={st.get('issuer')} l4={st.get('last_four')}")
        print(f"   desc={st.get('desc')}")
        print(f"   dir={st.get('direction_vs_marker')}")
        for i in st.get("issues", []):
            print(f"   ! {i['kind']} {i['field']} luna={i['luna']!r} inc={i['incumbent']!r} "
                  f"note={i['note']}")


def _missing_rows(ltx, geom):
    """PDF rows with no plausible Luna counterpart (date+amount, not used for scoring
    -- this is diagnosis of COMPLETENESS, a different question from field accuracy)."""
    have = set()
    for t in ltx:
        a = S.num(t.get("amount"))
        have.add((S.date_norm(t.get("date")), float(a) if a is not None else None))
    out = []
    for g in geom:
        d = S.date_norm(g["date_norm"])
        if (d, g["amount"]) not in have:
            out.append({"page": g["page"], "date": g["date"], "amount": g["amount"],
                        "desc": g["description_geom"][:60], "marker": g["marker"]})
    return out


def _direction_audit(ltx, geom):
    """Compare Luna's direction to the C/D marker printed in the PDF's own right-hand
    column, matched on (date, amount) -- unique enough for this purpose and used only
    for DIAGNOSIS, never for the scored numbers."""
    bym = {}
    for g in geom:
        if g["marker"] in ("C", "D") and g["amount"] is not None:
            bym.setdefault((S.date_norm(g["date_norm"]), g["amount"]), []).append(g["marker"])
    ok = bad = unknown = 0
    ex = []
    for t in ltx:
        a = S.num(t.get("amount"))
        k = (S.date_norm(t.get("date")), float(a) if a is not None else None)
        ms = bym.get(k)
        if not ms or len(set(ms)) != 1:
            unknown += 1
            continue
        want = "CREDIT" if ms[0] == "C" else "DEBIT"
        got = (t.get("direction") or "").upper()
        if got == want:
            ok += 1
        else:
            bad += 1
            ex.append({"date": t.get("date"), "amount": t.get("amount"),
                       "desc": (t.get("description") or "")[:50],
                       "printed_marker": ms[0], "expected": want, "luna": got})
    return {"agrees_with_marker": ok, "luna_contradicts_marker": bad,
            "unresolvable": unknown, "examples": ex}


def _desc_audit(ltx, geom):
    """SBI pads narration into fixed-width columns ('UBER INDIA SYSTE  PVT L  NOIDA
    IN'). Does the model keep the internal run of spaces, collapse it, or drop the
    trailing country/city columns?"""
    multi = sum(1 for t in ltx if "  " in str(t.get("description") or ""))
    trail_in = sum(1 for t in ltx if str(t.get("description") or "").rstrip().endswith(" IN"))
    geom_trail = sum(1 for g in geom if g["description_geom"].rstrip().endswith(" IN"))
    return {"n": len(ltx), "with_double_space": multi,
            "ending_in_country_code": trail_in, "pdf_rows_ending_in_country": geom_trail}


if __name__ == "__main__":
    main()
