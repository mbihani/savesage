#!/usr/bin/env python3
"""Pick the deterministic, structurally-DIVERSE 10-statement Phase-1 tuning sample.

Requirement: do not sample 10 near-identical statements. ICICI is 133/304 Amazon Pay,
so uniform random sampling would return ~4-5 Amazon Pay clones and would never see
Emeralde/Rubyx/HPCL/MMT at all. So the sample is built by an explicit, recorded rule
rather than by a seed alone:

  1. Structural signature per PDF = (product family from filename, page count,
     transaction-table row estimate, whether >1 card block appears). Read with fitz --
     no API calls, no cost.
  2. One statement per DISTINCT product family, largest-by-txn-rows first, until 8
     slots are filled (product families ordered by corpus frequency so the common
     layouts are represented, then rarer ones).
  3. The remaining 2 slots go to the corpus-wide extremes: the highest txn-row PDF
     and the largest-byte PDF not already picked -- the truncation / long-table risk.

Deterministic: no RNG at all (a fixed seed is recorded for the record but the
selection is a total order, so it is reproducible byte-for-byte). Every id is written
out with the reason it was picked.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import icici_lib as L

import fitz

SEED = 20260810  # recorded for provenance; the selection rule is deterministic, not random
OUT = os.path.join(L.HERE, "phase1_sample.json")

# rows in a transaction table look like a date at the start of a line
_DATE_LINE = re.compile(r"^\s*(\d{2}[/-]\d{2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})")


def signature(path):
    doc = fitz.open(path)
    pages = doc.page_count
    txt = []
    for p in doc:
        txt.append(p.get_text("text"))
    doc.close()
    full = "\n".join(txt)
    lines = full.splitlines()
    txn_rows = sum(1 for ln in lines if _DATE_LINE.match(ln))
    # a second "Card Number"/"XXXX" block hints at an add-on card
    cardish = len(re.findall(r"\d{4}\s?X{4}\s?X{4}\s?\d{4}|\d{4}XXXXXXXX\d{4}", full))
    return {"pages": pages, "txn_row_est": txn_rows, "card_tokens": cardish,
            "chars": len(full)}


def main():
    corpus = L.discover_pdfs()
    recs = []
    for sid, fname, path in corpus:
        sig = signature(path)
        sig.update({"statement_id": sid, "pdf": fname,
                    "product": L._product_from_name(fname),
                    "bytes": os.path.getsize(path)})
        recs.append(sig)

    from collections import Counter, defaultdict
    freq = Counter(r["product"] for r in recs)
    by_prod = defaultdict(list)
    for r in recs:
        by_prod[r["product"]].append(r)
    # products ordered: common layouts first (so the dominant layouts are covered),
    # then rarer ones. Within a product, the biggest transaction table first -- the
    # hardest instance of that layout.
    prods = sorted(freq, key=lambda p: (-freq[p], p))
    for p in prods:
        by_prod[p].sort(key=lambda r: (-r["txn_row_est"], -r["bytes"], r["pdf"]))

    picked, why = [], {}
    for p in prods:
        if len(picked) >= 8:
            break
        cand = by_prod[p][0]
        picked.append(cand)
        why[cand["statement_id"]] = (
            f"distinct product family '{p}' (n={freq[p]} in corpus); "
            f"largest txn table within that family (est {cand['txn_row_est']} rows)")

    got = {r["statement_id"] for r in picked}
    # extreme 1: corpus-wide largest transaction table
    for r in sorted(recs, key=lambda r: (-r["txn_row_est"], r["pdf"])):
        if r["statement_id"] not in got:
            picked.append(r)
            got.add(r["statement_id"])
            why[r["statement_id"]] = (
                f"corpus extreme: highest transaction-row estimate not already picked "
                f"({r['txn_row_est']} rows, {r['pages']} pages) -- long-table/truncation risk")
            break
    # extreme 2: corpus-wide largest file
    for r in sorted(recs, key=lambda r: (-r["bytes"], r["pdf"])):
        if r["statement_id"] not in got:
            picked.append(r)
            got.add(r["statement_id"])
            why[r["statement_id"]] = (
                f"corpus extreme: largest PDF not already picked ({r['bytes']} bytes, "
                f"{r['pages']} pages) -- largest native-PDF payload")
            break

    picked = picked[:10]
    out = {
        "seed_recorded": SEED,
        "selection_rule": ("1 per distinct product family (families ordered by corpus "
                           "frequency desc, then name; within family: max txn_row_est, "
                           "then max bytes, then filename) for 8 slots; then the "
                           "corpus-wide max-txn-rows and max-bytes PDFs for slots 9-10. "
                           "Fully deterministic total order, no RNG."),
        "corpus_pdfs": len(corpus),
        "product_family_freq": dict(sorted(freq.items(), key=lambda x: (-x[1], x[0]))),
        "sample": [{**r, "reason": why[r["statement_id"]]} for r in picked],
        "sample_ids": [r["statement_id"] for r in picked],
        "sample_pdfs": [r["pdf"] for r in picked],
    }
    with open(OUT, "w") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps({"n": len(picked), "corpus": len(corpus)}, indent=1))
    for r in picked:
        print(f"  {r['statement_id']:>28}  {r['product']:<32} pages={r['pages']:>2} "
              f"txn~{r['txn_row_est']:>3} {r['bytes']/1024:>7.0f}K")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
