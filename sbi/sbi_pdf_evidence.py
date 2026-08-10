"""PyMuPDF page/coordinate evidence from the SBI PDF itself.

Used to ADJUDICATE Luna-vs-CSV and Luna-vs-GT disagreements against the physical
document rather than trusting either side. fitz is already installed; pip is
blocked on this machine so nothing new is added.

SBI's page-1 layout is the reason coordinates are mandatory here rather than a
nicety. `page.get_text()` returns the ACCOUNT SUMMARY block as all labels first
and then all values, in a DIFFERENT order from the labels -- e.g. labels come out
"Credit Limit / Cash Limit / Available Credit Limit / Available Cash Limit" while
the four figures come out in an unrelated sequence. Any text-order reading of that
block is guesswork. The label->value binding is only recoverable geometrically:
each value sits in the column strip under (or beside) its label, so the value is
found by nearest-label search in (x, y) space.
"""
import re

import fitz

# The four ACCOUNT SUMMARY money labels that are mutually confusable, plus the
# rest of the statement-level figures.
SUMMARY_LABELS = {
    "totalAmountDue": ["total amount due"],
    "totalMinimumAmountDue": ["minimum amount due"],
    "totalCreditLimit": ["credit limit"],
    "availableCreditLimit": ["available credit limit"],
    "cashLimit": ["cash limit"],
    "availableCashLimit": ["available cash limit"],
    "previousBalance": ["previous balance"],
    "totalOutstanding": ["total outstanding"],
}

MONEY = re.compile(r"^-?[\d,]+\.\d{2}$|^-?[\d,]{1,3}(?:,[\d]{2,3})*$")
# Summary figures are ALWAYS printed with two decimals on SBI statements
# ('11,787.00', '0.00'). Requiring the decimals is what stops the label binder
# picking up a bare integer out of nearby prose -- the first version of this bound
# 'Minimum Amount Due' to the '20' in the footnote "within 20 days" and reported 8
# spurious defects across the 10-statement sample.
MONEY_DEC = re.compile(r"^-?[\d,]+\.\d{2}$")
DATE = re.compile(r"^\d{2}\s+[A-Za-z]{3}\s+\d{4}$")

# The ACCOUNT SUMMARY label/value grid lives in the top band of page 1. The same
# label phrases recur verbatim in the footnote prose at the bottom of the page
# ('**To keep your credit card in good standing... the Minimum Amount Due
# includes...'), so the binder is restricted to the grid band by y-coordinate.
SUMMARY_BAND_Y_MAX = 340.0


def open_pdf(path):
    return fitz.open(path)


def page_lines(page):
    """-> [(y, x0, x1, text)] one entry per rendered line, y-sorted.

    Lines rather than words: SBI splits a narration across many word boxes on one
    baseline, and the whole point is to reconstruct the printed row.
    """
    out = []
    d = page.get_text("dict")
    for blk in d.get("blocks", []):
        for ln in blk.get("lines", []):
            txt = "".join(sp.get("text", "") for sp in ln.get("spans", []))
            if not txt.strip():
                continue
            x0, y0, x1, y1 = ln["bbox"]
            out.append((round(y0, 1), round(x0, 1), round(x1, 1), txt.strip()))
    out.sort(key=lambda t: (t[0], t[1]))
    return out


def find_label_boxes(page, phrase):
    """All rects whose text contains `phrase` (case-insensitive), via search_for on
    the phrase and a fallback over line text."""
    hits = list(page.search_for(phrase, quads=False) or [])
    if hits:
        return hits
    out = []
    for y, x0, x1, t in page_lines(page):
        if phrase.lower() in t.lower():
            out.append(fitz.Rect(x0, y, x1, y + 10))
    return out


def money_tokens(page, require_decimals=True):
    """-> [(rect, text, value)] for every token on the page that looks like money.

    `require_decimals` is the default because every ACCOUNT SUMMARY figure on an SBI
    statement is printed to two decimals; accepting bare integers lets footnote prose
    ("within 20 days") masquerade as a money value.
    """
    pat = MONEY_DEC if require_decimals else MONEY
    out = []
    for w in page.get_text("words"):
        x0, y0, x1, y1, t = w[0], w[1], w[2], w[3], w[4]
        s = t.strip()
        if pat.match(s):
            try:
                v = float(s.replace(",", ""))
            except ValueError:
                continue
            out.append((fitz.Rect(x0, y0, x1, y1), s, v))
    return out


def summary_evidence(pdf_path, page_index=0):
    """Bind each ACCOUNT SUMMARY label to its nearest money token GEOMETRICALLY.

    Returns {field: [{value, printed, label_rect, value_rect, dx, dy, page}]} with
    candidates ranked nearest-first, plus the raw token list, so a human (or the
    report) can see WHY a binding was chosen and how close the runner-up was.

    'Credit Limit' is a substring of 'Available Credit Limit', so the longer
    labels are matched FIRST and their rects are excluded from the shorter label's
    candidates -- without that, totalCreditLimit would bind to the available-limit
    label on every statement.
    """
    doc = open_pdf(pdf_path)
    page = doc[page_index]
    toks = [t for t in money_tokens(page) if t[0].y0 <= SUMMARY_BAND_Y_MAX]

    claimed = []          # label rects already consumed by a longer phrase
    ev = {}
    order = ["available credit limit", "available cash limit", "total amount due",
             "minimum amount due", "credit limit", "cash limit",
             "previous balance", "total outstanding"]
    field_of = {}
    for f, phrases in SUMMARY_LABELS.items():
        for p in phrases:
            field_of[p] = f

    for phrase in order:
        field = field_of[phrase]
        boxes = []
        for r in find_label_boxes(page, phrase):
            if r.y0 > SUMMARY_BAND_Y_MAX:
                continue      # a footnote-prose occurrence, not the grid label
            if any(r.intersects(c) and abs(r.x0 - c.x0) < 2 and abs(r.y0 - c.y0) < 2
                   for c in claimed):
                continue
            # a shorter phrase must not re-use a rect already inside a longer one
            if any(c.contains(r) for c in claimed):
                continue
            boxes.append(r)
        cands = []
        for r in boxes:
            claimed.append(r)
            for tr, s, v in toks:
                dx = tr.x0 - r.x0
                dy = tr.y0 - r.y0
                # value is in the same column strip, at or below the label
                if abs(dx) <= 60 and -4 <= dy <= 120:
                    cands.append({"value": v, "printed": s, "dx": round(dx, 1),
                                  "dy": round(dy, 1), "dist": round(abs(dx) + dy * 0.6, 2),
                                  "label_rect": [round(x, 1) for x in r],
                                  "value_rect": [round(x, 1) for x in tr],
                                  "page": page_index + 1})
        cands.sort(key=lambda c: c["dist"])
        ev[field] = cands[:4]
    doc.close()
    return ev


def txn_rows(pdf_path):
    """Reconstruct printed transaction rows geometrically across all pages.

    An SBI transaction row is: a date token ('03 Jan 26'), narration words, a money
    token, and a trailing single-char C/D marker in the right-most column. Rows are
    grouped by baseline y, which is what makes the C/D marker attributable to the
    right row -- in plain text order the marker lands on its own line.
    """
    doc = open_pdf(pdf_path)
    rows = []
    dre = re.compile(r"^(\d{2})\s+([A-Za-z]{3})\s+(\d{2})$")
    for pi in range(doc.page_count):
        page = doc[pi]
        by_y = {}
        for w in page.get_text("words"):
            x0, y0, x1, y1, t = w[0], w[1], w[2], w[3], w[4]
            by_y.setdefault(round(y0, 0), []).append((x0, t, x1))
        for y in sorted(by_y):
            ws = sorted(by_y[y], key=lambda z: z[0])
            joined = " ".join(w[1] for w in ws)
            m = dre.match(" ".join(w[1] for w in ws[:3]))
            if not m:
                continue
            money = [(x0, t) for x0, t, _ in ws if MONEY.match(t.strip())
                     and "." in t]
            marker = [t for x0, t, _ in ws if t.strip() in ("C", "D", "T")
                      and x0 > 400]
            desc = " ".join(w[1] for w in ws[3:] if not MONEY.match(w[1].strip())
                            and w[1].strip() not in ("C", "D", "T"))
            rows.append({"page": pi + 1, "y": y, "date": m.group(0),
                         "date_norm": f"{m.group(1)} {m.group(2)} {m.group(3)}",
                         "description_geom": desc.strip(),
                         "amount_printed": money[-1][1] if money else None,
                         "amount": (float(money[-1][1].replace(",", "")) if money else None),
                         "marker": marker[-1] if marker else None,
                         "line": joined})
    doc.close()
    return rows


def find_value_on_page(pdf_path, needle):
    """Where (if anywhere) does a literal string appear? -> [(page, rect, line)]."""
    doc = open_pdf(pdf_path)
    hits = []
    for pi in range(doc.page_count):
        for r in doc[pi].search_for(str(needle)) or []:
            line = ""
            for y, x0, x1, t in page_lines(doc[pi]):
                if abs(y - r.y0) < 3:
                    line = t
                    break
            hits.append({"page": pi + 1, "rect": [round(x, 1) for x in r], "line": line})
    doc.close()
    return hits


def reward_evidence(pdf_path):
    """The reward/cashback region, which is the SBI-specific closingPoints trap.

    SBI prints TWO different point/cashback tables:
      * a per-cycle strip: 'Previous Balance | Earned | Redeemed/Expired/Reversed |
        Closing Balance'  -> the CURRENT-CYCLE numbers.
      * a 'SAVINGS AND BENEFITS SECTION' with three columns 'For this statement /
        For this year / From the card issue date' for both Cash Back and Reward
        Points  -> LIFETIME and year-to-date figures.
    Reading closingPoints out of the second table (or out of total cashback) is the
    documented failure mode, so both are returned with their geometry.
    """
    doc = open_pdf(pdf_path)
    out = {"cycle_strip": [], "savings_section": [], "raw_lines": []}
    for pi in range(doc.page_count):
        page = doc[pi]
        lines = page_lines(page)
        txt = "\n".join(t for _, _, _, t in lines)
        if re.search(r"Previous Balance|Closing Balance|SAVINGS AND BENEFITS|"
                     r"Cash Back|Reward Points|SHOP & SMILE|CASHBACK", txt, re.I):
            ints = []
            for w in page.get_text("words"):
                s = w[4].strip()
                if re.match(r"^-?[\d,]+(?:\.\d{2})?$", s):
                    try:
                        ints.append({"v": float(s.replace(",", "")), "printed": s,
                                     "x": round(w[0], 1), "y": round(w[1], 1),
                                     "page": pi + 1})
                    except ValueError:
                        pass
            labels = {}
            for key in ("Previous Balance", "Earned", "Redeemed", "Closing Balance",
                        "For this statement", "For this year", "From the card issue date",
                        "Cash Back", "Reward Points", "Petrol Surcharge Waiver"):
                for r in page.search_for(key) or []:
                    labels.setdefault(key, []).append(
                        {"x": round(r.x0, 1), "y": round(r.y0, 1), "page": pi + 1})
            if labels:
                out["savings_section"].append({"page": pi + 1, "labels": labels,
                                               "numbers": ints})
            out["raw_lines"].append({"page": pi + 1,
                                     "lines": [t for _, _, _, t in lines][:200]})
    doc.close()
    return out
