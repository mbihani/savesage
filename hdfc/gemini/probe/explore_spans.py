"""Discovery pass: what do HDFC transaction-amount spans actually look like?

Written before the real probe so the probe is built on observed structure, not on an
assumed layout. Prints, for one PDF: the font inventory, and every span that looks
like a money amount together with its font, colour and bbox.
"""
import re
import sys

import fitz

AMT = re.compile(r"[\d][\d,]*\.\d{2}")


def main(path):
    doc = fitz.open(path)
    fonts = {}
    print(f"=== {path.rsplit('/', 1)[-1]}  pages={len(doc)}")
    for pno in range(len(doc)):
        page = doc[pno]
        d = page.get_text("dict")
        for blk in d.get("blocks", []):
            for ln in blk.get("lines", []):
                for sp in ln.get("spans", []):
                    fonts[sp["font"]] = fonts.get(sp["font"], 0) + 1
    print("--- font inventory ---")
    for f, n in sorted(fonts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:6d}  {f}")

    print("--- amount-looking spans (first 40, pages 1-3) ---")
    shown = 0
    for pno in range(min(3, len(doc))):
        page = doc[pno]
        for blk in page.get_text("dict").get("blocks", []):
            for ln in blk.get("lines", []):
                spans = ln.get("spans", [])
                txts = [s["text"] for s in spans]
                for i, sp in enumerate(spans):
                    if not AMT.search(sp["text"]):
                        continue
                    if shown >= 40:
                        break
                    shown += 1
                    print(f"  p{pno+1} x={sp['bbox'][0]:7.1f} y={sp['bbox'][1]:7.1f} "
                          f"col=0x{sp['color']:06x} font={sp['font']:<22} "
                          f"txt={sp['text']!r}")
                    print(f"        LINE={txts!r}")
    doc.close()


if __name__ == "__main__":
    main(sys.argv[1])
