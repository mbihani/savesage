#!/usr/bin/env python3
"""Profile the HDFC corpus structurally, then pick a DIVERSE 10-statement sample.

Sampling is deterministic (sorted corpus, fixed seed, recorded ids) but biased for
STRUCTURAL diversity rather than being 10 near-identical statements: the sample is
stratified over layout family x transaction volume, and the largest / highest-row
statements are force-included because those are where a native-PDF extractor
truncates or drops table rows.
"""
import json
import os
import random
import re
import sys

import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hdfc_lib as H

HERE = os.path.dirname(os.path.abspath(__file__))
SEED = 20260810


def layout_family(text, filename):
    """A coarse layout label from marker strings actually present in the text."""
    t = text.lower()
    if "pixel play" in t or "pixel" in filename.lower():
        return "pixel_play"
    if "neucoin" in t or "tata neu" in t:
        return "tata_neu"
    if "cash back" in t or "cashback summary" in t or "cash back summary" in t:
        return "cashback"
    if "millennia" in t:
        return "millennia"
    if "infinia" in t:
        return "infinia"
    if "diners" in t:
        return "diners"
    if "regalia" in t:
        return "regalia"
    return "standard"


# HDFC txn rows start with a date. Both DD/MM/YYYY and DD Mon YYYY appear.
_DATE_ROW = re.compile(r"(?m)^\s*(\d{2}/\d{2}/\d{4}|\d{2}\s+[A-Za-z]{3},?\s*\d{4})")


def profile_one(path, filename):
    d = fitz.open(path)
    txt = "\n".join(p.get_text() for p in d)
    n_pages = d.page_count
    d.close()
    low = txt.lower()
    return {
        "filename": filename,
        "bytes": os.path.getsize(path),
        "pages": n_pages,
        "chars": len(txt),
        "date_rows": len(_DATE_ROW.findall(txt)),
        "layout": layout_family(txt, filename),
        "has_rewards_block": any(k in low for k in
                                 ("reward point", "neucoin", "cash back", "cashback")),
        "n_card_masks": len(set(re.findall(r"\d{4}\s?[X\*]{4,}\s?[X\*]*\d{4}", txt))),
        "mentions_addon": ("add-on" in low or "addon" in low or "add on card" in low),
    }


def main():
    matched, unmatched, pdfs_no_csv = H.build_join()
    prof = []
    for m in matched:
        try:
            p = profile_one(m["path"], m["filename"])
        except Exception as e:
            p = {"filename": m["filename"], "error": f"{type(e).__name__}: {e}",
                 "bytes": os.path.getsize(m["path"]), "pages": None, "chars": 0,
                 "date_rows": 0, "layout": "UNREADABLE", "has_rewards_block": False,
                 "n_card_masks": 0, "mentions_addon": False}
        p["sid"] = m["sid"]
        prof.append(p)

    from collections import Counter
    print("matched", len(matched), "unmatched_csv", len(unmatched),
          "pdfs_without_csv", len(pdfs_no_csv))
    print("layouts:", Counter(p["layout"] for p in prof).most_common())
    print("pages:", Counter(p["pages"] for p in prof).most_common())
    dr = sorted(p["date_rows"] for p in prof)
    print("date_rows min/med/max:", dr[0], dr[len(dr) // 2], dr[-1])
    print("zero-text (image-only?) PDFs:", sum(1 for p in prof if p["chars"] < 200))
    print("multi-mask:", Counter(p["n_card_masks"] for p in prof).most_common())

    # ---- deterministic diverse sample of 10 -------------------------------
    by_layout = {}
    for p in prof:
        by_layout.setdefault(p["layout"], []).append(p)
    for v in by_layout.values():
        v.sort(key=lambda p: p["sid"])

    picked, seen = [], set()

    def take(p, why):
        if p and p["sid"] not in seen:
            seen.add(p["sid"])
            picked.append({**p, "sample_reason": why})

    # 1) structural extremes: these are where truncation / row-loss shows up
    take(max(prof, key=lambda p: (p["date_rows"], p["sid"])), "max_transaction_rows")
    take(max(prof, key=lambda p: (p["bytes"], p["sid"])), "largest_file_bytes")
    take(max(prof, key=lambda p: (p["pages"] or 0, p["sid"])), "most_pages")
    take(min([p for p in prof if p["date_rows"] > 0] or prof,
             key=lambda p: (p["date_rows"], p["sid"])), "fewest_transaction_rows")
    zero = [p for p in prof if p["chars"] < 200]
    if zero:
        take(sorted(zero, key=lambda p: p["sid"])[0], "no_extractable_text_layer")
    multi = [p for p in prof if p["n_card_masks"] > 1]
    if multi:
        take(max(multi, key=lambda p: (p["n_card_masks"], p["sid"])), "multiple_card_masks")

    # 2) one representative per layout family, largest-first within family
    for lay in sorted(by_layout, key=lambda k: (-len(by_layout[k]), k)):
        if len(picked) >= 10:
            break
        take(max(by_layout[lay], key=lambda p: (p["date_rows"], p["sid"])),
             f"layout_representative:{lay}")

    # 3) fill remaining slots by seeded random draw over the sorted remainder
    rng = random.Random(SEED)
    rest = sorted([p for p in prof if p["sid"] not in seen], key=lambda p: p["sid"])
    rng.shuffle(rest)
    for p in rest:
        if len(picked) >= 10:
            break
        take(p, "seeded_random_fill")

    picked = picked[:10]
    out = {
        "seed": SEED,
        "corpus_pdfs": len(H.discover_pdfs()),
        "csv_rows": len(H.csv_rows()),
        "matched": len(matched),
        "unmatched_csv": [r["link"] for r in unmatched],
        "pdfs_without_csv": pdfs_no_csv,
        "sample": picked,
        "profile": sorted(prof, key=lambda p: p["sid"]),
    }
    H.G.atomic_write_json(os.path.join(HERE, "corpus_profile.json"), out)
    print("\nSAMPLE (10):")
    for p in picked:
        print(f"  {p['layout']:12s} rows={p['date_rows']:4d} pg={p['pages']} "
              f"kb={p['bytes']//1024:5d} {p['sample_reason']:34s} {p['filename'][:60]}")


if __name__ == "__main__":
    main()
