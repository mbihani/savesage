#!/usr/bin/env python3
"""ICICI measurement: rewards geometry (b closingPoints, c programType, d pointsRedeemed)
plus the page-1 identity region (f cardDisplayName).

Method notes:
  * Layout discovery is GEOMETRIC, not heading-whitelisted. On a sibling bank three
    heading-anchored analyses were each wrong in a different direction because the
    真 layout was one nobody had listed. So: cluster every page's spans into rows,
    keep any row-cluster containing a points/cashback/reward lexeme OR sitting in a
    table whose header does, and report ALL of them.
  * The documented ICICI trap: a PRE-PRINTED illustrative Minimum-Amount-Due worked
    example containing a FIXED specimen "Closing Balance" (26,958.20 on an earlier
    corpus). Every CLOSING hit is tested for containment in that example.
"""
import glob
import json
import os
import re
import sys

import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdflib import doc_lines, find_token, numbers_in, parse_indian_num  # noqa: E402

PDF_DIR = "/Users/mayanck.bihani/Downloads/output/ICICI/PDF"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rewards_v2.json")

POINT_LEX = ["POINT", "REWARD", "CASHBACK", "CASH BACK", "MYCASH", "PAYBACK", "ISHOP"]
MONEY_LEX = ["BALANCE", "AMOUNT", "DUE", "CREDIT LIMIT", "PURCHASE", "PAYMENT", "CHARGE"]
# heading that opens the pre-printed illustrative MAD worked example
MAD_HEAD = re.compile(r"following\s+Minimum\s+Amount\s+Due\s+is\s+calculated", re.I)
MAD_HEAD2 = re.compile(r"On\s+statement\s+dated", re.I)


def has_lex(t, lex):
    u = re.sub(r"\s+", "", t.upper())
    return [k for k in lex if re.sub(r"\s+", "", k) in u]


def main():
    res = {}
    for path in sorted(glob.glob(PDF_DIR + "/*.pdf")):
        base = os.path.basename(path)
        sid = re.match(r"decrypt_(\d+)_", base).group(1)
        lines, meta = doc_lines(path)
        r = {"filename": base, "n_pages": meta["n_pages"]}

        # ---------- locate the pre-printed MAD worked example ----------
        mad = []
        for li, ln in enumerate(lines):
            if MAD_HEAD.search(ln["text"]) or MAD_HEAD2.search(ln["text"]):
                mad.append({"line_idx": li, "page": ln["page"], "bbox": ln["bbox"],
                            "text": ln["text"][:150]})
        r["mad_example_heads"] = mad
        # the example runs from its heading to ~the end of that page's table
        mad_pages = {m["page"] for m in mad}
        mad_ymin = {m["page"]: m["bbox"][1] for m in mad}

        def in_mad(ln):
            if ln["page"] not in mad_pages:
                return False
            return ln["bbox"][1] >= mad_ymin[ln["page"]] - 2

        # ---------- (b) CLOSING / BALANCE / OPENING / POINTS hits ----------
        hits = {}
        for tok in ["CLOSING BALANCE", "CLOSING", "OPENING BALANCE", "OPENING", "BALANCE",
                    "REWARD POINTS", "POINTS", "TOTAL POINTS EARNED", "POINTS EARNED",
                    "POINTS TRANSFERRED", "PAYBACK", "ISHOP", "MYCASH", "CASHBACK",
                    "POINTS REDEEMED", "REDEEMED", "EXPIR"]:
            hs = find_token(lines, tok)
            if not hs:
                continue
            out = []
            for h in hs:
                ln = lines[h["line_idx"]]
                nums = numbers_in(ln["text"])
                out.append({
                    "page": h["page"], "mode": h["mode"], "bbox": h["bbox"],
                    "in_mad_boilerplate": in_mad(ln),
                    "money_lex": has_lex(ln["text"], MONEY_LEX),
                    "point_lex": has_lex(ln["text"], POINT_LEX),
                    "nums_on_line": [n[0] for n in nums][:8],
                    "line": ln["text"][:180],
                })
            hits[tok] = out
        r["hits"] = hits

        # specimen value in the MAD example
        spec = []
        for li, ln in enumerate(lines):
            if in_mad(ln) and re.search(r"closing", ln["text"], re.I):
                spec.append({"page": ln["page"], "bbox": ln["bbox"], "line": ln["text"][:180],
                             "nums": [n[0] for n in numbers_in(ln["text"])]})
        r["mad_closing_specimen"] = spec

        # ---------- (b/d) GEOMETRIC layout catalogue: every points-ish row cluster ----------
        clusters = []
        for li, ln in enumerate(lines):
            pl = has_lex(ln["text"], POINT_LEX)
            if not pl:
                continue
            # gather everything within +-26pt vertically on the same page (the row band)
            band = [l2 for l2 in lines
                    if l2["page"] == ln["page"] and abs(l2["bbox"][1] - ln["bbox"][1]) <= 26]
            band.sort(key=lambda l2: l2["bbox"][0])
            clusters.append({
                "page": ln["page"], "anchor_bbox": ln["bbox"], "anchor": ln["text"][:110],
                "lex": pl, "in_mad_boilerplate": in_mad(ln),
                "band": [{"bbox": b["bbox"], "t": b["text"][:70]} for b in band][:14],
            })
        r["points_clusters"] = clusters

        # ---------- (c) programType candidates ----------
        prog = []
        for tok in ["REWARD POINTS", "PAYBACK POINTS", "PAYBACK", "MYCASH", "MY CASH",
                    "CASHBACK", "CASH BACK", "MEMBERSHIP REWARDS", "REWARDS"]:
            for h in find_token(lines, tok):
                ln = lines[h["line_idx"]]
                prog.append({"token": tok, "page": h["page"], "mode": h["mode"],
                             "bbox": h["bbox"], "in_mad_boilerplate": in_mad(ln),
                             "line": ln["text"][:160]})
        r["programtype_candidates"] = prog

        # ---------- (f) page-1 identity region: top strip text + images ----------
        doc = fitz.open(path)
        p1 = doc[0]
        ident_text = []
        for blk in p1.get_text("dict")["blocks"]:
            if blk.get("type") != 0:
                continue
            for ln2 in blk["lines"]:
                for sp in ln2["spans"]:
                    bb = sp["bbox"]
                    if bb[1] < 200:  # top strip
                        ident_text.append({"bbox": [round(v, 2) for v in bb],
                                           "font": sp.get("font"), "size": round(sp.get("size", 0), 1),
                                           "text": sp["text"]})
        ident_text.sort(key=lambda s: (s["bbox"][1], s["bbox"][0]))
        r["page1_top_text"] = ident_text
        r["page1_top_images"] = [
            {"xref": im[0], "px": f"{im[2]}x{im[3]}",
             "bbox": [round(v, 2) for v in rc] }
            for im in p1.get_images(full=True)
            for rc in (p1.get_image_rects(im[0]) or [])
            if rc.y0 < 200]
        doc.close()

        res[sid] = r
        n_cl = len([c for c in clusters if not c["in_mad_boilerplate"]])
        print(f"{sid}: mad_heads={len(mad)} specimen={[s['nums'] for s in spec]} "
              f"points_clusters(non-boilerplate)={n_cl}")

    with open(OUT, "w") as f:
        json.dump(res, f, indent=1)
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
