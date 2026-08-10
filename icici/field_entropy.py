#!/usr/bin/env python3
"""Measure how DISCRIMINATING each of the 16 priority fields actually is, in the GT.

A field whose top value covers ~100% of instances is trivially solved: a 100% score on it
is evidence the field is near-constant, NOT evidence the model earned it. This is measured
rather than asserted so the report's "do not misread this score" flags carry numbers.
"""
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import icici_lib as L
import score_lib as S


def main():
    gt = S.load_arm(os.path.join(L.HERE, "opus_gt"))
    vals = collections.defaultdict(collections.Counter)
    for _sid, r in gt.items():
        p = S.model_as_extraction(r)
        if not p:
            continue
        for f in S.PRIORITY:
            leaf = f.split(".")[-1]
            if f.startswith("cards[]"):
                for c in (p.get("cards") or []):
                    vals[f][str((c.get("cardMeta") or {}).get(leaf))] += 1
            elif f.startswith("transactions[]"):
                for t in (p.get("transactions") or []):
                    vals[f][str(t.get(leaf))] += 1
            else:
                vals[f][str(S.dig(p, f))] += 1

    out = {}
    for f in S.PRIORITY:
        c = vals[f]
        n = sum(c.values())
        if not n:
            continue
        top, tc = c.most_common(1)[0]
        share = tc / n
        out[f] = {"n": n, "distinct_values": len(c), "top_value": top,
                  "top_value_share": round(share, 4),
                  "verdict": ("TRIVIALLY_SOLVED (near-constant)" if share >= 0.95 else
                              "LOW_DISCRIMINATION" if share >= 0.80 else "DISCRIMINATING")}
    dest = os.path.join(L.HERE, "field_entropy.json")
    json.dump(out, open(dest, "w"), indent=1)
    print(f"wrote {dest}\n")
    print(f"{'field':<50}{'n':>6}{'distinct':>10}{'top share':>11}  verdict")
    for f, d in out.items():
        print(f"{f:<50}{d['n']:>6}{d['distinct_values']:>10}"
              f"{d['top_value_share']*100:>10.1f}%  {d['verdict']}")


if __name__ == "__main__":
    main()
