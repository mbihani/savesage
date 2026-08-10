#!/usr/bin/env python3
"""Render the measured numbers as markdown tables for HDFC_REPORT.md.

Kept separate from the narrative so every table in the report is generated from
scores_phase3.json / adjudication_*.json rather than transcribed by hand.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HERE = os.path.dirname(os.path.abspath(__file__))

# The 16 client-priority fields, in the order the brief lists them.
PRIORITY = [
    ("cardDisplayName", "cardDisplayName"),
    ("lastFourDigit", "lastFourDigit"),
    ("network", "network"),
    ("statementLevelSummary.totalAmountDue", "sls.totalAmountDue"),
    ("statementLevelSummary.availableCreditLimit", "sls.availableCreditLimit"),
    ("statementLevelSummary.utilisationPercent", "sls.utilisationPercent (as-extracted)"),
    ("statementLevelSummary.utilisationPercent_DERIVED", "sls.utilisationPercent (derived)"),
    ("statementLevelSummary.totalCreditLimit", "sls.totalCreditLimit"),
    ("statementLevelSummary.totalMinimumAmountDue", "sls.totalMinimumAmountDue"),
    ("statementMeta.issuerName", "meta.issuerName"),
    ("statementMeta.statementDate", "meta.statementDate"),
    ("statementMeta.dueDate", "meta.dueDate"),
]
TXN = ["date", "description", "amount", "direction", "currency"]

NOTE = {
    "statementMeta.issuerName": "NON-DISCRIMINATING (single issuer, 281/281 HDFC Bank)",
    "currency": "NEAR-CONSTANT (98.7% INR)",
    "cardDisplayName": "LENIENT (containment); unstable run-to-run",
    "statementLevelSummary.utilisationPercent": "printed in 0/281 PDFs",
}


def load(p):
    fp = os.path.join(HERE, p)
    return json.load(open(fp)) if os.path.exists(fp) else None


def pct(x):
    return "—" if x is None else f"{100 * x:.2f}%"


def field_table(score, title):
    if not score:
        return f"_{title}: not run._\n"
    sf = score["statement_fields"]
    tf = score["transaction_fields"]
    out = [f"**{title}** — n={score['statements_scored']} statements. "
           f"{score['comparison']}\n",
           "| field | n | accuracy | wrong | null_when_populated | hallucinated_when_GT_null | note |",
           "|---|---:|---:|---:|---:|---:|---|"]
    for key, label in PRIORITY:
        d = sf.get(key)
        if not d:
            continue
        out.append(f"| {label} | {d['n']} | {pct(d['accuracy'])} | {d['wrong_value']} | "
                   f"{d['null_when_populated']} | {d['hallucinated_when_gold_null']} | "
                   f"{NOTE.get(key,'')} |")
    for f in TXN:
        d = tf.get(f)
        if not d:
            continue
        out.append(f"| transactions.{f} | {d['n']} | {pct(d['accuracy'])} | {d['wrong_value']} | "
                   f"{d['null_when_populated']} | {d['hallucinated_when_gold_null']} | "
                   f"{NOTE.get(f,'')} |")
    tm = score["transaction_matching"]
    out.append("")
    out.append(f"Transaction matching (description-only 1:1): pairs={tm['matched_pairs']}, "
               f"pred-only={tm['pred_only_false_pos']}, gold-only={tm['gold_only_false_neg']}, "
               f"**P={pct(tm['precision'])} R={pct(tm['recall'])} F1={pct(tm['f1'])}**, "
               f"description exact-match={pct(tm['description_exact_match_rate'])}, "
               f"mean similarity={tm['description_mean_similarity']}\n")
    return "\n".join(out)


def token_table(tok):
    out = ["| run | calls | input total | output total | reasoning total | grand total | "
           "in mean/med/max | out mean/med/max | reasoning mean/med/max | p+c==total | reasoning inside completion |",
           "|---|---:|---:|---:|---:|---:|---|---|---|---:|---|"]
    for k, t in tok.items():
        out.append(
            f"| {k} | {t['n_calls']} | {t['input_total']:,} | {t['output_total']:,} | "
            f"{t['reasoning_total']:,} | {t['grand_total']:,} | "
            f"{t['input_mean']}/{t['input_median']}/{t['input_max']} | "
            f"{t['output_mean']}/{t['output_median']}/{t['output_max']} | "
            f"{t['reasoning_mean']}/{t['reasoning_median']}/{t['reasoning_max']} | "
            f"{t['prompt_plus_completion_equals_total']}/{t['n_calls']} | "
            f"{t['reasoning_nested_inside_completion']} |")
    return "\n".join(out)


def adjud_table(a, level):
    if not a:
        return f"_{level} adjudication not available._\n"
    out = [f"| field | disagreements | LUNA_WRONG | CSV_WRONG | BOTH_WRONG | AMBIGUOUS_IN_PDF | Luna right (of separable) |",
           "|---|---:|---:|---:|---:|---:|---:|"]
    for f, c in sorted(a["by_field"].items(), key=lambda kv: -kv[1]["disagreements"]):
        out.append(f"| {f} | {c['disagreements']} | {c['LUNA_WRONG']} | {c['CSV_WRONG']} | "
                   f"{c['BOTH_WRONG']} | {c['AMBIGUOUS_IN_PDF']} | "
                   f"{pct(c['luna_right_share_of_separable'])} |")
    out.append("")
    out.append(f"Overall: `{a['overall']}`")
    if a.get("overall_heldout"):
        out.append(f"Held-out only: `{a['overall_heldout']}`")
    return "\n".join(out)


def main():
    s = load("scores_phase3.json")
    if not s:
        raise SystemExit("run score_full.py first")
    parts = []
    parts.append("## Corpus and join\n")
    c = s["corpus"]
    parts.append(f"""| | |
|---|---:|
| PDFs on disk | {c['pdfs_on_disk']} |
| CSV data rows | {c['csv_data_rows']} |
| **Joined / scoreable** | **{c['joined_scoreable']}** |
| CSV rows not joining | {c['csv_rows_unmatched']} |
| PDFs with no CSV row | {c['pdfs_without_csv_row']} |
| Opus-5 GT usable | {s['gt_usable_statements']} |

{c['note']}
""")

    parts.append("\n## Outcome tally\n")
    parts.append("| run | outcomes |\n|---|---|")
    for k, v in s["outcomes"].items():
        parts.append(f"| {k} | `{v}` |")

    parts.append("\n\n## Token accounting\n")
    parts.append(token_table(s["tokens"]))
    if s.get("gt_opus_cost_usd_published_rate"):
        parts.append(f"\nOpus-5 GT cost at published rate ($5/M in, $25/M out): "
                     f"**${s['gt_opus_cost_usd_published_rate']}**. "
                     f"Luna's price is unpublished — token counts only, no dollar figure.\n")

    parts.append("\n## Field-by-field\n")
    for key, title in [
        ("luna_refined_vs_GT__all", "Refined Luna vs Opus-5 GT — ALL statements"),
        ("luna_refined_vs_GT__heldout", "Refined Luna vs Opus-5 GT — HELD-OUT (excl. 10 tuning)"),
        ("CSV_vs_GT__all", "Incumbent CSV vs Opus-5 GT — ALL statements"),
        ("CSV_vs_GT__heldout", "Incumbent CSV vs Opus-5 GT — HELD-OUT"),
        ("luna_refined_vs_CSV__all", "Refined Luna vs Incumbent CSV — ALL (AGREEMENT)"),
        ("luna_refined_vs_CSV__heldout", "Refined Luna vs Incumbent CSV — HELD-OUT (AGREEMENT)"),
        ("luna_generic_full_vs_GT__all", "GENERIC-prompt Luna vs Opus-5 GT — ALL"),
        ("luna_generic_full_vs_CSV__all", "GENERIC-prompt Luna vs Incumbent CSV — ALL"),
    ]:
        parts.append(field_table(s["scores"].get(key), title))
        parts.append("")

    parts.append("\n## Adjudication of Luna-vs-CSV disagreements (statement level)\n")
    parts.append(adjud_table(load("adjudication_stmt.json"), "statement"))
    parts.append("\n\n## Adjudication of Luna-vs-CSV disagreements (transaction level)\n")
    parts.append(adjud_table(load("adjudication_txn.json"), "transaction"))

    mis = load("glaring_misses.json")
    if mis:
        parts.append("\n\n## Glaring misses — counts\n")
        parts.append("| | count |\n|---|---:|")
        for k, v in mis["counts"].items():
            parts.append(f"| {k} | {v} |")
        parts.append(f"\nHeld-out only: `{mis['counts_heldout']}`\n")
        parts.append(f"By field — Luna: `{mis['by_field']['luna']}`\n")
        parts.append(f"By field — incumbent: `{mis['by_field']['incumbent']}`\n")

    open(os.path.join(HERE, "REPORT_TABLES.md"), "w").write("\n".join(parts))
    print("wrote REPORT_TABLES.md")


if __name__ == "__main__":
    main()
