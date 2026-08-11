#!/usr/bin/env python3
"""Build the HDFC machine-readable roll-up from existing artifacts only."""
import json, os
from collections import Counter

import hdfc_lib as H

HERE = os.path.dirname(os.path.abspath(__file__))
load = lambda p: json.load(open(os.path.join(HERE, p)))

def slim_field(x):
    return {k: x.get(k) for k in ("n", "correct", "wrong_value",
            "null_when_populated", "hallucinated_when_gold_null", "accuracy")}

def comparison(x):
    tf = {k: slim_field(v) for k, v in x["transaction_fields"].items()}
    n = sum(v["n"] for v in tf.values())
    correct = sum(v["correct"] for v in tf.values())
    return {
        "label": x["label"], "n_statements": x["statements_scored"],
        "row_alignment": {
            **x["transaction_matching"],
            "classification": "PAIRING-BASED; NOT A CORRECTNESS CLAIM",
            "admission_field": "description", "description_similarity_threshold": 0.55,
            "one_to_one_assignment": True,
            "warning": "precision/recall/F1 here measure row admission and pairing only",
        },
        "value_correctness": {
            "classification": "VALUE CORRECTNESS OVER MATCHED PAIRS",
            "normalization": "score_lib canonical comparison; formatting differences are not errors",
            "fields": tf, "micro": {"n": n, "correct": correct,
                "accuracy": round(correct / n, 4)},
        },
        "statement_fields": {k: slim_field(v) for k, v in x["statement_fields"].items()},
    }

def main():
    scores = load("scores_phase3.json")
    txn, stmt = load("adjudication_txn.json"), load("adjudication_stmt.json")
    misses, corr = load("glaring_misses.json"), load("corrected_scores.json")
    matched, unmatched, pdf_missing = H.build_join()
    csv_rows = H.csv_rows()
    failed = [x.strip() for x in open(os.path.join(H.PDF_ROOT, "failed-download-links.txt")) if x.strip()]
    unmatched_links = [r.get("link", "").strip() for r in unmatched]
    rc = txn["row_counts"]
    tok = scores["tokens"]
    gt, luna = tok["gt_opus"], tok["luna_refined"]
    gt_in_cost = gt["input_total"] * 5 / 1_000_000
    gt_out_cost = gt["output_total"] * 25 / 1_000_000
    old_desc = {"disagreements": 120, "CSV_WRONG": 88, "LUNA_WRONG": 31,
                "BOTH_WRONG": 1, "AMBIGUOUS_IN_PDF": 0}
    desc = txn["by_field"]["description"]
    out = {
      "status": "complete; artifacts recomputed without model inference",
      "record_counts": {
        "directory_entries": len(os.listdir(H.PDF_ROOT)), "pdfs_on_disk": len(H.discover_pdfs()),
        "failed_download_file_entries": len(failed), "csv_rows": len(csv_rows),
        "incumbent_csv_joined": len(matched), "scoreable": len(matched),
        "csv_rows_unmatched": len(unmatched), "pdfs_without_csv_row": len(pdf_missing),
        "unmatched_links_equal_failed_download_links": set(unmatched_links) == set(failed),
        "unmatched_links_set_sizes": {"unmatched": len(set(unmatched_links)), "failed": len(set(failed))},
      },
      "excluded_arms": [],
      "matcher": {"admission": "description similarity only", "description_similarity_threshold": 0.55,
        "assignment": "greedy one-to-one over globally sorted similarity", "order_sensitive": False,
        "warning": "row-alignment metrics are not correctness metrics"},
      "comparisons": {
        "luna_vs_gt": comparison(scores["scores"]["luna_refined_vs_GT__all"]),
        "csv_vs_gt": comparison(scores["scores"]["CSV_vs_GT__all"]),
        "luna_vs_csv": comparison(scores["scores"]["luna_refined_vs_CSV__all"]),
      },
      "gt_corrected_accuracy": {
        "luna_vs_gt": {"headline_unchanged": True,
          "correction_2_fx_prompt_gap": corr["CORRECTION_2_FX_instrument_asymmetry_vs_GT"]},
        "csv_vs_gt": None,
        "separate_rejectable_corrections": {
          "correction_1_csv_wrong_cells": corr["CORRECTION_1_luna_vs_CSV_agreement_minus_CSV_WRONG"],
          "correction_2_fx_prompt_gap": corr["CORRECTION_2_FX_instrument_asymmetry_vs_GT"],
        },
      },
      "pdf_adjudication": {"statement": {k: stmt[k] for k in ("total_disagreements","overall","overall_heldout","by_field")},
        "transaction": {"overall": txn["overall"], "by_field": txn["by_field"],
          "findings_are_complete": True, "n_findings": len(txn["findings"])},
        "glaring_misses_counts": misses["counts"], "glaring_misses_by_field": misses["by_field"],
        "findings_cap_correction": {"old_truncated_description": old_desc,
          "corrected_description": desc,
          "delta": {k: desc[k] - old_desc[k] for k in old_desc},
          "old_overall": {"CSV_WRONG":209,"LUNA_WRONG":57,"AMBIGUOUS_IN_PDF":107,"BOTH_WRONG":7},
          "corrected_overall": txn["overall"]},
        "statement_adjudicator_cap_check": "no findings cap; [:limit] only bounds duplicate rectangle search results per value"},
      "gt_audit": {"gt_arm": "Opus-5", "n_statements": gt["n_calls"], "no_model_inference_in_rollup": True},
      "tokens": {"luna_refined": luna, "opus_gt": {**gt, "pricing_usd_per_million": {"input":5,"output":25},
        "input_cost_usd": round(gt_in_cost,2), "output_cost_usd": round(gt_out_cost,2),
        "total_cost_usd": round(gt_in_cost+gt_out_cost,2),
        "cost_per_statement_usd": round((gt_in_cost+gt_out_cost)/len(matched),4)},
        "comparative": {"gt_to_luna_input_ratio": round(gt["input_total"]/luna["input_total"],2),
          "gt_to_luna_output_ratio": round(gt["output_total"]/luna["output_total"],2),
          "luna_reasoning_pct_of_output": round(100*luna["reasoning_total"]/luna["output_total"],1),
          "reasoning_is_inside_output": luna["reasoning_nested_inside_completion"]}},
      "gt_output_health": load("cap_audit.json"),
      "statement_date_rule": None,
      "notes": {"row_counts": {"statements":len(rc), "pairs":sum(x["pairs"] for x in rc),
        "luna_rows":sum(x["luna"] for x in rc), "csv_rows":sum(x["csv"] for x in rc),
        "statements_with_luna_only_rows":sum(x["luna_only"]>0 for x in rc),
        "statements_with_csv_only_rows":sum(x["csv_only"]>0 for x in rc)},
        "detection_source_counts": dict(Counter(r.get("detectionSource") for r in csv_rows)),
        "uppercase_PDF":sum(f.endswith('.PDF') for _,f,_ in H.discover_pdfs()),
        "filenames_with_spaces":sum(' ' in f for _,f,_ in H.discover_pdfs())},
      "discrepancies": [
        {"source":"HDFC_REPORT.md", "issue":"stale partial coverage", "reported":"GT 154/281; Luna 131/281", "recomputed":"281/281 each"},
        {"source":"HDFC_REPORT.md", "issue":"transaction findings cap", "reported_description":old_desc, "recomputed_description":desc},
        {"source":"HDFC_REPORT.md", "issue":"stale transaction overall", "reported":{"AMBIGUOUS_IN_PDF":101,"BOTH_WRONG":1,"CSV_WRONG":116,"LUNA_WRONG":49}, "recomputed":txn["overall"]},
        {"source":"HDFC_REPORT.md", "issue":"stale glaring misses totals", "reported":{"luna_substantive_errors":53,"incumbent_substantive_errors":128}, "recomputed":misses["counts"]},
        {"source":"REPORT_TABLES.md", "issue":"stale partial-run tables (69/147-style subsets and capped adjudication)", "recomputed":"full 281-statement artifacts used"},
        {"source":"NOTES_verified_findings.md", "issue":"stale partial description analysis", "reported":"31 description defects at 147/281", "recomputed_description_luna_wrong":desc["LUNA_WRONG"]},
      ]
    }
    H.G.atomic_write_json(os.path.join(HERE, "final_scores.json"), out)
    json.load(open(os.path.join(HERE, "final_scores.json")))
    print("final_scores.json ok")

if __name__ == "__main__": main()
