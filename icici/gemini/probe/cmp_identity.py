#!/usr/bin/env python3
"""Did the cardDisplayName / productFamily convention change land? A vs B vs C vs incumbent."""
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INC = json.load(open(os.path.join(HERE, "probe", "incumbent_vs_ours.json")))


def load(arm):
    d = os.path.join(HERE, f"json_arm{arm}")
    out = {}
    for f in sorted(os.listdir(d)):
        r = json.load(open(os.path.join(d, f)))
        pj = r.get("parsed_json") or {}
        out[r["sid"]] = [((c.get("cardMeta") or {}).get("cardDisplayName"),
                          (c.get("cardMeta") or {}).get("productFamily"))
                         for c in (pj.get("cards") or [])]
    return out


A, B, C = load("A"), load("B"), load("C")
print(f"{'sid':<12} {'ARM A (new)':<40} {'ARM B (prev)':<40}")
print("-" * 96)
for sid in sorted(A):
    a = sorted({x[0] for x in A[sid]}, key=lambda v: (v is None, v))
    b = sorted({x[0] for x in B[sid]}, key=lambda v: (v is None, v))
    c = sorted({x[0] for x in C[sid]}, key=lambda v: (v is None, v))
    mark = "  <-- CHANGED" if a != b else ""
    print(f"{sid:<12} {str(a)[:39]:<40} {str(b)[:39]:<40}{mark}")
    print(f"{'':<12}   armC={str(c)[:38]:<38} incumbent={INC[sid]['inc_names']}")
    fa = sorted({x[1] for x in A[sid]}, key=lambda v: (v is None, v))
    fb = sorted({x[1] for x in B[sid]}, key=lambda v: (v is None, v))
    print(f"{'':<12}   productFamily  A={str(fa)[:34]:<34} B={fb}")
