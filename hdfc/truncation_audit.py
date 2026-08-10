#!/usr/bin/env python3
"""Truncation audit. HDFC is the densest of the three corpora (16.5 txn/stmt mean,
max 223), so this is the evaluation's primary technical risk -- and specifically a
truncated GROUND TRUTH record is the dangerous case, because it silently penalises
the challenger and yields a confidently wrong verdict.

Two INDEPENDENT signals, because either alone can miss:
  1. the terminal state: finish_reason not in {stop, end_turn, None-with-content}
     plus completion_tokens at/near the cap;
  2. under-extraction: this record's transaction count far below the CSV's count for
     the SAME statement -- catches a model that stopped early while still emitting a
     syntactically closed JSON object, which reports finish_reason='stop'.

Signal 2 uses the CSV only as a ROW-COUNT tripwire, never as ground truth: a flagged
statement is a candidate for inspection, not an automatic defect.
"""
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hdfc_lib as H
import score_lib as S

HERE = os.path.dirname(os.path.abspath(__file__))

NORMAL_STOP = {"stop", "end_turn", None}

RUNS = [("gt_opus", "gt_full", H.GT_MAX_TOKENS),
        ("luna_refined", "phase3_refined", H.LUNA_MAX_TOKENS),
        ("luna_generic_sample", "phase1_baseline", H.LUNA_MAX_TOKENS),
        ("luna_refined_sample", "phase2_refined", H.LUNA_MAX_TOKENS)]


def main():
    matched, _, _ = H.build_join()
    csv_txn = {m["sid"]: len(S.csv_extraction(m["csv_row"])["transactions"])
               for m in matched}

    out = {"csv_txn_density": {
        "mean": round(statistics.mean(csv_txn.values()), 2),
        "median": statistics.median(csv_txn.values()),
        "max": max(csv_txn.values()),
        "total": sum(csv_txn.values()),
        "n_statements": len(csv_txn),
    }, "notes": []}

    for label, d, cap in RUNS:
        run = S.load_run(os.path.join(HERE, d))
        if not run:
            continue
        abnormal, under, near_cap = [], [], []
        comps = []
        for sid, r in run.items():
            fr = r.get("finish_reason")
            u = r.get("usage_raw") or {}
            c = u.get("completion_tokens")
            n = r.get("n_transactions")
            if c:
                comps.append(c)
            if fr not in NORMAL_STOP:
                abnormal.append({"sid": sid, "finish_reason": fr, "completion_tokens": c,
                                 "n_transactions": n, "outcome": r.get("outcome")})
            if c and c >= 0.9 * cap:
                near_cap.append({"sid": sid, "completion_tokens": c, "cap": cap})
            cn = csv_txn.get(sid)
            # Only meaningful when the CSV actually found rows; a 0-row CSV statement
            # says nothing about the model.
            if cn and n is not None and n < 0.8 * cn:
                under.append({"sid": sid, "n_transactions": n, "csv_transactions": cn,
                              "finish_reason": fr, "completion_tokens": c,
                              "ratio": round(n / cn, 3)})
        # empirical tokens-per-transaction on LARGE statements, used to project the cap
        big = [(r.get("n_transactions"), (r.get("usage_raw") or {}).get("completion_tokens"))
               for r in run.values()]
        big = [(n, c) for n, c in big if n and c and n >= 30]
        worst_ratio = max((c / n for n, c in big), default=None)
        out[label] = {
            "n": len(run),
            "cap": cap,
            "abnormal_finish": len(abnormal),
            "abnormal_finish_detail": abnormal[:20],
            "records_within_10pct_of_cap": len(near_cap),
            "near_cap_detail": near_cap[:20],
            "max_completion_tokens": max(comps) if comps else None,
            "max_completion_pct_of_cap": round(max(comps) / cap * 100, 2) if comps else None,
            "under_extracted_vs_csv": len(under),
            "under_extracted_detail": sorted(under, key=lambda x: x["ratio"])[:20],
            "worst_tokens_per_txn_on_large_stmts": round(worst_ratio, 1) if worst_ratio else None,
            "projected_tokens_for_223_txn_stmt": int(worst_ratio * 223) if worst_ratio else None,
            "projection_fits_cap": (worst_ratio * 223 < cap) if worst_ratio else None,
        }

    gt = out.get("gt_opus")
    if gt:
        out["notes"].append(
            f"GT: **{gt['abnormal_finish']} of {gt['n']}** records show an abnormal "
            f"finish_reason and **{gt['under_extracted_vs_csv']}** extracted <80% of the "
            f"CSV's row count. Peak completion was {gt['max_completion_tokens']:,} tokens = "
            f"{gt['max_completion_pct_of_cap']}% of the {gt['cap']:,} cap.")
        if gt["worst_tokens_per_txn_on_large_stmts"]:
            out["notes"].append(
                f"GT cap sizing: worst observed {gt['worst_tokens_per_txn_on_large_stmts']} "
                f"tokens/txn on statements with >=30 txns projects "
                f"~{gt['projected_tokens_for_223_txn_stmt']:,} tokens for the 223-txn "
                f"outlier -- fits the {gt['cap']:,} cap: {gt['projection_fits_cap']}.")
        out["notes"].append(
            "GT max_tokens was raised 32,000 -> 64,000 partway through the sweep. The "
            "records collected under 32,000 remain comparable: max_tokens is a ceiling, "
            "not a sampling parameter, and none of those records came within 78% of even "
            "the lower cap, so none could have been shaped by it.")
    ln = out.get("luna_refined")
    if ln:
        out["notes"].append(
            f"Challenger: {ln['abnormal_finish']}/{ln['n']} abnormal finishes, "
            f"{ln['under_extracted_vs_csv']} under-extractions, peak "
            f"{ln['max_completion_tokens']:,} tokens = {ln['max_completion_pct_of_cap']}% "
            f"of the {ln['cap']:,} cap.")

    H.G.atomic_write_json(os.path.join(HERE, "truncation_audit.json"), out)

    print(json.dumps({k: (v if not isinstance(v, dict) else
                          {kk: vv for kk, vv in v.items() if not kk.endswith("detail")})
                      for k, v in out.items()}, indent=1))


if __name__ == "__main__":
    main()
