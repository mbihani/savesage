#!/usr/bin/env python3
"""Render the page-1 identity region so I can SEE whether product name / network
logo exists only as artwork (a hard ceiling for any text-based extractor)."""
import glob
import os
import re

import fitz

PDF_DIR = "/Users/mayanck.bihani/Downloads/output/ICICI/PDF"
OUTD = os.path.dirname(os.path.abspath(__file__))

WANT = ["1737715836", "205034973", "952325284", "232344130", "693462745"]

for path in sorted(glob.glob(PDF_DIR + "/*.pdf")):
    sid = re.match(r"decrypt_(\d+)_", os.path.basename(path)).group(1)
    if sid not in WANT:
        continue
    doc = fitz.open(path)
    page = doc[0]
    # top 40% of page 1 = identity / card-artwork region
    clip = fitz.Rect(0, 0, page.rect.x1, page.rect.y1 * 0.40)
    pix = page.get_pixmap(dpi=140, clip=clip)
    out = os.path.join(OUTD, f"top_{sid}.png")
    pix.save(out)
    print(f"{sid}: {out}  {pix.width}x{pix.height}")
    doc.close()
