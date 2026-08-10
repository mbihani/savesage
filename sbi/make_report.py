#!/usr/bin/env python3
"""Assemble SBI_REPORT.md from the measured artifacts. No numbers are typed by hand:
everything comes from scores_*.json / adjudication*.json / tokens.json / phase2_measured.json.
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import score_lib_sbi as S  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
NICE = {
    "cards[].cardMeta.cardDisplayName": "cardDisplayName",
    "cards[].cardMeta.lastFourDigit": "lastFourDigit",
    "cards[].cardMeta.network": "network",
    "statementLevelSummary.totalAmountDue": "sls.totalAmountDue",
    "statementLevelSummary.availableCreditLimit": "sls.availableCreditLimit",
    "statementLevelSummary.utilisationPercent": "sls.utilisationPercent",
    "statementLevelSummary.totalCreditLimit": "sls.totalCreditLimit",
    "statementLevelSummary.totalMinimumAmountDue": "sls.totalMinimumAmountDue",
    "statementMeta.issuerName": "meta.issuerName",
    "statementMeta.statementDate": "meta.statementDate",
    "statementMeta.dueDate": "meta.dueDate",
    "transactions[].date": "txn.date",
    "transactions[].description": "txn.description",
    "transactions[].amount": "txn.amount",
    "transactions[].direction": "txn.direction",
    "transactions[].currency": "txn.currency",
}


def _local_score_module():
    """Load THIS directory's score.py by absolute path.

    A bare `import score` resolves to /Users/.../bakeoff/scorer/score.py -- the
    canonical normaliser module, which score_lib_sbi puts FIRST on sys.path and which
    has no `aggregate`. Loading by file location removes the ambiguity.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "sbi_score_local", os.path.join(ROOT, "score.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def pct(v):
    return "n/a" if v is None else f"{v:.1f}"


def field_table(blk, note_map=None):
    L = ["| field | n | correct % | wrong | null_when_populated | hallucinated_when_GT_null | both_null (excl.) |",
         "|---|---:|---:|---:|---:|---:|---:|"]
    for f, nm in NICE.items():
        v = blk["priority"].get(f)
        if v is None:
            continue
        flag = (note_map or {}).get(f, "")
        L.append(f"| `{nm}`{flag} | {v['n_compared']} | {pct(v['pct'])} | "
                 f"{v['wrong_value']} | {v['null_when_populated']} | "
                 f"{v['hallucinated_when_null']} | {v['both_null']} |")
    return "\n".join(L)


def txn_table(name, blk):
    t = blk["txn"]
    return (f"| {name} | {t['n_ref_total']} | {t['n_pred_total']} | {t['matched']} | "
            f"{pct((t['precision'] or 0) * 100)} | {pct((t['recall'] or 0) * 100)} | "
            f"{pct((t['f1'] or 0) * 100)} | {t['unmatched_ref']} | {t['unmatched_pred']} | "
            f"{t['desc_exact']} ({pct(100 * t['desc_exact'] / t['matched']) if t['matched'] else 'n/a'}%) |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores", default=os.path.join(ROOT, "scores_refined.json"))
    ap.add_argument("--scores-client", default=os.path.join(ROOT, "scores_client_full.json"))
    ap.add_argument("--adj", default=os.path.join(ROOT, "adjudication_refined.json"))
    ap.add_argument("--tokens", default=os.path.join(ROOT, "tokens.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "REPORT_TABLES.md"))
    a = ap.parse_args()

    sc = json.load(open(a.scores))
    tok = json.load(open(a.tokens))
    adj = json.load(open(a.adj)) if os.path.exists(a.adj) else None
    scc = json.load(open(a.scores_client)) if os.path.exists(a.scores_client) else None

    O = []
    w = O.append
    n_all = sc["summary"]["luna_vs_gt"]["all"]["n_statements"]
    n_held = sc["summary"]["luna_vs_gt"]["held_out"]["n_statements"]

    w(f"### Scoreable set\n")
    e = sc["exclusions"]
    w(f"- PDFs discovered: **{e['pdf_total']}**")
    w(f"- CSV rows joining a PDF: **{e['csv_join']}**")
    w(f"- excluded, no CSV row: {len(e['pdf_without_csv_row'])}")
    w(f"- excluded, GT missing/unusable: {len(e['gt_missing_or_unusable'])}")
    w(f"- excluded, Luna not run: {len(e['luna_not_run'])}")
    w(f"- **scoreable: {n_all}**   held-out (minus 10 tuning): **{n_held}**\n")

    notes = {"cards[].cardMeta.network": " ⚠",
             "statementLevelSummary.utilisationPercent": " ⚠",
             "statementMeta.issuerName": " ⚠",
             "transactions[].currency": " ⚠",
             "cards[].cardMeta.cardDisplayName": " ⚠"}

    for ref, title in (("luna_vs_gt", "Luna (refined) vs Opus-5 GT — ACCURACY"),
                       ("csv_vs_gt", "Incumbent CSV vs Opus-5 GT — the incumbent's OWN accuracy"),
                       ("luna_vs_csv", "Luna (refined) vs incumbent CSV — AGREEMENT, not correctness")):
        for scope, lbl in (("all", f"all statements (n={n_all})"),
                           ("held_out", f"HELD-OUT only (n={n_held})")):
            w(f"### {title} — {lbl}\n")
            w(field_table(sc["summary"][ref][scope], notes))
            u = sc["summary"][ref][scope]["utilisation_derived"][
                "statementLevelSummary.utilisationPercent"]
            w(f"\n`utilisationPercent` **as-derived** (same formula all three sources): "
              f"n={u['n_compared']}, correct={pct(u['pct'])}%, wrong={u['wrong_value']}, "
              f"both_null={u['both_null']}\n")

    w("### Transactions — precision / recall / F1 / description fidelity\n")
    w("| comparison | ref rows | pred rows | matched | P % | R % | F1 % | recall misses | false positives | description byte-exact |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for ref, nm in (("luna_vs_gt", "Luna vs GT (all)"), ("csv_vs_gt", "CSV vs GT (all)"),
                    ("luna_vs_csv", "Luna vs CSV (all)")):
        w(txn_table(nm, sc["summary"][ref]["all"]))
    for ref, nm in (("luna_vs_gt", "Luna vs GT (held-out)"),
                    ("csv_vs_gt", "CSV vs GT (held-out)"),
                    ("luna_vs_csv", "Luna vs CSV (held-out)")):
        w(txn_table(nm, sc["summary"][ref]["held_out"]))
    w("")

    if scc:
        w("### Refinement lift — refined vs CLIENT-baseline prompt, same statements\n")
        ids = sorted(set(sc["scoreable_ids"]) & set(scc["scoreable_ids"]))
        w(f"Common scoreable set: **{len(ids)}** statements.\n")
        w("| field | baseline (client prompt) correct % | refined correct % | delta |")
        w("|---|---:|---:|---:|")
        SC = _local_score_module()
        cb = [c for c in scc["cells"] if c["ref"] == "luna_vs_gt" and c["statement_id"] in set(ids)]
        cr = [c for c in sc["cells"] if c["ref"] == "luna_vs_gt" and c["statement_id"] in set(ids)]
        ab, ar = SC.aggregate(cb, S.PRIORITY), SC.aggregate(cr, S.PRIORITY)
        for f, nm in NICE.items():
            b, r = ab.get(f), ar.get(f)
            if not b or not r or b["pct"] is None or r["pct"] is None:
                continue
            w(f"| `{nm}` | {pct(b['pct'])} | {pct(r['pct'])} | "
              f"{r['pct'] - b['pct']:+.1f} |")
        tb = SC.txn_block([t for t in scc["txn_per_statement"]
                           if t["ref"] == "luna_vs_gt" and t["statement_id"] in set(ids)])
        tr = SC.txn_block([t for t in sc["txn_per_statement"]
                           if t["ref"] == "luna_vs_gt" and t["statement_id"] in set(ids)])
        w(f"| **txn recall** | {pct((tb['recall'] or 0) * 100)} | "
          f"{pct((tr['recall'] or 0) * 100)} | "
          f"{((tr['recall'] or 0) - (tb['recall'] or 0)) * 100:+.1f} |")
        w(f"| **txn rows emitted** | {tb['n_pred_total']} | {tr['n_pred_total']} | "
          f"{tr['n_pred_total'] - tb['n_pred_total']:+d} |")
        w("")

    if adj:
        w("### Adjudication of Luna-vs-incumbent disagreements against the PDF\n")
        w(f"{adj['n_statements']} statements, **{len(adj['items'])}** disagreements adjudicated.\n")
        w("| verdict | count |")
        w("|---|---:|")
        for k, v in adj["tally"].items():
            w(f"| {k} | {v} |")
        w("\n| field | verdicts |")
        w("|---|---|")
        for f, c in sorted(adj["per_field"].items()):
            w(f"| `{f}` | " + ", ".join(f"{k}={v}" for k, v in
                                        sorted(c.items(), key=lambda x: -x[1])) + " |")
        w("")

    w("### Token accounting (from VERBATIM persisted `usage` blocks)\n")
    w("| arm | n | input sum | input mean | output sum | output mean | output median | output max | reasoning sum | total sum |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for nm, v in tok.items():
        i, o, t, r = v["input"], v["output"], v["total"], v["reasoning"]
        w(f"| {nm} | {v['n_records']} | {i['sum']:,} | {i['mean']:,.0f} | {o['sum']:,} | "
          f"{o['mean']:,.0f} | {o['median']:,.0f} | {o['max']:,} | "
          f"{(r['sum'] if r else 0):,} | {t['sum']:,} |")
    w("")
    w("| arm | prompt+completion==total | records w/ reasoning>0 | reasoning inside completion? | truncated | 429s | attempts>1 |")
    w("|---|---|---:|---|---:|---:|---:|")
    for nm, v in tok.items():
        idn = v["prompt_plus_completion_equals_total"]
        w(f"| {nm} | {idn['holds']}/{idn['holds'] + idn['fails']} ({idn['pct']}%) | "
          f"{v['records_with_reasoning_gt_0']} | {v['reasoning_inside_completion']} | "
          f"{len(v['truncated_records'])} | {v['rate_limited_calls']} | {v['attempts_gt_1']} |")
    w("")
    for nm, v in tok.items():
        w(f"- **{nm} cost**: {v['cost_usd'] if isinstance(v['cost_usd'], str) else json.dumps(v['cost_usd'])}")
    w("")

    w("### Outcome tally\n")
    w("| arm | " + " | ".join(sorted({k for v in tok.values() for k in v["outcomes"]})) + " |")
    keys = sorted({k for v in tok.values() for k in v["outcomes"]})
    w("|---|" + "---:|" * len(keys))
    for nm, v in tok.items():
        w(f"| {nm} | " + " | ".join(str(v["outcomes"].get(k, 0)) for k in keys) + " |")
    w("")

    open(a.out, "w").write("\n".join(O))
    print("\n".join(O))
    print("\nwrote", a.out)


if __name__ == "__main__":
    main()
