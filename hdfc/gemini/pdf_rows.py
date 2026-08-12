"""Geometric transaction-row extraction from HDFC PDFs, used as the PDF-side reference.

This is the adjudication backbone for the per-field analysis, so it is built on
OBSERVED span structure (see probe/explore_*.py) rather than on an assumed layout.

TWO LAYOUTS EXIST IN THIS 15-FILE SET AND THEY SHARE NOTHING
------------------------------------------------------------
Layout A -- "classic", 13 of 15 files. Fonts Calibri + ITFRupee + Wingdings.
  The rupee sign is a SEPARATE SPAN whose font is `ITFRupee` and whose text is the
  single character "C" (the font maps the rupee glyph onto code point 0x43 = ASCII
  'C'). A row looks like:
      ['17/06/2026| 16:06' Calibri 0x333333]  ['UPI-SAIMA BANU' Calibri 0x333333]
      ['C' ITFRupee 0x333333]  [' 31.00' Calibri 0x333333]  ['l' Wingdings 0xbc8f8f]
  CREDIT rows add a '+' span and recolour the '+'/'C'/amount run green 0x05c747:
      ['30/06/2026| 14:08' ...] ['CREDIT CARD PAYMENT...' ...]
      ['+  ' Calibri 0x05c747] ['C' ITFRupee 0x05c747] [' 2,600.00' Calibri 0x05c747]
  Row anchor: a leading span matching DD/MM/YYYY.

Layout B -- "Pixel Play", 2 of 15 files. Fonts Inter-*. NO ITFRupee at all: the
  rupee sign is a real U+20B9 glyph glued to the amount ("₹264.00"). Dates read
  "16 Jan 2026, 23:31". CREDIT is '+' glued to the amount plus a DIFFERENT green,
  0x07bf7d. The reward-points column is ALSO '+'-prefixed ("+13") but stays dark
  0x333333 -- so "a '+' somewhere in the row" is NOT the credit test; the '+' must be
  on the AMOUNT. This is the exact trap the HDFC prompt already warns about.

WHY COLOUR AND '+' ARE BOTH READ
-------------------------------
They are independent encodings of the same fact, so agreeing on 100% of rows makes
them hard ground truth for `direction`; a disagreement would mean the probe is wrong
and must be fixed before any model is judged against it. The agreement rate is
reported, never assumed.

Rows are keyed by (page, y-bucket). The numbered "Cash Back Summary" table further
down the page is anchored by a bare ordinal ('1', '2', '3'), NOT by a date, so the
date anchor excludes it -- verified on decrypt_1723515293.
"""

import os
import re
from collections import defaultdict

import fitz

PDF_DIR = "/Users/mayanck.bihani/Downloads/output/HDFC/PDF"

# layout A
A_DATE = re.compile(r"^\s*(\d{2}/\d{2}/\d{4})\s*(?:\|\s*(\d{2}:\d{2}))?\s*$")
A_GREEN = 0x05C747
# layout B
B_DATE = re.compile(r"^\s*(\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\s*(?:,\s*(\d{2}:\d{2}))?\s*$")
B_GREEN = 0x07BF7D

AMOUNT_BODY = re.compile(r"^\s*([\d][\d,]*(?:\.\d{1,2})?)\s*$")
B_AMOUNT = re.compile(r"^\s*(\+)?\s*₹\s*([\d][\d,]*(?:\.\d{1,2})?)\s*$")
RP_ONLY = re.compile(r"^\s*([+-])\s*(\d+)\s*$")   # a bare signed integer = reward points
PLUS_ONLY = re.compile(r"^\s*\+\s*$")
CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def to_num(s):
    return float(s.replace(",", ""))


def clean_text(s):
    """Treat PDF control separators as whitespace, never as printed characters."""
    return re.sub(r"\s+", " ", CONTROL.sub(" ", s)).strip()


def _rows(page, ytol=2.0):
    """Group non-blank spans into visual rows by y, left-to-right within a row."""
    b = defaultdict(list)
    for blk in page.get_text("dict").get("blocks", []):
        for ln in blk.get("lines", []):
            for sp in ln.get("spans", []):
                if sp["text"].strip():
                    b[round(sp["bbox"][1] / ytol)].append(sp)
    for k in sorted(b):
        yield sorted(b[k], key=lambda s: s["bbox"][0])


def detect_layout(doc):
    for pno in range(len(doc)):
        for blk in doc[pno].get_text("dict").get("blocks", []):
            for ln in blk.get("lines", []):
                for sp in ln.get("spans", []):
                    if "ITFRupee" in sp["font"]:
                        return "A"
    return "B"


def _row_a(spans):
    """Layout A row -> dict, or None if this row is not a transaction."""
    # A wrapped narration may occupy lines above and below the date/amount baseline.
    # Find the anchor rather than assuming the first visual line is the row.
    di = next((i for i, s in enumerate(spans) if A_DATE.match(s["text"])), None)
    if di is None:
        return None
    m = A_DATE.match(spans[di]["text"])
    if not m:
        return None
    # find the ITFRupee 'C' span; the amount is the next span to its right
    currency_spans = [(i, s) for i, s in enumerate(spans)
                      if "ITFRupee" in s["font"] and s["text"].strip() == "C"]
    # Loan-summary tables can contain a date plus several rupee columns on one line;
    # those are not card-transaction rows.
    if len(currency_spans) != 1:
        return None
    ci, _ = currency_spans[0]
    if ci + 1 >= len(spans):
        return None
    amt_sp = spans[ci + 1]
    am = AMOUNT_BODY.match(amt_sp["text"])
    if not am:
        return None

    has_plus = ci >= 1 and PLUS_ONLY.match(spans[ci - 1]["text"]) is not None
    green = amt_sp["color"] == A_GREEN
    # description = spans strictly between the date and the '+'/'C' run, minus a
    # standalone signed-integer reward-points span.
    stop = ci - 1 if has_plus else ci
    desc_sp, rp = [], None
    date_sp = spans[di]
    # Narration is the column between the date and amount columns.  A bold standalone
    # EMI token to its left is a separate badge column (observed x=232.3/240.6 while
    # narration begins x=250.7/260.2; similarly x=114.4 vs 126.7 on page-2 tables).
    # Do not join that adjacent column into the description.
    candidates = [s for i, s in enumerate(spans) if i not in (di, ci)
                  and s is not amt_sp and s["bbox"][0] > date_sp["bbox"][0]
                  and s["bbox"][0] < spans[ci]["bbox"][0]]
    for s in candidates:
        if PLUS_ONLY.match(s["text"]):
            continue
        if clean_text(s["text"]).upper() == "EMI" and "Bold" in s["font"]:
            continue
        if RP_ONLY.match(s["text"]):
            g = RP_ONLY.match(s["text"])
            rp = int(g.group(2)) * (-1 if g.group(1) == "-" else 1)
            continue
        desc_sp.append(s)
    desc_sp.sort(key=lambda s: (round(s["bbox"][1], 1), s["bbox"][0]))
    return {
        "date": m.group(1), "time": m.group(2),
        "description": clean_text(" ".join(s["text"] for s in desc_sp)),
        "amount": to_num(am.group(1)),
        "has_plus": has_plus, "is_green": green,
        "reward_points": rp,
        "currency_marker": "ITFRupee_C",
        "amount_color": f"0x{amt_sp['color']:06x}",
        "date_bbox": [round(x, 2) for x in date_sp["bbox"]],
        "amount_bbox": [round(x, 2) for x in amt_sp["bbox"]],
        "description_bboxes": [[round(x, 2) for x in s["bbox"]] for s in desc_sp],
    }


def _row_b(spans):
    """Layout B (Pixel Play) row -> dict, or None."""
    m = B_DATE.match(spans[0]["text"])
    if not m:
        return None
    ai = None
    for i, s in enumerate(spans):
        if B_AMOUNT.match(s["text"]):
            ai = i
    if ai is None:
        return None
    amt_sp = spans[ai]
    bm = B_AMOUNT.match(amt_sp["text"])
    has_plus = bm.group(1) == "+"
    green = amt_sp["color"] == B_GREEN
    desc_sp, rp = [], None
    for s in spans[1:ai]:
        g = RP_ONLY.match(s["text"])
        if g:
            rp = int(g.group(2)) * (-1 if g.group(1) == "-" else 1)
            continue
        desc_sp.append(s)
    return {
        "date": m.group(1), "time": m.group(2),
        "description": "".join(s["text"] for s in desc_sp).strip(),
        "amount": to_num(bm.group(2)),
        "has_plus": has_plus, "is_green": green,
        "reward_points": rp,
        "currency_marker": "rupee_glyph",
        "amount_color": f"0x{amt_sp['color']:06x}",
        "date_bbox": [round(x, 2) for x in spans[0]["bbox"]],
        "amount_bbox": [round(x, 2) for x in amt_sp["bbox"]],
        "description_bboxes": [[round(x, 2) for x in s["bbox"]] for s in desc_sp],
    }


def extract(path):
    """-> dict with layout, itfrupee_span_count, and the transaction row list."""
    doc = fitz.open(path)
    layout = detect_layout(doc)
    fn = _row_a if layout == "A" else _row_b
    itf = 0
    rows = []
    for pno in range(len(doc)):
        for blk in doc[pno].get_text("dict").get("blocks", []):
            for ln in blk.get("lines", []):
                for sp in ln.get("spans", []):
                    if "ITFRupee" in sp["font"]:
                        itf += 1
        visual_rows = list(_rows(doc[pno]))
        if layout == "A":
            # Build transaction bands from successive date anchors. This captures
            # narrations wrapped around the date/amount baseline (e.g. y=692/700
            # around a y=696 anchor) while midpoint boundaries prevent row leakage.
            flat = [s for row in visual_rows for s in row]
            anchors = sorted([s for s in flat if A_DATE.match(s["text"])],
                             key=lambda s: s["bbox"][1])
            bands = []
            for i, anchor in enumerate(anchors):
                y = (anchor["bbox"][1] + anchor["bbox"][3]) / 2
                py = ((anchors[i-1]["bbox"][1] + anchors[i-1]["bbox"][3]) / 2
                      if i else y - 20)
                ny = ((anchors[i+1]["bbox"][1] + anchors[i+1]["bbox"][3]) / 2
                      if i + 1 < len(anchors) else y + 20)
                # Normal row pitch is ~14pt; cap unusually large gaps so unrelated
                # totals/loan tables cannot be absorbed by the last transaction.
                lo, hi = max((py + y) / 2, y - 7.1), min((y + ny) / 2, y + 7.1)
                band = [s for s in flat if lo <= (s["bbox"][1] + s["bbox"][3]) / 2 < hi]
                bands.append(sorted(band, key=lambda s: (s["bbox"][1], s["bbox"][0])))
        else:
            bands = visual_rows
        for spans in bands:
            r = fn(spans)
            if r:
                r["page"] = pno + 1
                rows.append(r)
    doc.close()
    for r in rows:
        # direction from the two independent signals; disagreement is surfaced, not hidden
        r["direction"] = "CREDIT" if (r["has_plus"] or r["is_green"]) else "DEBIT"
        r["signals_agree"] = (r["has_plus"] == r["is_green"])
    return {"layout": layout, "itfrupee_spans": itf, "rows": rows}


def full_text(path):
    doc = fitz.open(path)
    t = "\n".join(doc[p].get_text() for p in range(len(doc)))
    doc.close()
    return t


def corpus():
    """-> sorted [(sid, filename, path)] for the 15-file HDFC set."""
    out = []
    for f in sorted(os.listdir(PDF_DIR)):
        if f.lower().endswith(".pdf"):
            out.append((sid_for(f), f, os.path.join(PDF_DIR, f)))
    return out


_SAFE = re.compile(r"[^A-Za-z0-9]+")


def sid_for(filename):
    stem = filename[:-4] if filename.lower().endswith(".pdf") else filename
    return _SAFE.sub("_", stem).strip("_")


def statement_id(filename):
    """The numeric statement id used by gt_full/ record names, e.g. 1723515293."""
    m = re.match(r"^decrypt_(?:encrypt_)?(\d+)_", filename)
    return m.group(1) if m else None
