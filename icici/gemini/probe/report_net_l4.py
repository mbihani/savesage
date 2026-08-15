#!/usr/bin/env python3
"""Human-readable report over net_l4_v2.json."""
import json
import os

J = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "net_l4_v2.json")))

print("=" * 100)
print("(a) NETWORK -- non-disclaimer hits (the only hits that could be card identity)")
print("=" * 100)
for sid, r in J.items():
    nd = r["network_summary"]["non_disclaimer_hits"]
    if not nd:
        continue
    print(f"\n### {sid}  verdict={r['network_summary']['verdict']}")
    for h in nd:
        print(f"  {h['token']:<10} p{h['page']} bbox={h['bbox']} mode={h['mode']}")
        print(f"     LINE: {h['line']}")

print("\n" + "=" * 100)
print("(a) the DISCLAIMER line, verbatim, one example")
print("=" * 100)
for sid, r in J.items():
    for t, hs in r["network_hits"].items():
        for h in hs:
            if h["in_disclaimer"]:
                print(f"{sid} p{h['page']} bbox={h['bbox']}\n  {h['line']}")
                break
        break
    break

print("\n" + "=" * 100)
print("(e) CARD HEADINGS in reading order  +  filename cross-check  +  BIN")
print("=" * 100)
for sid, r in J.items():
    print(f"\n### {sid}")
    print(f"  filename card = {r['filename_card']}  -> last4={r['filename_last4']} "
          f"BIN digit={r['filename_bin_digit']} => {r['filename_bin_network']}")
    print(f"  filename last4 present in PDF text? {r['filename_card_present_in_text']}")
    print(f"  reading order last4: {r['card_last4_reading_order']}   distinct={r['distinct_last4']}")
    for c in r["card_headings"]:
        print(f"    p{c['page']} y={c['bbox'][1]:<7} x={c['bbox'][0]:<7} printed={c['printed']!r:<24} "
              f"last4={c['last4']} spacedmask={c['mask_has_spaces']} BIN={c['bin_digit']}=>{c['bin_network']}")
        print(f"        line: {c['line']}")

print("\n" + "=" * 100)
print("IMAGE LAYER -- top-35% images (card artwork / identity region)")
print("=" * 100)
for sid, r in J.items():
    tops = [i for i in r["images"] if i["in_top35pct"]]
    big = [i for i in tops if i["w"] > 40 and i["h"] > 15]
    print(f"{sid}: total_imgs={len(r['images'])} top35={len(tops)} sizeable_top={len(big)}")
    for i in big[:6]:
        print(f"    p{i['page']} xref={i['xref']} bbox={i['bbox']} {i['w']}x{i['h']}pt px={i['px']}")

print("\n" + "=" * 100)
print("RUPEE SIGN ENCODING")
print("=" * 100)
for sid, r in J.items():
    cps = r["rupee"]["codepoints_near_amount"]
    print(f"\n{sid}: codepoints in amount/rupee spans: {cps}")
    for s in r["rupee"]["amount_header_spans"][:4]:
        print(f"    p{s['page']} font={s['font']:<22} {s['repr']}")
