#!/usr/bin/env python3
"""Inspect the ICICI transaction-table geometry so the row oracle binds columns right."""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdflib import doc_lines  # noqa: E402

want = sys.argv[1] if len(sys.argv) > 1 else "238910814"
path = [p for p in glob.glob("/Users/mayanck.bihani/Downloads/output/ICICI/PDF/*.pdf")
        if want in p][0]
lines, meta = doc_lines(path)
print(f"{want}: pages={meta['n_pages']} rect={meta['page_rects'][0]}")

# find the transaction table header
for ln in lines:
    if re.search(r"Transaction\s+Details|Date.*Ref|SL\.?\s*No", ln["text"], re.I):
        print(f"  HDR p{ln['page']} y={ln['bbox'][1]:<7} x={ln['bbox'][0]:<7} | {ln['text'][:90]}")

# dump the row region of the first transaction page
pg = None
for ln in lines:
    if re.search(r"Transaction\s+Details", ln["text"], re.I):
        pg, hy = ln["page"], ln["bbox"][1]
        break
print(f"\n--- page {pg} rows after y={hy} ---")
rows = [l for l in lines if l["page"] == pg and l["bbox"][1] >= hy - 12]
rows.sort(key=lambda l: (round(l["bbox"][1], 0), l["bbox"][0]))
prev = None
for l in rows[:70]:
    y = round(l["bbox"][1], 1)
    if prev is not None and abs(y - prev) > 2.5:
        print("   " + "-" * 88)
    print(f"   y={y:<7} x={l['bbox'][0]:<7} x1={l['bbox'][2]:<7} | {l['text'][:70]}")
    prev = y
