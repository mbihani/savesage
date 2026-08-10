#!/usr/bin/env python3
"""Render the Phase-3 markdown tables from scores_phase3.json + adjudication.json.

Emits report_tables.md, which is pasted into ICICI_REPORT.md. Kept separate so the
numbers in the report are generated from the scored artifacts and never hand-typed.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import icici_lib as L
import score_lib as S

HERE = L.HERE
D = json.load(open(os.path.join(HERE, "scores_phase3.json")))
A = json.load(open(os.path.join(HERE, "adjudication.json")))

# Fields that a high score must NOT be read as "earned".
FLAGS = {
    "statementMeta.issuerName":
        "NON-DISCRIMINATING: single issuer, 303/304 incumbent rows are 'ICICI Bank'",
    "transactions[].currency":
        "NEAR-CONSTANT: 3,917/3,932 incumbent rows are INR",
    "cards[].cardMeta.cardDisplayName":
        "LENIENT SCORING (substring match); unstable run-to-run even inside the GT",
    "cards[].cardMeta.network":
        "TRIVIAL-NULL: not printed on ICICI statements; almost all pairs are both_null",
    "statementLevelSummary.utilisationPercent@extracted":
        "NOT PRINTED IN ANY PDF (0/304 contain 'utilis'); no model emits it",
    "statementLevelSummary.utilisationPercent@derived":
        "ARITHMETIC, not extraction: each side derived from its OWN totalAmountDue/totalCreditLimit",
}

ORDER = []
for f in S.PRIORITY:
    if f == "statementLevelSummary.utilisationPercent":
        ORDER += [f + "@extracted", f + "@derived"]
    else:
        ORDER.append(f)


def fmt(a):
    if not a:
        return None
    acc = a["accuracy"]
    return (a["n"], a["scored_n"], "n/a" if acc is None else f"{acc*100:.2f}%",
            a["wrong_value"], a["null_when_populated"], a["hallucinated_when_null"],
            a["both_null"])


def field_table(keys, title, note=""):
    out = [f"### {title}", ""]
    if note:
        out += [note, ""]
    cols = " | ".join(f"{k.split('__')[0].replace('_',' ')}" for k in keys)
    out.append(f"| field | " + " | ".join(
        f"n | scored | acc | wrong | null | halluc" for _ in keys) + " |")
    out.append("|---|" + "".join("---:|---:|---:|---:|---:|---:|" for _ in keys))
    for f in ORDER:
        cells = []
        ok = False
        for k in keys:
            a = (D["comparisons"].get(k) or {}).get("fields", {}).get(f)
            r = fmt(a)
            if r:
                ok = True
                cells.append(f"{r[0]} | {r[1]} | **{r[2]}** | {r[3]} | {r[4]} | {r[5]}")
            else:
                cells.append("– | – | – | – | – | –")
        if ok:
            out.append(f"| `{f}` | " + " | ".join(cells) + " |")
    return "\n".join(out)


def txn_table(keys):
    out = ["| metric | " + " | ".join(k for k in keys) + " |",
           "|---|" + "".join("---:|" for _ in keys)]
    rows = [("statements", "statements"), ("rows_pred", "rows (pred)"),
            ("rows_ref", "rows (ref)"), ("rows_matched", "rows matched"),
            ("micro_precision", "micro precision"), ("micro_recall", "micro recall"),
            ("micro_f1", "micro F1"), ("macro_f1", "macro F1 (per-statement mean)"),
            ("mean_desc_sim", "mean description similarity"),
            ("desc_exact_char_for_char", "descriptions exact char-for-char"),
            ("desc_exact_casefold", "descriptions exact (casefold)"),
            ("row_count_exact_match_statements", "statements with exact row count")]
    for key, label in rows:
        cells = []
        for k in keys:
            t = (D["comparisons"].get(k) or {}).get("txn") or {}
            v = t.get(key)
            if v is None:
                cells.append("–")
            elif isinstance(v, float):
                cells.append(f"{v:.4f}")
            else:
                cells.append(f"{v:,}")
        out.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(out)


def tokens_table():
    out = ["| arm | model | calls | input | output | reasoning | total | out/stmt median | out/stmt max |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    names = {"luna_refined": "Luna 5.6 (refined prompt)",
             "luna_client_p1": "Luna 5.6 (client baseline, 10 only)",
             "opus_gt": "Opus 5 (GT)"}
    for k, t in D["tokens"].items():
        p, c, tt, r = (t["prompt_tokens"], t["completion_tokens"],
                       t["total_tokens"], t["reasoning_tokens"])
        if not p:
            continue
        model = ("databricks-gpt-5-6-luna" if k.startswith("luna")
                 else "databricks-claude-opus-5")
        reasoning = f"{r['sum']:,}" if r else "not reported"
        out.append(
            f"| {names.get(k,k)} | `{model}` | {p['n']:,} | {p['sum']:,} | {c['sum']:,} "
            f"| {reasoning} | {tt['sum']:,} | {c['median']:,.0f} | {c['max']:,} |")
    return "\n".join(out)


def main():
    parts = []
    sc = D["scoreable"]
    parts.append(f"""## Scope actually measured

| | count |
|---|---:|
| PDFs in the ICICI corpus | **{D['corpus_pdfs']}** |
| CSV data rows | {D['csv_join']['csv_rows']} |
| CSV rows joined to a PDF (URL-decoded basename) | **{D['csv_join']['matched']}** |
| CSV rows with no PDF on disk | {D['csv_join']['unmatched_csv_rows']} |
| statements with an Opus-5 GT | {sc['with_opus_gt']} |
| statements with an incumbent CSV extraction | {sc['with_csv']} |
| **scoreable 3-way intersection** | **{sc['intersection_gt_and_csv']}** |
| held-out (intersection minus the 10 tuning statements) | **{sc['held_out_excl_10_tuning']}** |
""")

    parts.append("## Outcome tally\n")
    parts.append("| arm | " + " | ".join(sorted({o for v in D["outcomes"].values() for o in v}))
                 + " | 429-affected calls |")
    allo = sorted({o for v in D["outcomes"].values() for o in v})
    parts.append("|---|" + "".join("---:|" for _ in allo) + "---:|")
    for k, v in D["outcomes"].items():
        parts.append(f"| `{k}` | " + " | ".join(str(v.get(o, 0)) for o in allo)
                     + f" | {D['rate_limited_calls'].get(k,0)} |")

    parts.append("\n## Token accounting (verbatim `usage`)\n")
    parts.append(tokens_table())
    parts.append("")
    for k, t in D["tokens"].items():
        rp = t["reasoning_placement"]
        if rp["n"]:
            parts.append(f"* `{k}`: {rp}")
    parts.append(f"\n* Opus 5 cost at its published rate "
                 f"(${L.OPUS_PRICE_IN_PER_M}/M in, ${L.OPUS_PRICE_OUT_PER_M}/M out): "
                 f"**${D.get('opus_gt_cost_usd_at_published_rate')}**")
    parts.append(f"* Luna cost: **{D['luna_cost_usd']}** — Luna's price is unpublished, so no "
                 f"dollar figure is given and none is interpolated from a sibling model.")

    parts.append("\n## Field-by-field — ALL statements\n")
    parts.append(field_table(["luna_refined_vs_GT__all"],
                             "Luna (refined) vs Opus-5 GT — ACCURACY"))
    parts.append("")
    parts.append(field_table(["CSV_vs_GT__all"],
                             "Incumbent CSV vs Opus-5 GT — INCUMBENT ACCURACY"))
    parts.append("")
    parts.append(field_table(["luna_refined_vs_CSV__all"],
                             "Luna (refined) vs incumbent CSV — AGREEMENT (not accuracy)"))

    parts.append("\n## Field-by-field — HELD OUT (excludes the 10 tuning statements)\n")
    parts.append(field_table(["luna_refined_vs_GT__heldout"],
                             "Luna (refined) vs Opus-5 GT — held-out ACCURACY"))
    parts.append("")
    parts.append(field_table(["CSV_vs_GT__heldout"],
                             "Incumbent CSV vs Opus-5 GT — held-out"))

    parts.append("\n## Transactions\n")
    parts.append(txn_table(["luna_refined_vs_GT__all", "CSV_vs_GT__all",
                            "luna_refined_vs_GT__heldout", "CSV_vs_GT__heldout"]))

    parts.append("\n## Fields where a high score must NOT be read as earned\n")
    ent_path = os.path.join(HERE, "field_entropy.json")
    if os.path.exists(ent_path):
        ent = json.load(open(ent_path))
        parts.append("Discriminating power MEASURED in the Opus-5 GT — a field whose single most "
                     "common value covers ~all instances is trivially solved, so a high score on "
                     "it reflects the corpus, not the model.\n")
        parts.append("| field | instances | distinct values | top value | top share | verdict |")
        parts.append("|---|---:|---:|---|---:|---|")
        for f, d in sorted(ent.items(), key=lambda x: -x[1]["top_value_share"]):
            parts.append(f"| `{f}` | {d['n']:,} | {d['distinct_values']} | "
                         f"`{d['top_value'][:28]}` | {d['top_value_share']*100:.1f}% | "
                         f"{'**' + d['verdict'] + '**' if d['verdict'] != 'DISCRIMINATING' else d['verdict']} |")
        parts.append("")
    for f, why in FLAGS.items():
        parts.append(f"* `{f}` — {why}")

    parts.append("\n## Adjudication of Luna-vs-incumbent disagreements (against the PDF)\n")
    parts.append(f"{A['n_disagreements']} priority-field disagreements adjudicated across "
                 f"{A['n_luna_statements_adjudicated']} statements.\n")
    parts.append("| classification | count |\n|---|---:|")
    for k, v in sorted(A["tally"].items(), key=lambda x: -x[1]):
        parts.append(f"| **{k}** | {v} |")
    parts.append("\n| field | " + " | ".join(sorted(A["tally"])) + " |")
    parts.append("|---|" + "".join("---:|" for _ in A["tally"]))
    for f, c in sorted(A["per_field"].items(), key=lambda x: -sum(x[1].values())):
        parts.append(f"| `{f}` | " + " | ".join(str(c.get(k, 0)) for k in sorted(A["tally"])) + " |")

    dest = os.path.join(HERE, "report_tables.md")
    open(dest, "w").write("\n".join(parts) + "\n")
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
