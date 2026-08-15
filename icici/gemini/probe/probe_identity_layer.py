#!/usr/bin/env python3
"""Is the page-1 identity header (product name) text, raster, or VECTOR?
Decides whether any cardDisplayName rule is even expressible for a text extractor."""
import glob
import os
import re

import fitz

PDF_DIR = "/Users/mayanck.bihani/Downloads/output/ICICI/PDF"
PROD = ["SAPPHIRO", "CORAL", "RUBYX", "RUBIX", "PLATINUM", "AMAZON", "MAKEMYTRIP", "HPCL",
        "EMERALDE", "EXPRESSIONS", "MINE", "MANU", "ADANI"]

for path in sorted(glob.glob(PDF_DIR + "/*.pdf")):
    sid = re.match(r"decrypt_(\d+)_", os.path.basename(path)).group(1)
    doc = fitz.open(path)
    p1 = doc[0]
    H = p1.rect.height
    # identity band = top-right quadrant of page 1
    band = fitz.Rect(p1.rect.x1 * 0.45, 0, p1.rect.x1, min(140, H * 0.18))
    txt = p1.get_text("text", clip=band)
    rasters = [im for im in p1.get_images(full=True)
               for rc in (p1.get_image_rects(im[0]) or []) if rc.intersects(band)]
    drawings = [d for d in p1.get_drawings() if fitz.Rect(d["rect"]).intersects(band)]
    # any product token anywhere in the whole doc's TEXT layer?
    alltext = "".join(pg.get_text() for pg in doc).upper()
    alltext_ns = re.sub(r"\s+", "", alltext)
    prod_in_text = [p for p in PROD if p in alltext_ns]
    print(f"### {sid}")
    print(f"    identity-band TEXT: {txt.strip()[:70]!r}")
    print(f"    identity-band rasters={len(rasters)}  VECTOR drawings={len(drawings)}")
    print(f"    product tokens present anywhere in TEXT layer: {prod_in_text}")
    doc.close()
