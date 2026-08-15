#!/usr/bin/env python3
"""What table is 'Closing Balance' in, and what value sits with it?
Also: is the 26,958.20 specimen present anywhere?"""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdflib import doc_lines  # noqa: E402

PDF_DIR = "/Users/mayanck.bihani/Downloads/output/ICICI/PDF"

for path in sorted(glob.glob(PDF_DIR + "/*.pdf")):
    sid = re.match(r"decrypt_(\d+)_", os.path.basename(path)).group(1)
    lines, _ = doc_lines(path)
    print("=" * 100)
    print(f"### {sid}")
    tgt = [(i, l) for i, l in enumerate(lines) if re.search(r"closing", l["text"], re.I)]
    if not tgt:
        print("  no 'closing' anywhere")
    for i, ln in tgt:
        pg, y = ln["page"], ln["bbox"][1]
        print(f"\n  --- 'closing' at p{pg} y={y} -> context on that page, y in [{y-150:.0f},{y+90:.0f}] ---")
        ctx = [l for l in lines if l["page"] == pg and y - 150 <= l["bbox"][1] <= y + 90]
        ctx.sort(key=lambda l: (l["bbox"][1], l["bbox"][0]))
        for c in ctx:
            mark = " <<<" if c is ln else ""
            print(f"    y={c['bbox'][1]:<7} x={c['bbox'][0]:<7} | {c['text'][:96]}{mark}")
    # the documented specimen value
    spec = [l for l in lines if "26,958.20" in l["text"] or "26958.20" in l["text"].replace(",", "")]
    print(f"\n  26,958.20 specimen present? {bool(spec)}")
    for s in spec:
        print(f"    p{s['page']} y={s['bbox'][1]} | {s['text'][:110]}")
