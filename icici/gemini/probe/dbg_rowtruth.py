#!/usr/bin/env python3
"""Why are 952325284's row descriptions blank? Print the detected column geometry."""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pdflib import doc_lines  # noqa: E402
import pdf_rowtruth as R  # noqa: E402

path = [p for p in glob.glob("/Users/mayanck.bihani/Downloads/output/ICICI/PDF/*.pdf")
        if "952325284" in p][0]
lines, _ = doc_lines(path)
for pg, hy, hdr in R.page_tables(lines):
    print(f"page {pg} hy={hy}")
    for k, v in hdr.items():
        print(f"   {k:6s} x0={v[0]:<8} y={v[1]}")
    rpx0 = hdr["rp"][0] if "rp" in hdr else None
    amtx0 = hdr["amt"][0] if "amt" in hdr else hdr["desc"][0] + 200
    lo = hdr["desc"][0] - 40
    hi = (rpx0 - 12 if rpx0 else amtx0 - 12)
    print(f"   => desc window [{lo}, {hi})   amtx0={amtx0} rpx0={rpx0}")
    # what lines fall in a real row band?
    ls = [l for l in lines if l["page"] == pg and l["bbox"][1] > hy + 4]
    for l in ls[:14]:
        inwin = lo <= l["bbox"][0] < hi
        print(f"      y={l['bbox'][1]:<8} x={l['bbox'][0]:<8} inwin={inwin} | {l['text'][:44]}")
    break
