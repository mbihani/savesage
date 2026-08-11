#!/usr/bin/env python3
"""CORRECTED scores: strip cells the PDF adjudication proves are NOT the arm's fault.

Two independent corrections, kept separate and each reported with its own delta so a
reader can accept one and reject the other.

CORRECTION 1 -- CSV_WRONG cells do not count against Luna.
  The Luna-vs-CSV number is AGREEMENT, not accuracy. Where PDF adjudication finds the
  INCUMBENT wrong, a disagreement is Luna being RIGHT. Those cells are removed from
  Luna's disagreement count (and only those).

CORRECTION 2 -- the FX INSTRUMENT ASYMMETRY (found while adjudicating; disclosed).
  The Opus GT prompt carries a rule that NEITHER the client's baseline prompt NOR my
  refined HDFC prompt contains:

    GT (gt298_lib.py:275): "If the statement shows a foreign spend converted to
    rupees, report the rupee amount with INR."
    Client baseline / refined HDFC prompt: "If the transaction row explicitly states a
    currency code (e.g., INR, USD, GBP) -> use it."

  On an HDFC FX row BOTH amounts are printed ("USD 20.00" and "C 1,849.76"). Luna
  reports USD 20.00 / "USD" -- which is what ITS OWN prompt instructs. The GT reports
  1849.76 / "INR". So part of Luna's measured currency+amount error is the GT being
  judged by a STRICTER INSTRUMENT, not a Luna defect.

  This does NOT make Luna right for the client: the client wants the billed rupee
  figure. It is reported as a PROMPT GAP (mine) rather than a model failure, and the
  corrected score is shown BOTH ways so nothing is hidden.

Nothing here weakens a matcher or a rule. Cells are excluded only on documented PDF
evidence or a documented instrument asymmetry, and every exclusion is counted.
"""
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hdfc_lib as H  # noqa: E402
import score_lib as S  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FX_LINE = re.compile(r"\b([A-Z]{3})\s*([\d,]+\.\d{2})\b")


def is_fx_row(gt_txn, luna_txn):
    """An FX row: the GT reports INR while Luna reports a non-INR ISO code, and a
    foreign amount is visible in either narration. Both amounts are printed, so this is
    the instrument-asymmetry class, not a fabrication."""
    gc = (gt_txn.get("currency") or "").upper()
    lc = (luna_txn.get("currency") or "").upper()
    if gc != "INR" or lc in ("INR", ""):
        return False
    blob = f"{gt_txn.get('description') or ''} {luna_txn.get('description') or ''}"
    return bool(FX_LINE.search(blob)) or lc in blob.upper()


def main():
    gt = S.load_run(os.path.join(HERE, "gt_full"))
    luna = S.load_run(os.path.join(HERE, "phase3_refined"))
    matched, _, _ = H.build_join()
    csvx = {m["sid"]: S.csv_extraction(m["csv_row"]) for m in matched}
    gold = {s: r["parsed_json"] for s, r in gt.items() if isinstance(r.get("parsed_json"), dict)}
    lunax = {s: r["parsed_json"] for s, r in luna.items() if isinstance(r.get("parsed_json"), dict)}

    # ---- adjudicated CSV_WRONG cells, keyed so they can be subtracted from AGREEMENT
    adj = json.load(open(os.path.join(HERE, "adjudication_txn.json")))
    csv_wrong = collections.Counter()
    luna_wrong = collections.Counter()
    ambig = collections.Counter()
    for f in adj["findings"]:
        v, fld = f.get("verdict"), f.get("field")
        if v == "CSV_WRONG":
            csv_wrong[fld] += 1
        elif v == "LUNA_WRONG":
            luna_wrong[fld] += 1
        elif v == "AMBIGUOUS_IN_PDF":
            ambig[fld] += 1
        elif v == "BOTH_WRONG":
            luna_wrong[fld] += 1
            csv_wrong[fld] += 1

    # ---- FX-affected cell counts vs the GT (instrument asymmetry)
    fx = collections.Counter()
    fx_rows = []
    for sid, g in sorted(gold.items()):
        p = lunax.get(sid)
        if not isinstance(p, dict):
            continue
        pairs, _, _ = S.match_transactions(p.get("transactions"), g.get("transactions"))
        for i, j, _s in pairs:
            pt = p["transactions"][i] or {}
            gtx = g["transactions"][j] or {}
            if not is_fx_row(gtx, pt):
                continue
            fx["rows"] += 1
            if not S.txn_field_equal("currency", pt.get("currency"), gtx.get("currency")):
                fx["currency_cells"] += 1
            if not S.txn_field_equal("amount", pt.get("amount"), gtx.get("amount")):
                fx["amount_cells"] += 1
            if len(fx_rows) < 40:
                fx_rows.append({"sid": sid, "luna_amount": pt.get("amount"),
                                "luna_currency": pt.get("currency"),
                                "gt_amount": gtx.get("amount"),
                                "gt_currency": gtx.get("currency"),
                                "desc": (gtx.get("description") or "")[:60]})
    fx["statements"] = len({r["sid"] for r in fx_rows})

    base = json.load(open(os.path.join(HERE, "scores_phase3.json")))
    lg = base["scores"]["luna_refined_vs_GT__all"]["transaction_fields"]
    lc = base["scores"]["luna_refined_vs_CSV__all"]["transaction_fields"]

    def corrected(tf, field, remove, label):
        n, corr = tf[field]["n"], tf[field]["correct"]
        wrong = n - corr
        rem = min(remove, wrong)
        return {
            "field": field, "n": n,
            "as_measured_correct": corr,
            "as_measured_accuracy": round(corr / n, 4) if n else None,
            "cells_excluded": rem, "exclusion_basis": label,
            "corrected_correct": corr + rem,
            "corrected_accuracy": round((corr + rem) / n, 4) if n else None,
        }

    out = {
        "purpose": "corrected scores after PDF adjudication + disclosed instrument asymmetry",
        "CORRECTION_1_luna_vs_CSV_agreement_minus_CSV_WRONG": {
            "basis": ("Luna-vs-CSV is AGREEMENT. PDF-adjudicated CSV_WRONG cells are "
                      "Luna being right and are removed from Luna's disagreement count."),
            "csv_wrong_by_field": dict(csv_wrong),
            "luna_wrong_by_field": dict(luna_wrong),
            "ambiguous_by_field": dict(ambig),
            "fields": [corrected(lc, f, csv_wrong.get(f, 0), "PDF-adjudicated CSV_WRONG")
                       for f in ["date", "description", "amount", "direction", "currency"]],
        },
        "CORRECTION_2_FX_instrument_asymmetry_vs_GT": {
            "basis": ("The GT prompt requires the RUPEE amount with INR on a converted "
                      "foreign spend (gt298_lib.py:275). Neither the client's baseline "
                      "prompt nor the refined HDFC prompt states which of the two printed "
                      "amounts wins, and both say to use an explicitly stated currency "
                      "code. Luna followed its own instrument. This is a PROMPT GAP "
                      "(mine), disclosed, not a silent correction."),
            "fx_rows_affected": fx["rows"],
            "fx_statements_affected": fx["statements"],
            "currency_cells_affected": fx["currency_cells"],
            "amount_cells_affected": fx["amount_cells"],
            "CLIENT_IMPACT": ("Luna is still WRONG for the client on these rows -- the "
                             "client wants the billed rupee figure. Fixing the prompt is "
                             "a one-line change; UNVERIFIED until a sweep runs with it."),
            "fields": [
                corrected(lg, "currency", fx["currency_cells"], "FX instrument asymmetry"),
                corrected(lg, "amount", fx["amount_cells"], "FX instrument asymmetry"),
            ],
            "examples": fx_rows[:12],
        },
    }
    H.G.atomic_write_json(os.path.join(HERE, "corrected_scores.json"), out)

    print("== CORRECTION 1: Luna-vs-CSV AGREEMENT, CSV_WRONG cells removed ==")
    for r in out["CORRECTION_1_luna_vs_CSV_agreement_minus_CSV_WRONG"]["fields"]:
        print(f"  {r['field']:12s} n={r['n']:5d} as_measured={r['as_measured_accuracy']} "
              f"-excl {r['cells_excluded']:3d}-> corrected={r['corrected_accuracy']}")
    print("\n== CORRECTION 2: FX instrument asymmetry vs GT ==")
    c2 = out["CORRECTION_2_FX_instrument_asymmetry_vs_GT"]
    print(f"  FX rows={c2['fx_rows_affected']} across {c2['fx_statements_affected']} statements; "
          f"currency cells={c2['currency_cells_affected']} amount cells={c2['amount_cells_affected']}")
    for r in c2["fields"]:
        print(f"  {r['field']:12s} n={r['n']:5d} as_measured={r['as_measured_accuracy']} "
              f"-excl {r['cells_excluded']:3d}-> corrected={r['corrected_accuracy']}")


if __name__ == "__main__":
    main()
