"""Discovery pass 2: how are HDFC transaction rows and their CREDIT markers encoded?

Groups spans into visual rows by y-coordinate and prints each row's spans with font
and colour, so the '+' marker and the green 0x05c747 credit colour can be seen
directly rather than assumed.
"""
import re
import sys
from collections import defaultdict

import fitz

AMT = re.compile(r"[\d][\d,]*\.\d{2}")


def rows_on_page(page, ytol=2.0):
    buckets = defaultdict(list)
    for blk in page.get_text("dict").get("blocks", []):
        for ln in blk.get("lines", []):
            for sp in ln.get("spans", []):
                if not sp["text"].strip():
                    continue
                key = round(sp["bbox"][1] / ytol)
                buckets[key].append(sp)
    out = []
    for k in sorted(buckets):
        spans = sorted(buckets[k], key=lambda s: s["bbox"][0])
        out.append((spans[0]["bbox"][1], spans))
    return out


def main(path, maxrows=45):
    doc = fitz.open(path)
    shown = 0
    for pno in range(len(doc)):
        page = doc[pno]
        for y, spans in rows_on_page(page):
            joined = "".join(s["text"] for s in spans)
            if not AMT.search(joined):
                continue
            if shown >= maxrows:
                break
            shown += 1
            print(f"p{pno+1} y={y:7.1f} | " + " ".join(
                f"[{s['text']!r} {s['font'].split(',')[0][:12]} 0x{s['color']:06x}]"
                for s in spans))
    doc.close()


if __name__ == "__main__":
    main(sys.argv[1])
