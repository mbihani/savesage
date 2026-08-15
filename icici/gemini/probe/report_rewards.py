#!/usr/bin/env python3
"""Report over rewards_v2.json -- show reality, do not trust the classifier."""
import json
import os
import sys

J = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "rewards_v2.json")))
mode = sys.argv[1] if len(sys.argv) > 1 else "closing"

if mode == "closing":
    print("=" * 104)
    print("(b) EVERY 'CLOSING' / 'CLOSING BALANCE' / 'BALANCE' HIT  -- money or points?")
    print("=" * 104)
    for sid, r in J.items():
        print(f"\n### {sid}   MAD-example heads: {len(r['mad_example_heads'])}")
        for m in r["mad_example_heads"][:3]:
            print(f"    MADhead p{m['page']} y={m['bbox'][1]}  {m['text'][:120]}")
        for tok in ["CLOSING BALANCE", "CLOSING"]:
            for h in r["hits"].get(tok, []):
                print(f"  [{tok}] p{h['page']} y={h['bbox'][1]:<7} mode={h['mode']:<6} "
                      f"mad={h['in_mad_boilerplate']}")
                print(f"      money_lex={h['money_lex']} point_lex={h['point_lex']} nums={h['nums_on_line']}")
                print(f"      LINE: {h['line']}")

elif mode == "points":
    print("=" * 104)
    print("(b/d) POINTS / EARNED / REDEEMED / TRANSFERRED / PAYBACK / ISHOP lines")
    print("=" * 104)
    for sid, r in J.items():
        print(f"\n### {sid}")
        for tok in ["TOTAL POINTS EARNED", "POINTS EARNED", "POINTS TRANSFERRED",
                    "POINTS REDEEMED", "REDEEMED", "REWARD POINTS", "PAYBACK", "ISHOP",
                    "MYCASH", "CASHBACK", "EXPIR"]:
            for h in r["hits"].get(tok, []):
                if h["in_mad_boilerplate"]:
                    continue
                print(f"  [{tok:<20}] p{h['page']} y={h['bbox'][1]:<7} mode={h['mode']:<6} "
                      f"nums={h['nums_on_line']}")
                print(f"      LINE: {h['line']}")

elif mode == "clusters":
    for sid, r in J.items():
        print("=" * 104)
        print(f"### {sid}  -- geometric points clusters")
        print("=" * 104)
        for c in r["points_clusters"]:
            if c["in_mad_boilerplate"]:
                continue
            print(f"\n  p{c['page']} y={c['anchor_bbox'][1]} lex={c['lex']}  ANCHOR: {c['anchor']}")
            for b in c["band"]:
                print(f"       x={b['bbox'][0]:<7} y={b['bbox'][1]:<7} | {b['t']}")

elif mode == "ident":
    print("=" * 104)
    print("(f) PAGE-1 TOP STRIP (y<200): text spans + images  -> cardDisplayName evidence")
    print("=" * 104)
    for sid, r in J.items():
        print(f"\n### {sid}")
        print("  IMAGES in top strip:")
        for i in r["page1_top_images"]:
            print(f"    xref={i['xref']:<5} bbox={i['bbox']} px={i['px']}")
        print("  TEXT in top strip:")
        for s in r["page1_top_text"]:
            if not s["text"].strip():
                continue
            print(f"    x={s['bbox'][0]:<7} y={s['bbox'][1]:<7} sz={s['size']:<5} "
                  f"{s['font'][:22]:<24} {s['text']!r}")

elif mode == "prog":
    print("=" * 104)
    print("(c) programType candidates (non-boilerplate)")
    print("=" * 104)
    for sid, r in J.items():
        seen = set()
        print(f"\n### {sid}")
        for p in r["programtype_candidates"]:
            if p["in_mad_boilerplate"]:
                continue
            k = (p["token"], p["page"], tuple(p["bbox"]))
            if k in seen:
                continue
            seen.add(k)
            print(f"  [{p['token']:<19}] p{p['page']} y={p['bbox'][1]:<7} mode={p['mode']:<6} "
                  f"| {p['line'][:120]}")
