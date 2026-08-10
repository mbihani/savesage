#!/usr/bin/env python3
"""Decompose transactions[].description defects into severity classes.

A single accuracy number for `description` is misleading on this corpus: most mismatches are
pure text-fidelity slips (Luna closing the PDF's intra-cell line-wrap gaps, or dropping a
trailing country code) rather than corrupted content. This splits them so the report can say
which, with counts, instead of quoting one blended figure.
"""
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import icici_lib as L

SQ = lambda s: re.sub(r"\s+", "", str(s or "")).casefold()


def main():
    d = json.load(open(os.path.join(L.HERE, "scores_phase3.json")))
    out = {}
    for comp in ("luna_refined_vs_GT__all", "CSV_vs_GT__all"):
        c = d["comparisons"].get(comp)
        if not c:
            continue
        cls = collections.Counter()
        by_stmt = collections.Counter()
        others = []
        total = 0
        for st in c["per_statement"]:
            for r in st["fields"].get("transactions[].description", []):
                total += 1
                if r["verdict"] == "correct":
                    continue
                a, b = SQ(r["pred"]), SQ(r["ref"])
                if a == b:
                    cls["spacing_only"] += 1
                elif b == a + "in":
                    cls["dropped_trailing_country_code"] += 1
                    by_stmt[st["statement_id"]] += 1
                elif a == b + "in":
                    cls["added_trailing_country_code"] += 1
                else:
                    cls["real_character_difference"] += 1
                    others.append({"statement_id": st["statement_id"],
                                   "pred": r["pred"], "ref": r["ref"]})
        out[comp] = {
            "rows_compared": total,
            "defects": sum(cls.values()),
            "classes": dict(cls),
            "fidelity_only_share_of_defects": round(
                (cls["spacing_only"] + cls["dropped_trailing_country_code"]
                 + cls["added_trailing_country_code"]) / max(1, sum(cls.values())), 4),
            "dropped_country_code_by_statement": dict(by_stmt.most_common()),
            "real_character_differences": others,
        }
    dest = os.path.join(L.HERE, "desc_defect_classes.json")
    json.dump(out, open(dest, "w"), indent=1, default=str)
    print(f"wrote {dest}\n")
    for k, v in out.items():
        print(f"=== {k}: {v['defects']} defects of {v['rows_compared']} rows")
        for c, n in v["classes"].items():
            print(f"    {n:>5}  {c}")
        print(f"    fidelity-only share of defects: {v['fidelity_only_share_of_defects']*100:.1f}%")
        if v["dropped_country_code_by_statement"]:
            print(f"    dropped-IN concentrated in: {v['dropped_country_code_by_statement']}")


if __name__ == "__main__":
    main()
