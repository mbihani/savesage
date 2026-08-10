#!/usr/bin/env python3
"""Adjudicate every Luna-vs-incumbent disagreement against the PDF itself.

The CSV is NOT ground truth -- it is the incumbent Gemini parser's output
(detectionSource=GEMINI on 315/315 rows). So a Luna/CSV difference is a
DISAGREEMENT, and which side is wrong can only be settled by the physical document.
Each disagreement is classified LUNA_WRONG / CSV_WRONG / BOTH_WRONG /
AMBIGUOUS_IN_PDF, with the PyMuPDF page/coordinate evidence that decided it, and a
CORRECTED score is computed from the verdicts.

Statement-level money fields are adjudicated by GEOMETRIC label binding (the SBI
ACCOUNT SUMMARY grid emits labels and values in different orders, so reading order
is not evidence). Transaction rows are adjudicated against the geometrically
reconstructed printed row, including SBI's right-hand C/D direction column.
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sbi_lib as L          # noqa: E402
import sbi_pdf_evidence as E  # noqa: E402
import score_lib_sbi as S    # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))

MONEY_FIELDS = {
    "statementLevelSummary.totalAmountDue": "totalAmountDue",
    "statementLevelSummary.totalMinimumAmountDue": "totalMinimumAmountDue",
    "statementLevelSummary.totalCreditLimit": "totalCreditLimit",
    "statementLevelSummary.availableCreditLimit": "availableCreditLimit",
}
# MEASURED, not guessed. Every occurrence of a network word in this corpus was
# enumerated: 296 of 300 PDFs never print one on page 1 at all, the other 4 print it
# only in T&C prose, and 0 of 300 print one anywhere in the page-1 header band (y<420)
# where the card number and product name live. The recurring boilerplate lines are:
#   "(VISA, MasterCard, Rupay, Amex) Guidelines. You will receive"     (dispute policy)
#   "made by customer through any instant channel (NEFT, Visa Money Transfer, ..."
#   "VISA Credit Card Pay" / "Use VISA Credit Card Pay to pay your SBI Credit Card bill"
#   "Mastercard MoneySend" / "...platform that supports the Mastercard"
#   "Actual cost (subject to a minimum of $175 for VISA and $ 148" / "for Mastercard)"
#   "Emergency Card Replacement (When Abroad)"
# So on SBI, ANY non-null `network` is a fabrication regardless of where the literal
# string appears. The y<420 header test is the real discriminator; the phrase list
# below is kept as a second, human-readable filter.
NETWORK_BOILERPLATE = ("Guidelines", "Network", "Credit Card Pay", "MoneySend",
                       "Money Transfer", "minimum of", "Mastercard)", "Amex)",
                       "Emergency Card Replacement", "Abroad", "ongoing offers",
                       "instant channel", "third-party payment app")
# Header band on page 1: above this y the ACCOUNT SUMMARY grid, card number and
# product name are printed. A network label identifying THIS card would have to be here.
NETWORK_HEADER_Y_MAX = 420.0


def adj_money(field, lv, iv, ev):
    """Geometric label binding decides it. -> (verdict, evidence)."""
    key = MONEY_FIELDS[field]
    cands = ev.get(key) or []
    if not cands:
        return "AMBIGUOUS_IN_PDF", {"reason": "label not bound geometrically on page 1"}
    pv = cands[0]["value"]
    others = {k: (ev[k][0]["value"] if ev.get(k) else None) for k in ev}
    lok = lv is not None and abs(float(lv) - pv) < 0.01
    iok = iv is not None and abs(float(iv) - pv) < 0.01
    e = {"pdf_value": pv, "value_rect": cands[0]["value_rect"],
         "label_rect": cands[0]["label_rect"], "page": cands[0]["page"],
         "runner_up": cands[1]["value"] if len(cands) > 1 else None,
         "luna": lv, "incumbent": iv}
    if lok and iok:
        return "AGREE", e
    if lok:
        e["incumbent_matches_label"] = [k for k, v in others.items()
                                        if v is not None and iv is not None
                                        and abs(float(iv) - v) < 0.01]
        return "CSV_WRONG", e
    if iok:
        e["luna_matches_label"] = [k for k, v in others.items()
                                   if v is not None and lv is not None
                                   and abs(float(lv) - v) < 0.01]
        return "LUNA_WRONG", e
    return "BOTH_WRONG", e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--luna", default=os.path.join(ROOT, "run_luna_refined"))
    ap.add_argument("--out", default=os.path.join(ROOT, "adjudication.json"))
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()

    corpus = {s: p for s, f, p in L.discover_pdfs()}
    csvref, _ = S.load_csv_incumbent()
    luna = S.load_arm(a.luna)
    ids = [s for s in sorted(corpus) if s in csvref and s in luna]
    if a.limit:
        ids = ids[:a.limit]

    tally = Counter()
    per_field = defaultdict(Counter)
    items = []

    for sid in ids:
        rec = luna[sid]
        p = S.parsed_of(rec)
        inc = csvref[sid]
        path = corpus[sid]
        ev = None
        geom = None

        # ---------- statement-level money
        for field in MONEY_FIELDS:
            lv, iv = S.dig(p, field), S.dig(inc, field)
            v, _k = S.cmp_scalar(field, lv, iv)
            if v == "correct" or v == "both_null":
                continue
            if ev is None:
                ev = E.summary_evidence(path)
            verdict, e = adj_money(field, lv, iv, ev)
            tally[verdict] += 1
            per_field[field][verdict] += 1
            items.append({"statement_id": sid, "field": field, "luna": lv,
                          "incumbent": iv, "verdict": verdict, "evidence": e})

        # ---------- dates
        for field in ("statementMeta.statementDate", "statementMeta.dueDate"):
            lv, iv = S.dig(p, field), S.dig(inc, field)
            v, _k = S.cmp_scalar(field, lv, iv)
            if v in ("correct", "both_null"):
                continue
            label = ("Statement Date" if field.endswith("statementDate")
                     else "Payment Due Date")
            hits = E.find_value_on_page(path, label)
            # which of the two values is printed anywhere in the PDF?
            lh = E.find_value_on_page(path, _dmy_to_sbi(lv)) if lv else []
            ih = E.find_value_on_page(path, _dmy_to_sbi(iv)) if iv else []
            verdict = ("AGREE" if lh and ih else "LUNA_WRONG" if ih and not lh
                       else "CSV_WRONG" if lh and not ih else "AMBIGUOUS_IN_PDF")
            tally[verdict] += 1
            per_field[field][verdict] += 1
            items.append({"statement_id": sid, "field": field, "luna": lv,
                          "incumbent": iv, "verdict": verdict,
                          "evidence": {"label_hits": hits[:2],
                                       "luna_printed": bool(lh),
                                       "incumbent_printed": bool(ih)}})

        # ---------- network: is the claimed value real, or boilerplate?
        lnw = ((p.get("cards") or [{}])[0].get("cardMeta") or {}).get("network") \
            if p.get("cards") else None
        inw = ((inc.get("cards") or [{}])[0].get("cardMeta") or {}).get("network") \
            if inc.get("cards") else None
        if S.text(lnw) != S.text(inw):
            def real(nw):
                """Non-boilerplate evidence that THIS card carries network `nw`.
                Requires a page-1 header-band hit (where the card number and product
                name are printed) AND a line that is not known boilerplate."""
                if not nw:
                    return False
                hits = E.find_value_on_page(path, nw)
                return [h for h in hits
                        if h["page"] == 1
                        and h["rect"][1] <= NETWORK_HEADER_Y_MAX
                        and not any(b in h["line"] for b in NETWORK_BOILERPLATE)]
            lr, ir = real(lnw), real(inw)
            verdict = ("LUNA_WRONG" if (lnw and not lr) and not (inw and not ir)
                       else "CSV_WRONG" if (inw and not ir) and not (lnw and not lr)
                       else "BOTH_WRONG" if (lnw and not lr) and (inw and not ir)
                       else "AMBIGUOUS_IN_PDF")
            tally[verdict] += 1
            per_field["cards[].cardMeta.network"][verdict] += 1
            items.append({"statement_id": sid, "field": "cards[].cardMeta.network",
                          "luna": lnw, "incumbent": inw, "verdict": verdict,
                          "evidence": {"luna_nonboilerplate_hits": (lr or [])[:2],
                                       "incumbent_nonboilerplate_hits": (ir or [])[:2]}})

        # ---------- transaction COUNT disagreement -> who matches the printed rows?
        ltx = p.get("transactions") or []
        itx = inc.get("transactions") or []
        if len(ltx) != len(itx):
            geom = E.txn_rows(path)
            ng = len(geom)
            dl, di = abs(len(ltx) - ng), abs(len(itx) - ng)
            verdict = ("LUNA_WRONG" if dl > di else "CSV_WRONG" if di > dl
                       else "AMBIGUOUS_IN_PDF")
            tally["TXNCOUNT_" + verdict] += 1
            per_field["transactions[].count"][verdict] += 1
            items.append({"statement_id": sid, "field": "transactions[].count",
                          "luna": len(ltx), "incumbent": len(itx), "verdict": verdict,
                          "evidence": {"pdf_geom_rows": ng,
                                       "luna_delta": len(ltx) - ng,
                                       "incumbent_delta": len(itx) - ng}})

        # ---------- transaction DIRECTION disagreement -> the printed C/D column rules
        pairs, _up, _ur = S.match_txns_by_description(ltx, itx)
        dis = [m for m in pairs
               if S.direction(m["pred"]) != S.direction(m["ref"])]
        if dis:
            if geom is None:
                geom = E.txn_rows(path)
            bym = defaultdict(set)
            for g in geom:
                if g["marker"] in ("C", "D", "T") and g["amount"] is not None:
                    bym[(S.date_norm(g["date_norm"]), round(g["amount"], 2))].add(g["marker"])
            for m in dis:
                amt = m["ref"].get("amount")
                k = (S.date_norm(m["ref"].get("date")),
                     round(float(amt), 2) if amt is not None else None)
                ms = bym.get(k) or set()
                if len(ms) != 1:
                    verdict = "AMBIGUOUS_IN_PDF"
                    want = None
                else:
                    # NOTE: S.direction() is the canonical normaliser and returns
                    # LOWERCASE ('credit'/'debit'), so `want` must be lowercased before
                    # comparison. Comparing against 'CREDIT' silently never matches and
                    # turns every direction disagreement into a bogus BOTH_WRONG.
                    want = "debit" if ms == {"D"} else "credit"
                    lw = S.direction(m["pred"]) == want
                    iw = S.direction(m["ref"]) == want
                    verdict = ("CSV_WRONG" if lw and not iw
                               else "LUNA_WRONG" if iw and not lw else "BOTH_WRONG")
                tally[verdict] += 1
                per_field["transactions[].direction"][verdict] += 1
                items.append({"statement_id": sid, "field": "transactions[].direction",
                              "luna": m["pred"].get("direction"),
                              "incumbent": m["ref"].get("direction"),
                              "verdict": verdict,
                              "evidence": {"printed_marker": sorted(ms) or None,
                                           "pdf_says": (want or "").upper() or None,
                                           "date": m["ref"].get("date"),
                                           "amount": amt,
                                           "desc": (m["ref"].get("description") or "")[:70]}})

        # ---------- transaction AMOUNT disagreement inside a matched pair
        for m in pairs:
            la, ia = m["pred"].get("amount"), m["ref"].get("amount")
            if la is None or ia is None or abs(float(la) - float(ia)) < 0.01:
                continue
            if geom is None:
                geom = E.txn_rows(path)
            cand = [g for g in geom
                    if S.desc_sim(m["ref"].get("description"), g["description_geom"]) > 0.6]
            # A pair of near-identical narrations that differ ONLY in a trailing
            # foreign-currency amount (SBI prints 'PAYOO-HIGHLANDS ... 39,000.00 VND'
            # and '... 1,68,000.00 VND') is a MATCHER ambiguity, not an extraction
            # defect: the description-only matcher may pair them either way round.
            # If BOTH disputed values are printed on candidate rows, the disagreement
            # is a row-assignment artifact and must NOT be charged to either side.
            printed = {round(g["amount"], 2) for g in cand if g["amount"] is not None}
            # SIGN IS A CONTRACT MATTER, NOT A PRINTING MATTER. Both prompts state
            # "transactions->amount is ALWAYS a positive number. Never negate the amount
            # field regardless of the transaction direction" -- sign lives in `direction`.
            # SBI prints magnitudes only (the C/D column carries the sign), so a negated
            # amount whose MAGNITUDE is printed correctly is a contract violation, not a
            # misread glyph. Judged against the contract and labelled as such, so the
            # verdict is not confused with "this number is not in the PDF".
            if abs(float(la)) == abs(float(ia)) and round(abs(float(la)), 2) in printed:
                offender = "CSV_WRONG" if float(ia) < 0 else (
                    "LUNA_WRONG" if float(la) < 0 else None)
                if offender:
                    tally[offender] += 1
                    per_field["transactions[].amount"][offender] += 1
                    items.append({
                        "statement_id": sid, "field": "transactions[].amount",
                        "luna": la, "incumbent": ia, "verdict": offender,
                        "basis": "CONTRACT_amount_must_be_positive",
                        "evidence": {"printed_magnitude": round(abs(float(la)), 2),
                                     "note": "magnitude printed correctly; sign negated "
                                             "in breach of 'amount is ALWAYS positive'",
                                     "desc": (m["ref"].get("description") or "")[:70]}})
                    continue
            la_p = round(float(la), 2) in printed
            ia_p = round(float(ia), 2) in printed
            if la_p and ia_p:
                verdict = "AMBIGUOUS_IN_PDF"
                pv = None
            else:
                pv = None
                for g in cand:
                    if g["amount"] is None:
                        continue
                    if (abs(g["amount"] - float(la)) < 0.01
                            or abs(g["amount"] - float(ia)) < 0.01):
                        pv = g
                        break
                if pv is None:
                    verdict = "AMBIGUOUS_IN_PDF"
                else:
                    lok = abs(pv["amount"] - float(la)) < 0.01
                    iok = abs(pv["amount"] - float(ia)) < 0.01
                    verdict = ("CSV_WRONG" if lok and not iok
                               else "LUNA_WRONG" if iok and not lok else "BOTH_WRONG")
            tally[verdict] += 1
            per_field["transactions[].amount"][verdict] += 1
            items.append({"statement_id": sid, "field": "transactions[].amount",
                          "luna": la, "incumbent": ia, "verdict": verdict,
                          "evidence": {"pdf_printed": pv["amount_printed"] if pv else None,
                                       "page": pv["page"] if pv else None,
                                       "desc": (m["ref"].get("description") or "")[:70]}})

    out = {"luna_arm": a.luna, "n_statements": len(ids),
           "tally": dict(tally.most_common()),
           "per_field": {k: dict(v) for k, v in per_field.items()},
           "items": items}
    json.dump(out, open(a.out, "w"), ensure_ascii=False, indent=1, default=str)

    print(f"adjudicated {len(ids)} statements, {len(items)} disagreements")
    for k, v in tally.most_common():
        print(f"  {v:>5}  {k}")
    print("\nper field:")
    for f, c in sorted(per_field.items()):
        print(f"  {f:<44} " + "  ".join(f"{k}={v}" for k, v in c.most_common()))
    print("wrote", a.out)


def _dmy_to_sbi(v):
    """'22/07/2026' -> '22 Jul 2026', the form SBI actually prints."""
    s = S.date_norm(v)
    if not s or len(str(s).split("/")) != 3:
        return str(v)
    d, m, y = str(s).split("/")
    mons = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
            "Nov", "Dec"]
    try:
        return f"{d} {mons[int(m) - 1]} {y}"
    except (ValueError, IndexError):
        return str(v)


if __name__ == "__main__":
    main()
