#!/usr/bin/env python3
"""Reconstruct the page-1 rewards strip GEOMETRICALLY for all 11 statements.

Deliberately NOT heading-anchored: dump every line in the lower half of page 1
(and page 2 top) so a layout nobody predicted still shows up.
"""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdflib import doc_lines  # noqa: E402

PDF_DIR = "/Users/mayanck.bihani/Downloads/output/ICICI/PDF"
NUM = re.compile(r"^-?[\d,]+(?:\.\d+)?$")

for path in sorted(glob.glob(PDF_DIR + "/*.pdf")):
    sid = re.match(r"decrypt_(\d+)_", os.path.basename(path)).group(1)
    lines, _ = doc_lines(path)
    print("=" * 100)
    print(f"### {sid}")
    print("=" * 100)
    # page 1 lower half + page 2 top: where every ICICI rewards strip observed so far lives
    band = [l for l in lines if (l["page"] == 1 and l["bbox"][1] >= 600)
            or (l["page"] == 2 and l["bbox"][1] <= 140)]
    band.sort(key=lambda l: (l["page"], round(l["bbox"][1], 0), l["bbox"][0]))
    prev_y = None
    for l in band:
        y = round(l["bbox"][1], 1)
        if prev_y is not None and abs(y - prev_y) > 3.0:
            print("   " + "-" * 90)
        isnum = NUM.match(l["text"].strip())
        tag = " [NUM]" if isnum else ""
        print(f"   p{l['page']} y={y:<7} x={l['bbox'][0]:<7} | {l['text'][:78]}{tag}")
        prev_y = y
