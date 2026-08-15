#!/usr/bin/env python3
"""ICICI measurement: network tokens (a), image layer, lastFourDigit (e), rupee sign.

Supersedes probe/measure_all.py, which had three defects that fabricate numbers:
  1. page.search_for() called per regex match -> quadratic double counting.
  2. no word bounding -> "VISA" matched inside "VISAKHAPATNAM".
  3. not whitespace-flexible, though ICICI wraps tokens mid-word.
"""
import glob
import json
import os
import re
import sys

import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pdflib import doc_lines, find_token, page_lines  # noqa: E402

PDF_DIR = "/Users/mayanck.bihani/Downloads/output/ICICI/PDF"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "net_l4_v2.json")

NETWORKS = ["VISA", "MASTERCARD", "MASTER CARD", "RUPAY", "AMERICAN EXPRESS", "AMEX",
            "DINERS", "DISCOVER", "MAESTRO"]
# Card-number heading: 4 digits, masked middle (8+ X, optionally space-grouped), 4 trailing chars.
CARD_RE = re.compile(r"\b(\d{4})[\s]*((?:[Xx]{4}[\s]*){2,3})([0-9Xx]{4})\b")
BIN_NET = {"3": "AMEX/DINERS", "4": "VISA", "5": "MASTERCARD", "6": "RUPAY/DISCOVER", "0": "UNKNOWN(0)"}


def selftest():
    """Negative-test the word-bounding: it MUST NOT match VISA inside VISAKHAPATNAM."""
    fake = [{"page": 1, "block": 0, "line": 0, "text": "SWIGGY VISAKHAPATNAM IN", "bbox": [0, 0, 1, 1],
             "words": [{"bbox": [0, 0, 1, 1], "w": w} for w in "SWIGGY VISAKHAPATNAM IN".split()]},
            {"page": 1, "block": 0, "line": 1, "text": "For RuPay/American Express/ Visa/Mastercard Credit Cards",
             "bbox": [0, 0, 1, 1],
             "words": [{"bbox": [0, 0, 1, 1], "w": w}
                       for w in "For RuPay/American Express/ Visa/Mastercard Credit Cards".split()]}]
    a = find_token(fake, "VISA")
    assert all("VISAKHAPATNAM" not in h["line"] or h["mode"] == "LOOSE" for h in a), a
    strict_lines = {h["line_idx"] for h in a if h["mode"] == "STRICT"}
    assert 0 not in strict_lines, f"word-bounding FAILED: matched inside VISAKHAPATNAM -> {a}"
    assert 1 in strict_lines, f"failed to find Visa in the disclaimer -> {a}"
    print("[selftest] word-bounding OK (rejects VISAKHAPATNAM, accepts disclaimer 'Visa/Mastercard')")


def is_disclaimer(line):
    """A line that lists MULTIPLE networks together identifies nothing."""
    u = re.sub(r"\s+", "", line.upper())
    n = sum(t in u for t in ["VISA", "MASTERCARD", "RUPAY", "AMERICANEXPRESS"])
    return n >= 2


def main():
    selftest()
    res = {}
    for path in sorted(glob.glob(PDF_DIR + "/*.pdf")):
        base = os.path.basename(path)
        sid = re.match(r"decrypt_(\d+)_", base).group(1)
        lines, meta = doc_lines(path)
        r = {"filename": base, "n_pages": meta["n_pages"], "page_rects": meta["page_rects"]}

        # ---- filename card number + BIN ----
        m = re.search(r"_(\d{4}(?:\s?[Xx]{4}\s?[Xx]{4}\s?|[Xx]{8,12})\d{4})", base)
        fn_card = m.group(1) if m else None
        r["filename_card"] = fn_card
        r["filename_bin_digit"] = fn_card[0] if fn_card else None
        r["filename_bin_network"] = BIN_NET.get(fn_card[0]) if fn_card else None
        r["filename_last4"] = re.sub(r"\s", "", fn_card)[-4:] if fn_card else None

        # ---- (a) network tokens ----
        nets = {}
        for tok in NETWORKS:
            hits = find_token(lines, tok)
            if hits:
                nets[tok] = [{"page": h["page"], "mode": h["mode"], "bbox": h["bbox"],
                              "in_disclaimer": is_disclaimer(h["line"]), "line": h["line"][:190]}
                             for h in hits]
        r["network_hits"] = nets
        n_strict = sum(1 for t in nets.values() for h in t if h["mode"] == "STRICT")
        n_nondisc = [(t, h) for t, hs in nets.items() for h in hs if not h["in_disclaimer"]]
        r["network_summary"] = {
            "tokens_found": {t: len(hs) for t, hs in nets.items()},
            "strict_hits": n_strict,
            "non_disclaimer_hits": [{"token": t, "page": h["page"], "bbox": h["bbox"],
                                     "mode": h["mode"], "line": h["line"]} for t, h in n_nondisc],
            "verdict": "ALL_IN_DISCLAIMER" if nets and not n_nondisc
                       else ("NO_NETWORK_TOKENS" if not nets else "HAS_NON_DISCLAIMER_HIT"),
        }

        # ---- image layer ----
        doc = fitz.open(path)
        imgs = []
        for pno in range(min(2, doc.page_count)):
            page = doc[pno]
            ph = page.rect.height
            for im in page.get_images(full=True):
                xref = im[0]
                try:
                    rects = page.get_image_rects(xref)
                except Exception:
                    rects = []
                for rc in rects:
                    imgs.append({"page": pno + 1, "xref": xref,
                                 "bbox": [round(v, 2) for v in (rc.x0, rc.y0, rc.x1, rc.y1)],
                                 "w": round(rc.x1 - rc.x0, 1), "h": round(rc.y1 - rc.y0, 1),
                                 "in_top35pct": rc.y0 < ph * 0.35,
                                 "px": f"{im[2]}x{im[3]}"})
        r["images"] = imgs
        r["n_images_top35"] = sum(1 for i in imgs if i["in_top35pct"])

        # ---- (e) card-number headings, in reading order ----
        cards = []
        for li, ln in enumerate(lines):
            for m2 in CARD_RE.finditer(ln["text"]):
                tokv = m2.group(0)
                last4 = re.sub(r"\s", "", tokv)[-4:]
                cards.append({"page": ln["page"], "bbox": ln["bbox"], "line_idx": li,
                              "printed": tokv, "last4": last4,
                              "bin4": m2.group(1), "bin_digit": m2.group(1)[0],
                              "bin_network": BIN_NET.get(m2.group(1)[0]),
                              "mask_has_spaces": bool(re.search(r"\d{4}\s+[Xx]", tokv)),
                              "line": ln["text"][:160]})
        # dedupe identical (page, printed, bbox)
        seen, uniq = set(), []
        for c in cards:
            k = (c["page"], c["printed"], tuple(c["bbox"]))
            if k in seen:
                continue
            seen.add(k)
            uniq.append(c)
        uniq.sort(key=lambda c: (c["page"], c["bbox"][1], c["bbox"][0]))
        r["card_headings"] = uniq
        r["card_last4_reading_order"] = [c["last4"] for c in uniq]
        r["distinct_last4"] = sorted({c["last4"] for c in uniq})
        r["filename_card_present_in_text"] = (
            r["filename_last4"] in {c["last4"] for c in uniq} if r["filename_last4"] else None)

        # ---- rupee sign encoding ----
        rup = {"amount_header_spans": [], "codepoints_near_amount": {}}
        for pno in range(min(3, doc.page_count)):
            for blk in doc[pno].get_text("dict")["blocks"]:
                if blk.get("type") != 0:
                    continue
                for ln2 in blk["lines"]:
                    for sp in ln2["spans"]:
                        t = sp["text"]
                        if re.search(r"amount", t, re.I) or "`" in t or "₹" in t:
                            rup["amount_header_spans"].append(
                                {"page": pno + 1, "repr": repr(t), "font": sp.get("font"),
                                 "bbox": [round(v, 2) for v in sp["bbox"]]})
                            for ch in t:
                                if not ch.isalnum() and not ch.isspace():
                                    rup["codepoints_near_amount"].setdefault(
                                        f"U+{ord(ch):04X} {ch!r}", 0)
                                    rup["codepoints_near_amount"][f"U+{ord(ch):04X} {ch!r}"] += 1
        rup["amount_header_spans"] = rup["amount_header_spans"][:14]
        r["rupee"] = rup
        doc.close()
        res[sid] = r
        print(f"{sid}: nets={r['network_summary']['tokens_found']} verdict={r['network_summary']['verdict']} "
              f"cards={r['card_last4_reading_order']} imgs_top={r['n_images_top35']}")

    with open(OUT, "w") as f:
        json.dump(res, f, indent=1)
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
