#!/usr/bin/env python3
"""Extract THE GLARING MISSES: every substantive error on each side, with statement id,
field, both values and the PDF evidence that decided it.

"Substantive" is defined mechanically, not by taste:
  * any statement-level field where the adjudicator returned LUNA_WRONG / CSV_WRONG /
    BOTH_WRONG (i.e. the PDF separated the two sides), or
  * any transaction row present on one side and absent on the other, or
  * any transaction-field verdict that the PDF separated.
AMBIGUOUS_IN_PDF is listed separately and never counted as anyone's error.
"""
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hdfc_lib as H
import score_lib as S

HERE = os.path.dirname(os.path.abspath(__file__))


def load(p):
    fp = os.path.join(HERE, p)
    return json.load(open(fp)) if os.path.exists(fp) else None


def main():
    stmt = load("adjudication_stmt.json") or {"findings": []}
    txn = load("adjudication_txn.json") or {"findings": [], "row_counts": []}

    luna_misses, csv_misses, both, ambig = [], [], [], []

    for f in stmt["findings"]:
        rec = {"level": "statement", "sid": f["sid"], "field": f["field"],
               "luna": f["luna"], "csv": f["csv"], "heldout": f.get("heldout"),
               "evidence": {"luna": f.get("luna_evidence"), "csv": f.get("csv_evidence"),
                            "luna_supported": f.get("luna_supported"),
                            "csv_supported": f.get("csv_supported")}}
        {"LUNA_WRONG": luna_misses, "CSV_WRONG": csv_misses,
         "BOTH_WRONG": both, "AMBIGUOUS_IN_PDF": ambig}[f["verdict"]].append(rec)

    for f in txn["findings"]:
        rec = {"level": "transaction", "sid": f["sid"], "field": f["field"],
               "luna": f["luna"], "csv": f["csv"], "heldout": f.get("heldout"),
               "desc": f.get("luna_desc") or f.get("csv_desc"),
               "evidence": f.get("evidence")}
        {"LUNA_WRONG": luna_misses, "CSV_WRONG": csv_misses,
         "BOTH_WRONG": both, "AMBIGUOUS_IN_PDF": ambig}[f["verdict"]].append(rec)

    # row-count divergences: a dropped or invented row is a substantive miss
    row_l, row_c = [], []
    for rc in txn.get("row_counts", []):
        if rc["csv_only"]:
            row_l.append(rc)      # Luna missing rows the CSV has
        if rc["luna_only"]:
            row_c.append(rc)      # CSV missing rows Luna has

    out = {
        "definition": ("substantive = the PDF separated the two sides "
                       "(LUNA_WRONG / CSV_WRONG / BOTH_WRONG). AMBIGUOUS_IN_PDF is "
                       "listed separately and charged to neither side."),
        "counts": {
            "luna_substantive_errors": len(luna_misses),
            "incumbent_substantive_errors": len(csv_misses),
            "both_wrong": len(both),
            "ambiguous_in_pdf": len(ambig),
            "statements_where_luna_lacks_rows_csv_has": len(row_l),
            "statements_where_csv_lacks_rows_luna_has": len(row_c),
        },
        "counts_heldout": {
            "luna_substantive_errors": sum(1 for x in luna_misses if x.get("heldout")),
            "incumbent_substantive_errors": sum(1 for x in csv_misses if x.get("heldout")),
        },
        "by_field": {
            "luna": dict(Counter(x["field"] for x in luna_misses)),
            "incumbent": dict(Counter(x["field"] for x in csv_misses)),
        },
        "luna_errors": luna_misses,
        "incumbent_errors": csv_misses,
        "both_wrong": both,
        "ambiguous": ambig[:200],
        "row_divergence_luna_short": row_l,
        "row_divergence_csv_short": row_c,
    }
    H.G.atomic_write_json(os.path.join(HERE, "glaring_misses.json"), out)

    print("GLARING MISSES")
    for k, v in out["counts"].items():
        print(f"  {k:48s} {v}")
    print("\n  by field — LUNA:     ", out["by_field"]["luna"])
    print("  by field — INCUMBENT:", out["by_field"]["incumbent"])
    print("\n  held-out only:", out["counts_heldout"])


if __name__ == "__main__":
    main()
