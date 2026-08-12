"""Discovery pass 3: locate the CREDIT markers ('+' span, green colour) across the corpus.

Reports, per PDF: the distinct span colours seen on amount spans, and every row that
carries a '+' span, so the credit encoding is observed rather than assumed. Also
prints the Pixel Play layout separately since those files lack ITFRupee.
"""
import os
import re
import sys
from collections import Counter, defaultdict

import fitz

PDF_DIR = "/Users/mayanck.bihani/Downloads/output/HDFC/PDF"
AMT = re.compile(r"^\s*[\d][\d,]*\.\d{2}\s*$")
DATED = re.compile(r"^\s*\d{2}/\d{2}/\d{4}")


def rows(page, ytol=2.0):
    b = defaultdict(list)
    for blk in page.get_text("dict").get("blocks", []):
        for ln in blk.get("lines", []):
            for sp in ln.get("spans", []):
                if sp["text"].strip():
                    b[round(sp["bbox"][1] / ytol)].append(sp)
    for k in sorted(b):
        yield sorted(b[k], key=lambda s: s["bbox"][0])


def main():
    for fn in sorted(os.listdir(PDF_DIR)):
        if not fn.lower().endswith(".pdf"):
            continue
        doc = fitz.open(os.path.join(PDF_DIR, fn))
        fonts = Counter()
        amt_colors = Counter()
        plus_rows = []
        dated = 0
        for pno in range(len(doc)):
            for spans in rows(doc[pno]):
                for s in spans:
                    fonts[s["font"]] += 1
                joined = "".join(s["text"] for s in spans)
                if DATED.match(joined):
                    dated += 1
                    for s in spans:
                        if AMT.match(s["text"]):
                            amt_colors[f"0x{s['color']:06x}"] += 1
                if any(s["text"].strip() == "+" for s in spans) and DATED.match(joined):
                    plus_rows.append((pno + 1, [(s["text"], s["font"].split(",")[0],
                                                 f"0x{s['color']:06x}") for s in spans]))
        itf = sum(v for k, v in fonts.items() if "ITFRupee" in k)
        print(f"\n=== {fn[:70]}")
        print(f"    ITFRupee spans={itf}  dated_rows={dated}  amt_colors={dict(amt_colors)}")
        for pno, spans in plus_rows[:4]:
            print(f"    +ROW p{pno}: {spans}")
        if not plus_rows:
            print("    (no '+' span on any dated row)")
        doc.close()


if __name__ == "__main__":
    main()
