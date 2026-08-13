"""PDF-DERIVED GROUND TRUTH for the SBI transaction table row count.

WHY THIS EXISTS
---------------
Three prompt arms disagree on statement 1707857175 by exactly one row (70 / 71 / 70).
Optimising toward the arm that emits 71 would be circular: the arm could be duplicating
a row just as easily as the other arms could be dropping one. The only authority is what
the PDF actually PRINTS, so this script reconstructs the printed transaction table from
span geometry and reports the row count independently of every model output.

METHOD
------
For each page, collect every text span with its (x0, y0, x1) box, then bucket spans into
printed LINES by y-centre (tolerance +/- 1.6pt, which is under the ~9.5pt line pitch of
this template and above the intra-line baseline jitter PyMuPDF reports). A printed
transaction ROW is a line that carries, in left-to-right order:

    a DATE token   ->  "28 Apr 26"  (SBI prints DD Mon YY)  -- OPTIONAL, see below
    a narration    ->  arbitrary text
    an AMOUNT      ->  Indian-grouped decimal, e.g. "1,750.00"
    a MARKER       ->  a lone C / D / T / M in the right-hand column

The row test is DELIBERATELY geometric, not lexical: the marker must sit in the
right-hand marker band, learned from the page itself. This is what makes it robust
against narrations that happen to contain digits or a stray capital D.

THE DATE IS OPTIONAL, AND GETTING THIS WRONG WAS A REAL BUG IN THIS FILE
-----------------------------------------------------------------------
The first version of this script REQUIRED the leftmost span to be a date. SBI prints its
tax-continuation rows -- "IGST DB @ 18.00%   190.74  D" -- with NO date of their own;
they inherit the date of the row above. Requiring a date silently dropped every one of
them, which made the probe report 7 where the PDF prints 8 (905768587) and 1 where it
prints 2 (221159806) -- i.e. the probe ACCUSED the model of inventing rows that are in
fact printed. The date is now optional and inherited from the previous dated row.

A continuation line from a WRAPPED narration is still excluded, because it carries
neither an amount nor a marker. That is what separates it from a dateless tax row.

KNOWN HAZARDS THIS SCRIPT HANDLES (each one produced a wrong count while developing it)
  * The date and the narration are frequently SEPARATE spans; so are the amount and the
    marker. Rows must be assembled from spans, never read from a single span.
  * SBI wraps long narrations onto a CONTINUATION line that carries no date and no
    marker. Such a line is NOT a row -- counting it would over-count.
  * The leading band above "TRANSACTIONS FOR <NAME>" contains genuine rows; the header
    line itself is not a row.
  * Page furniture (footers, the account-summary grid, the rewards strip) can contain
    grouped decimals. The marker-band requirement excludes it.

Everything printed is reported; nothing is filtered by whether a model found it.
"""

import json
import os
import re
import sys

import fitz

PDF_DIR = "/Users/mayanck.bihani/Downloads/output/SBI/PDF"

# SBI transaction dates: "28 Apr 26". Two-digit year. Whitespace-flexible.
DATE_RE = re.compile(
    r"^\s*(\d{1,2})\s+"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
    r"(\d{2})\s*$", re.I)

# Indian digit grouping: 1,750.00 / 1,28,100.00 / 40.00 . Requires the paise part so a
# bare integer inside a narration cannot masquerade as an amount.
AMOUNT_RE = re.compile(r"^\s*`?\s*(\d{1,2}(?:,\d{2})*,\d{3}|\d{1,3}(?:,\d{3})*|\d+)\.(\d{2})\s*$")

MARKER_RE = re.compile(r"^\s*([CDTM])\s*$")

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"])}


def spans_of(page):
    out = []
    d = page.get_text("dict")
    for blk in d.get("blocks", []):
        for ln in blk.get("lines", []):
            for sp in ln.get("spans", []):
                t = sp.get("text", "")
                if not t.strip():
                    continue
                x0, y0, x1, y1 = sp["bbox"]
                out.append({"text": t, "x0": x0, "x1": x1,
                            "y0": y0, "y1": y1, "yc": (y0 + y1) / 2.0})
    return out


def group_lines(spans, ytol=1.6):
    """Bucket spans into printed lines by y-centre. Returns [(yc, [spans left->right])]."""
    lines = []
    for sp in sorted(spans, key=lambda s: (s["yc"], s["x0"])):
        for ln in lines:
            if abs(ln["yc"] - sp["yc"]) <= ytol:
                ln["spans"].append(sp)
                ln["yc"] = sum(s["yc"] for s in ln["spans"]) / len(ln["spans"])
                break
        else:
            lines.append({"yc": sp["yc"], "spans": [sp]})
    for ln in lines:
        ln["spans"].sort(key=lambda s: s["x0"])
    lines.sort(key=lambda l: l["yc"])
    return lines


def marker_band(all_lines):
    """Learn the x-band of the right-hand C/D/T/M column from the page itself.

    Hard-coding an x threshold would be a guess. Instead: take every lone-letter span
    matching C/D/T/M that is the RIGHTMOST span on its line, and use the observed x0
    range. If the page has none, no rows are on it.
    """
    xs = []
    for ln in all_lines:
        if not ln["spans"]:
            continue
        last = ln["spans"][-1]
        if MARKER_RE.match(last["text"]):
            xs.append(last["x0"])
    if not xs:
        return None
    xs.sort()
    return (xs[0] - 3.0, xs[-1] + 8.0)


def rows_of_page(page):
    spans = spans_of(page)
    lines = group_lines(spans)
    band = marker_band(lines)
    rows = []
    for ln in lines:
        sp = ln["spans"]
        if len(sp) < 2 or band is None:
            continue
        last = sp[-1]
        if not MARKER_RE.match(last["text"]):
            continue
        if not (band[0] <= last["x0"] <= band[1]):
            continue
        # A leading date is OPTIONAL: SBI's tax-continuation rows carry none and inherit
        # the date printed above them. See the module docstring -- requiring it was a bug.
        m = DATE_RE.match(sp[0]["text"])
        first = 1 if m else 0
        # find the amount: rightmost amount-shaped span left of the marker
        amt = None
        amt_idx = None
        for i in range(len(sp) - 2, first - 1, -1):
            am = AMOUNT_RE.match(sp[i]["text"])
            if am:
                amt = float(am.group(1).replace(",", "") + "." + am.group(2))
                amt_idx = i
                break
        if amt is None or amt_idx <= first - 1:
            continue
        desc = " ".join(s["text"].strip() for s in sp[first:amt_idx]).strip()
        desc = re.sub(r"\s+", " ", desc)
        rec = {
            "date": None, "date_printed": None, "date_inherited": not bool(m),
            "desc": desc, "amount": amt, "marker": last["text"].strip(),
            "y": round(ln["yc"], 2), "x_marker": round(last["x0"], 2),
        }
        if m:
            dd, mon, yy = int(m.group(1)), MONTHS[m.group(2).lower()], int(m.group(3))
            rec["date"] = f"{dd:02d}/{mon:02d}/{2000 + yy:04d}"
            rec["date_printed"] = sp[0]["text"].strip()
        rows.append(rec)
    return rows, lines


def rows_of_pdf(path):
    doc = fitz.open(path)
    out = []
    per_page = {}
    for pno in range(doc.page_count):
        rows, _ = rows_of_page(doc[pno])
        for r in rows:
            r["page"] = pno + 1
        per_page[pno + 1] = len(rows)
        out.extend(rows)
    doc.close()
    # Propagate the inherited date onto dateless tax-continuation rows, in printed order.
    last_date = None
    for r in out:
        if r["date"]:
            last_date = r["date"]
        else:
            r["date"] = last_date
    return out, per_page


def find_pdf(sid):
    for f in sorted(os.listdir(PDF_DIR)):
        if f.lower().endswith(".pdf") and re.match(rf"^decrypt_(?:encrypt_)?{sid}_", f):
            return os.path.join(PDF_DIR, f)
    raise SystemExit(f"no PDF for {sid}")


def main():
    sids = sys.argv[1:]
    if not sids:
        sids = sorted({re.match(r"^decrypt_(?:encrypt_)?(\d+)_", f).group(1)
                       for f in os.listdir(PDF_DIR) if f.lower().endswith(".pdf")})
    result = {}
    for sid in sids:
        path = find_pdf(sid)
        rows, per_page = rows_of_pdf(path)
        result[sid] = {"n_rows": len(rows), "per_page": per_page, "rows": rows}
        print(f"{sid:12s} printed_rows={len(rows):4d}  per_page={per_page}")
    dest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pdf_rowtruth.json")
    with open(dest, "w") as fh:
        json.dump(result, fh, indent=1)
    print("wrote", dest)


if __name__ == "__main__":
    main()
