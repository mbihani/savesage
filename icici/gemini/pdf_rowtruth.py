"""Extract PDF TRANSACTION ROW TRUTH for the 11 ICICI statements.

Columns are discovered from each table page's OWN HEADER, not hardcoded, because ICICI
ships two templates with different geometry:
  classic  : Date x~208 | SerNo x~252 | Transaction Details x~305 | Reward Points x~452
             | Intl.# amount x~481 | Amount (in `) right-aligned to x1~557
  2018     : Date x~41  | Ref. Number x~89 | Transaction Details x~198
             | Reward Points x~376 | Currency/International amount x~424 | Amount(in |) x~549

Excluded on purpose:
  * the pre-printed illustrative Minimum-Amount-Due worked example (the numbered
    "SL. No / Transaction" table) -- fixed specimen values, belongs to no cardholder.
  * card-number heading lines, which sit in the date column but are not rows.

The "CR" marker is printed INSIDE the amount cell ("490.00 CR"), so direction comes from
the amount cell, and the amount itself is always reported unsigned.
"""

import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "probe"))
from pdflib import doc_lines, parse_indian_num  # noqa: E402

PDF_DIR = "/Users/mayanck.bihani/Downloads/output/ICICI/PDF"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_rowtruth.json")

DATE = re.compile(r"^(\d{2}/\d{2}/\d{4})$")
CARDHEAD = re.compile(r"\d{4}[\sXx]*[Xx]{4}[\sXx]*[0-9Xx]{4}")
MADHEAD = re.compile(r"SL\.?\s*No", re.I)
AMT = re.compile(r"^([\d,]+(?:\.\d+)?)\s*(CR|DR)?$", re.I)


def page_tables(lines):
    """Yield (page, header_y, cols) for every real transaction table."""
    by_page = {}
    for l in lines:
        by_page.setdefault(l["page"], []).append(l)
    for pg, ls in sorted(by_page.items()):
        # Anchor on the "Date" column header, then take the other column headers ONLY
        # from its own row band. Without the row-band constraint, 952325284's page-1
        # section banner "TRANSACTION DETAILS" (y=592.1, x=253.7) was picked as the
        # description column instead of the table's real header (y=643.8, x=233.4),
        # putting the desc window at [213.7,361.2) so every value at x=198.0 fell
        # outside and all 34 descriptions came back blank.
        dates = [l["bbox"] for l in ls if l["text"].strip().lower() == "date"]
        if not dates:
            continue
        dbb = min(dates, key=lambda b: b[1])
        hdr = {"date": dbb}
        for l in ls:
            if abs(l["bbox"][1] - dbb[1]) > 14:      # must share the header row band
                continue
            tl = l["text"].strip().lower()
            if "transaction details" in tl:
                hdr.setdefault("desc", l["bbox"])
            elif tl.startswith("amount(in") or tl.startswith("amount (in"):
                hdr.setdefault("amt", l["bbox"])
            elif tl in ("reward", "points"):
                hdr.setdefault("rp", l["bbox"])
        if "date" in hdr and "desc" in hdr:
            # skip the illustrative MAD example table
            if any(MADHEAD.match(l["text"].strip()) for l in ls
                   if abs(l["bbox"][1] - hdr["date"][1]) < 6):
                continue
            yield pg, hdr["date"][1], hdr


def extract(lines):
    rows = []
    for pg, hy, hdr in page_tables(lines):
        ls = [l for l in lines if l["page"] == pg and l["bbox"][1] > hy + 4]
        # skip anything at/below a MAD example heading on this page
        mad_y = min([l["bbox"][1] for l in ls if MADHEAD.match(l["text"].strip())] or [1e9])
        ls = [l for l in ls if l["bbox"][1] < mad_y]
        dx0 = hdr["date"][0]
        descx0 = hdr["desc"][0]
        amtx0 = hdr["amt"][0] if "amt" in hdr else descx0 + 200
        rpx0 = hdr["rp"][0] if "rp" in hdr else None

        # group into row bands by y
        bands = {}
        for l in ls:
            bands.setdefault(round(l["bbox"][1] / 3.0), []).append(l)
        # merge adjacent keys into rows anchored on a date in the date column
        ys = sorted(bands)
        for k in ys:
            band = bands[k]
            # Tolerance is 22pt, not 12: on the 2018 template the "Date" HEADER sits at
            # x=54.4 while its date VALUES sit at x=40.9 (the header is centred over a
            # wider cell). A 12pt window silently dropped all 34 rows of 952325284.
            dcell = [l for l in band if abs(l["bbox"][0] - dx0) < 22 and DATE.match(l["text"].strip())]
            if not dcell:
                continue
            y = dcell[0]["bbox"][1]
            # the full row = everything within 6pt of this y on this page
            full = [l for l in ls if abs(l["bbox"][1] - y) <= 6]
            # Left edge is descx0-40 for the same centred-header reason as the date
            # column: on the 2018 template "Transaction Details" is centred at x=233.4
            # while its values start at x=198.0. A -8 window returned 34 blank
            # descriptions on 952325284. The ref-number column is excluded by requiring
            # the cell to start at or right of the ref column when one exists.
            lo = descx0 - 40
            hi = (rpx0 - 12 if rpx0 else amtx0 - 12)
            desc = " ".join(l["text"] for l in sorted(
                [l for l in full if lo <= l["bbox"][0] < hi
                 and not re.match(r"^\d{9,}$", l["text"].strip())],
                key=lambda l: l["bbox"][0]))
            amtc = [l for l in full if l["bbox"][2] >= amtx0 - 6]
            amt, direction = None, None
            for l in sorted(amtc, key=lambda l: -l["bbox"][0]):
                m = AMT.match(l["text"].strip())
                if m:
                    amt = parse_indian_num(m.group(1))
                    direction = "CREDIT" if (m.group(2) or "").upper() == "CR" else "DEBIT"
                    break
            rp = None
            if rpx0 is not None:
                for l in full:
                    if abs(l["bbox"][0] - rpx0) < 30 and re.match(r"^-?\d+$", l["text"].strip()):
                        rp = int(l["text"].strip())
                        break
            if CARDHEAD.search(desc):
                continue
            rows.append({"page": pg, "y": round(y, 2), "date": dcell[0]["text"].strip(),
                         "description": desc.strip(), "amount": amt,
                         "direction": direction, "rewardPoints": rp})
    # dedupe on (page, y)
    seen, out = set(), []
    for r in rows:
        k = (r["page"], r["y"])
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    out.sort(key=lambda r: (r["page"], r["y"]))
    return out


def main():
    truth = {}
    for path in sorted(glob.glob(PDF_DIR + "/*.pdf")):
        sid = re.match(r"decrypt_(\d+)_", os.path.basename(path)).group(1)
        lines, _ = doc_lines(path)
        rows = extract(lines)
        truth[sid] = {"n_rows": len(rows), "rows": rows}
        nblank = sum(1 for r in rows if not r["description"])
        namt = sum(1 for r in rows if r["amount"] is None)
        print(f"{sid}: rows={len(rows):<4} blank_desc={nblank} missing_amt={namt}")
    json.dump(truth, open(OUT, "w"), indent=1)
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()
