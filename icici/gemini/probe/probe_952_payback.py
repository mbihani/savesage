#!/usr/bin/env python3
"""952325284 (2018 template): where do 146 / PAYBACK / Points Earned live?
A prior agent claimed 'Points Earned 146 / Points Transferred to PAYBACK 146' is ONE
printed cell double-labelled. Verify directly."""
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdflib import doc_lines  # noqa: E402

path = [p for p in glob.glob("/Users/mayanck.bihani/Downloads/output/ICICI/PDF/*.pdf")
        if "952325284" in p][0]
lines, meta = doc_lines(path)
print(f"pages={meta['n_pages']}  page_rects={meta['page_rects'][:2]}")

print("\n=== every line containing PAYBACK / POINT / EARN / REDEEM / TRANSFER / 146 ===")
for ln in lines:
    t = ln["text"]
    if re.search(r"payback|point|earn|redeem|transfer|\b146\b", t, re.I):
        print(f"  p{ln['page']} y={ln['bbox'][1]:<7} x={ln['bbox'][0]:<7} | {t[:100]}")

print("\n=== 'closing' present? ===")
print([f"p{l['page']} y={l['bbox'][1]} {l['text'][:60]}" for l in lines
       if re.search(r"closing", l["text"], re.I)] or "NONE")

# Full dump of any page region that holds a rewards summary
print("\n=== page 2 full (2018 template puts summaries there) ===")
for ln in sorted([l for l in lines if l["page"] == 2], key=lambda l: (l["bbox"][1], l["bbox"][0])):
    print(f"  y={ln['bbox'][1]:<7} x={ln['bbox'][0]:<7} | {ln['text'][:90]}")
