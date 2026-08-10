#!/usr/bin/env python3
"""Reconcile extracted transactions against the statement's OWN printed arithmetic strip.

HDFC prints a four-cell strip on page 1:

    PREVIOUS STATEMENT DUES | PAYMENTS/CREDITS RECEIVED | PURCHASES/DEBIT | FINANCE CHARGES
    C16,403.27                C17,023.00                  C17,880.84       C0.00

Summing the extracted rows by direction and comparing against those two middle cells is
a CHEAP, MODEL-FREE production control. It catches the two worst defect classes found in
this evaluation, both of which are invisible to field-level validation:

  * a DUPLICATED debit (Luna, decrypt_252502266...): sum(DEBIT) overshoots by exactly
    the duplicated amount -- measured 18,672.84 vs printed 17,880.84, delta 792.00;
  * a DROPPED credit (incumbent, decrypt_2050509744...): sum(CREDIT) undershoots.

This is not a scoring instrument -- it is the deployable check recommended in the
report's verdict, validated here rather than merely asserted.
"""
import json
import os
import re
import sys
from collections import Counter

import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hdfc_lib as H
import score_lib as S

HERE = os.path.dirname(os.path.abspath(__file__))
TOL = 1.0   # rupees; absorbs rounding in the printed cells


def strip_cells(path):
    """-> (payments_credits, purchases_debit) from the page-1 arithmetic strip, or None.

    The four labels are printed as a block and the four values follow as another block,
    so position within the value run is what identifies each cell.
    """
    d = fitz.open(path)
    try:
        t = "\n".join(d[i].get_text() for i in range(d.page_count))
    finally:
        d.close()
    i = t.find("PREVIOUS STATEMENT DUES")
    if i < 0:
        return None
    window = t[i:i + 320]
    # 'C' here is the ITFRupee glyph for the rupee sign, not a credit marker.
    # The SIGN is load-bearing: a credit balance prints as 'C-0.18'. Omitting the
    # optional '-' from the capture made the parser skip that cell entirely and shift
    # every subsequent cell by one, which mis-flagged correct extractions (measured:
    # Opus read decrypt_1003374647... exactly right yet appeared to be 2,358 out).
    vals = [float(x.replace(",", ""))
            for x in re.findall(r"C\s?(-?[\d,]+\.\d{2})", window)]
    if len(vals) < 3:
        return None
    # order: previous dues, payments/credits, purchases/debit, finance charges
    #
    # FINANCE CHARGES is its OWN cell but appears in the transaction table as an
    # ordinary interest DEBIT row, so the debit side of the identity is
    # purchases + finance_charges. Omitting it made a correct extraction look like a
    # duplicate: Opus on decrypt_535035... overshot by 10,031.84, which is exactly that
    # statement's printed FINANCE CHARGES cell.
    finance = vals[3] if len(vals) > 3 else 0.0
    return vals[1], vals[2] + finance


def check(run_dir, label):
    matched, _, _ = H.build_join()
    run = S.load_run(os.path.join(HERE, run_dir))
    rows, no_strip = [], 0
    for m in matched:
        r = run.get(m["sid"])
        pj = (r or {}).get("parsed_json")
        if not isinstance(pj, dict):
            continue
        cells = strip_cells(m["path"])
        if cells is None:
            no_strip += 1
            continue
        credits_printed, debits_printed = cells
        tx = pj.get("transactions") or []
        deb = sum(x.get("amount") or 0 for x in tx if x.get("direction") == "DEBIT")
        cr = sum(x.get("amount") or 0 for x in tx if x.get("direction") == "CREDIT")
        rows.append({
            "sid": m["sid"],
            "n_txn": len(tx),
            "debit_sum": round(deb, 2), "debit_printed": debits_printed,
            "debit_delta": round(deb - debits_printed, 2),
            "credit_sum": round(cr, 2), "credit_printed": credits_printed,
            "credit_delta": round(cr - credits_printed, 2),
        })
    ok = [r for r in rows if abs(r["debit_delta"]) <= TOL and abs(r["credit_delta"]) <= TOL]
    over = [r for r in rows if r["debit_delta"] > TOL or r["credit_delta"] > TOL]
    under = [r for r in rows if r["debit_delta"] < -TOL or r["credit_delta"] < -TOL]
    return {
        "label": label, "run_dir": run_dir,
        "statements_checked": len(rows),
        "statements_without_strip": no_strip,
        "reconciles": len(ok),
        "reconcile_rate": round(len(ok) / len(rows), 4) if rows else None,
        "overshoots_possible_duplicate": [
            {k: r[k] for k in ("sid", "n_txn", "debit_sum", "debit_printed", "debit_delta",
                               "credit_sum", "credit_printed", "credit_delta")}
            for r in sorted(over, key=lambda r: -max(r["debit_delta"], r["credit_delta"]))[:25]],
        "undershoots_possible_dropped_row": [
            {k: r[k] for k in ("sid", "n_txn", "debit_sum", "debit_printed", "debit_delta",
                               "credit_sum", "credit_printed", "credit_delta")}
            for r in sorted(under, key=lambda r: min(r["debit_delta"], r["credit_delta"]))[:25]],
        "all": rows,
    }


def main():
    out = {}
    for run_dir, label in (("phase3_refined", "Luna (refined prompt)"),
                           ("gt_full", "Opus-5 GT"),
                           ("phase3_generic", "Luna (generic prompt)")):
        if not os.path.isdir(os.path.join(HERE, run_dir, "json")):
            continue
        res = check(run_dir, label)
        if res["statements_checked"]:
            out[run_dir] = res
            print(f"{label:26s} checked={res['statements_checked']:4d} "
                  f"reconciles={res['reconciles']:4d} ({res['reconcile_rate']}) "
                  f"no_strip={res['statements_without_strip']:3d} "
                  f"over={len(res['overshoots_possible_duplicate'])} "
                  f"under={len(res['undershoots_possible_dropped_row'])}")

    # the incumbent, measured on the same control
    matched, _, _ = H.build_join()
    rows = []
    for m in matched:
        cells = strip_cells(m["path"])
        if cells is None:
            continue
        cp, dp = cells
        tx = S.csv_extraction(m["csv_row"]).get("transactions") or []
        deb = sum(abs(x.get("amount") or 0) for x in tx if x.get("direction") == "DEBIT")
        cr = sum(abs(x.get("amount") or 0) for x in tx if x.get("direction") == "CREDIT")
        rows.append({"sid": m["sid"], "n_txn": len(tx),
                     "debit_sum": round(deb, 2), "debit_printed": dp,
                     "debit_delta": round(deb - dp, 2),
                     "credit_sum": round(cr, 2), "credit_printed": cp,
                     "credit_delta": round(cr - cp, 2)})
    ok = [r for r in rows if abs(r["debit_delta"]) <= TOL and abs(r["credit_delta"]) <= TOL]
    out["incumbent_csv"] = {
        "label": "Incumbent CSV (Gemini)", "run_dir": "hdfc.csv",
        "statements_checked": len(rows), "statements_without_strip": None,
        "reconciles": len(ok),
        "reconcile_rate": round(len(ok) / len(rows), 4) if rows else None,
        "overshoots_possible_duplicate": sorted(
            [r for r in rows if r["debit_delta"] > TOL or r["credit_delta"] > TOL],
            key=lambda r: -max(r["debit_delta"], r["credit_delta"]))[:25],
        "undershoots_possible_dropped_row": sorted(
            [r for r in rows if r["debit_delta"] < -TOL or r["credit_delta"] < -TOL],
            key=lambda r: min(r["debit_delta"], r["credit_delta"]))[:25],
        "all": rows,
    }
    i = out["incumbent_csv"]
    print(f"{i['label']:26s} checked={i['statements_checked']:4d} "
          f"reconciles={i['reconciles']:4d} ({i['reconcile_rate']}) "
          f"over={len(i['overshoots_possible_duplicate'])} "
          f"under={len(i['undershoots_possible_dropped_row'])}")

    H.G.atomic_write_json(os.path.join(HERE, "reconciliation.json"), out)

    # Does the control actually flag the two hand-verified defects?
    print("\ndoes the control catch the two hand-verified row defects?")
    for run_dir, sid, what in (
            ("phase3_refined",
             "decrypt_252502266_19bc220c2d3c07ef_4341XXXXXXXXXX35_14_01_2026_31",
             "Luna duplicated a 792.00 DEBIT"),
            ("incumbent_csv",
             "decrypt_2050509744_193d3164f9e1b9b7_4854XXXXXXXXXX47_16_12_2024",
             "incumbent dropped a 1200 CREDIT")):
        res = out.get(run_dir)
        if not res:
            continue
        hit = [r for r in res["all"] if r["sid"] == sid]
        if not hit:
            print(f"  {what}: statement has no printed strip -> NOT COVERED")
            continue
        r = hit[0]
        flagged = abs(r["debit_delta"]) > TOL or abs(r["credit_delta"]) > TOL
        print(f"  {'FLAGGED' if flagged else 'MISSED '}  {what}: "
              f"debit {r['debit_sum']} vs {r['debit_printed']} (delta {r['debit_delta']}), "
              f"credit {r['credit_sum']} vs {r['credit_printed']} (delta {r['credit_delta']})")


if __name__ == "__main__":
    main()
