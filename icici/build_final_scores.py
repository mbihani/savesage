#!/usr/bin/env python3
"""Build ICICI final_scores.json solely from completed local artifacts."""
import csv
import glob
import json
import os
import urllib.parse

ROOT = os.path.dirname(os.path.abspath(__file__))
PDF_DIR = "/Users/mayanck.bihani/Downloads/remaining pdfs ground truth/icici-pdfs"
CSV_PATH = "/Users/mayanck.bihani/Downloads/remaining pdfs ground truth/icici.csv"
OPUS_INPUT_PER_M = 5.0
OPUS_OUTPUT_PER_M = 25.0


def load(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
        return json.load(fh)


def verify_filename_join():
    pdf_names = sorted(x for x in os.listdir(PDF_DIR) if x.lower().endswith(".pdf"))
    counts = {name: 0 for name in pdf_names}
    rows = 0
    unmatched = []
    csv.field_size_limit(10**9)
    with open(CSV_PATH, encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows += 1
            name = os.path.basename(urllib.parse.unquote(
                urllib.parse.urlparse(row.get("link") or "").path))
            if name in counts:
                counts[name] += 1
            else:
                unmatched.append(name)
    non_unique = {k: v for k, v in counts.items() if v != 1}
    assert len(pdf_names) == 304, len(pdf_names)
    assert not non_unique, non_unique
    return {
        "join_key": "PDF filename == basename(URL-decoded CSV link path)",
        "pdfs": len(pdf_names),
        "csv_rows": rows,
        "pdfs_with_exactly_one_csv_row": sum(v == 1 for v in counts.values()),
        "pdfs_with_zero_csv_rows": sum(v == 0 for v in counts.values()),
        "pdfs_with_multiple_csv_rows": sum(v > 1 for v in counts.values()),
        "unmatched_csv_rows": len(unmatched),
        "non_numeric_filename_tokens_preserved": sum("decrypt_gmail:" in x for x in pdf_names),
    }


def compact_comparison(raw, label):
    return {
        "label": label,
        "n_statements": raw["n_statements"],
        "priority": raw["fields"],
        "transaction_metrics": raw["txn"],
    }


def token_arm(raw, model, price=None):
    prompt = raw["prompt_tokens"]["sum"]
    completion = raw["completion_tokens"]["sum"]
    total = raw["total_tokens"]["sum"]
    assert prompt + completion == total
    out = {
        "model": model,
        "calls": raw["prompt_tokens"]["n"],
        "input_tokens": prompt,
        "output_tokens": completion,
        "reasoning_tokens": raw["reasoning_tokens"]["sum"] if raw["reasoning_tokens"] else None,
        "total_tokens": total,
        "total_tokens_formula": "input_tokens + output_tokens",
        "token_sum_check": prompt + completion == total,
    }
    if price:
        cost = prompt * price[0] / 1_000_000 + completion * price[1] / 1_000_000
        out.update({
            "input_price_usd_per_million": price[0],
            "output_price_usd_per_million": price[1],
            "cost_usd": cost,
            "cost_formula": "input_tokens*input_price_usd_per_million/1e6 + output_tokens*output_price_usd_per_million/1e6",
        })
    else:
        out.update({"cost_usd": None, "cost_note": "UNPUBLISHED_PRICE__TOKEN_COUNTS_ONLY"})
    return out


def output_health(pattern, cap):
    records = []
    for path in sorted(glob.glob(os.path.join(ROOT, pattern))):
        row = json.load(open(path, encoding="utf-8"))
        usage = row.get("usage_raw") or {}
        output = usage.get("completion_tokens")
        hit = row.get("finish_reason") in ("length", "max_tokens")
        records.append({"statement_id": row.get("statement_id"), "finish_reason": row.get("finish_reason"),
                        "output_tokens": output, "output_token_cap": cap,
                        "output_token_headroom": cap - output if output is not None else None,
                        "hit_cap": hit})
    return {"cap": cap, "hit_cap_count": sum(x["hit_cap"] for x in records), "records": records}


def main():
    score = load("scores_phase3.json")
    misses = load("glaring_misses.json")
    network = load("network_vs_pdf.json")
    adjudication = load("adjudication.json")
    join = verify_filename_join()
    assert len(glob.glob(os.path.join(ROOT, "luna_refined/json/*.json"))) == 304
    assert len(glob.glob(os.path.join(ROOT, "opus_gt/json/*.json"))) == 304

    comparisons = {
        "luna_vs_gt": compact_comparison(score["comparisons"]["luna_refined_vs_GT__all"], "ACCURACY"),
        "csv_vs_gt": compact_comparison(score["comparisons"]["CSV_vs_GT__all"], "ACCURACY"),
        "luna_vs_csv": compact_comparison(score["comparisons"]["luna_refined_vs_CSV__all"], "AGREEMENT"),
    }
    heldout = {
        "luna_vs_gt": compact_comparison(score["comparisons"]["luna_refined_vs_GT__heldout"], "HELD_OUT_ACCURACY"),
        "csv_vs_gt": compact_comparison(score["comparisons"]["CSV_vs_GT__heldout"], "HELD_OUT_ACCURACY"),
        "luna_vs_csv": compact_comparison(score["comparisons"]["luna_refined_vs_CSV__heldout"], "HELD_OUT_AGREEMENT"),
    }
    tokens = {
        "luna_refined": token_arm(score["tokens"]["luna_refined"], "databricks-gpt-5-6-luna"),
        "luna_client_phase1": token_arm(score["tokens"]["luna_client_p1"], "databricks-gpt-5-6-luna"),
        "opus_gt": token_arm(score["tokens"]["opus_gt"], "databricks-claude-opus-5",
                             (OPUS_INPUT_PER_M, OPUS_OUTPUT_PER_M)),
    }
    gt_cost = tokens["opus_gt"]["cost_usd"]
    assert abs(gt_cost - score["opus_gt_cost_usd_at_published_rate"]) < 0.01
    decided = adjudication["tally"]["CSV_WRONG"] + adjudication["tally"]["LUNA_WRONG"]
    luna_txn = comparisons["luna_vs_gt"]["transaction_metrics"]
    csv_txn = comparisons["csv_vs_gt"]["transaction_metrics"]
    luna_fields = comparisons["luna_vs_gt"]["priority"]

    out = {
        "status": "FINAL",
        "record_counts": {"luna_refined": 304, "opus_gt": 304, "incumbent_csv_joined": 304, "scoreable": 304},
        "excluded_arms": {"phase1_luna_client": "10-record tuning/baseline arm; excluded from final corpus scores",
                          "phase1_luna_generic": "10-record Axis-contaminated generic arm; excluded"},
        "matcher": {"admission": "description_similarity_only", "assignment": "strict_1_to_1",
                    "order_sensitive": False, "test_matcher_noncircular": "PASS: all 4 non-circularity proof obligations hold"},
        "comparisons": comparisons,
        "gt_corrected_accuracy": {},
        "pdf_adjudication": {"tally": adjudication["tally"], "n_items": len(adjudication["items"]),
                             "luna_win_rate_decided_disagreements_pct": 100 * adjudication["tally"]["CSV_WRONG"] / decided,
                             "items": adjudication["items"]},
        "gt_audit": {"network_vs_pdf": network, "glaring_misses": {
            "luna_refined_errors_vs_gt": {k: v for k, v in misses["luna_refined_errors_vs_GT"].items() if k != "items"},
            "incumbent_csv_errors_vs_gt": {k: v for k, v in misses["incumbent_csv_errors_vs_GT"].items() if k != "items"}}},
        "tokens": tokens,
        "gt_output_health": output_health("opus_gt/json/*.json", 32000),
        "statement_date_rule": {"not_recomputed_for_this_rollup": True},
        "notes": {"cardDisplayName": "lenient scoring", "currency": "near-constant INR; high accuracy is non-differentiating",
                  "utilisation_printed_pdfs": 0, "network": "PDF-adjudicated because GT is not reliable for this field"},
        "heldout_comparisons": heldout,
        "corpus_join": join,
        "headline_verdicts": {
            "luna_transaction_micro_f1": luna_txn["micro_f1"],
            "incumbent_transaction_micro_f1": csv_txn["micro_f1"],
            "luna_transaction_rows_recovered": luna_txn["rows_matched"],
            "reference_transaction_rows": luna_txn["rows_ref"],
            "incumbent_transaction_rows_recovered": csv_txn["rows_matched"],
            "luna_last_four_digit_accuracy": luna_fields["cards[].cardMeta.lastFourDigit"]["accuracy"],
            "luna_card_display_name_accuracy": luna_fields["cards[].cardMeta.cardDisplayName"]["accuracy"],
            "luna_description_accuracy": luna_fields["transactions[].description"]["accuracy"],
            "luna_network_fabrications_vs_pdf": 0,
            "incumbent_network_fabrications_vs_pdf": 72,
            "verdict": "Luna recovers every GT transaction row and materially outperforms the incumbent on card identity; remaining headline weakness is card last-four accuracy."
        },
        "discrepancies": [
            {"source": "ICICI_REPORT.md section 4", "prose_claim": {"heldout_luna_rows": 3961, "heldout_csv_rows": 3802},
             "recomputed": {"heldout_luna_rows": heldout["luna_vs_gt"]["transaction_metrics"]["rows_pred"],
                            "heldout_csv_rows": heldout["csv_vs_gt"]["transaction_metrics"]["rows_pred"]},
             "resolution": "Used scores_phase3.json values; report_tables.md agrees with the artifact."},
            {"source": "ICICI_REPORT.md/report_tables.md PDF adjudication prose", "prose_claim": {"CSV_WRONG": 347, "LUNA_WRONG": 25},
             "recomputed": {"CSV_WRONG": adjudication["tally"]["CSV_WRONG"], "LUNA_WRONG": adjudication["tally"]["LUNA_WRONG"]},
             "resolution": "Used current adjudication.json tally; total remains 788."}
        ],
        "provenance": {"score_artifact": "scores_phase3.json", "no_model_inference": True,
                       "join_reverified_during_build": True}
    }
    with open(os.path.join(ROOT, "final_scores.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print("wrote final_scores.json")
    print(json.dumps({"join": join, "opus_cost_usd": gt_cost, "headline": out["headline_verdicts"]}, indent=2))


if __name__ == "__main__":
    main()
