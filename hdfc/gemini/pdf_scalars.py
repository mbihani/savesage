"""Label-anchored extraction of the HDFC statement-level scalars, as a PDF reference.

Used to adjudicate statementLevelSummary.* / statementMeta.* without trusting any model.

THREE DEFECTS FOUND BY VALIDATING THIS PROBE BEFORE USING IT
------------------------------------------------------------
A first version of this file produced `availableCreditLimit == totalMinimumAmountDue` on
11 of 15 files and never resolved `totalCreditLimit` at all. Rather than ship its output,
the causes were found and fixed. All three are the classic false-accusation generators:

  1. PROSE MATCHES. HDFC's IMPORTANT INFORMATION block contains the sentence "THE
     AVAILABLE CREDIT LIMIT SHOWED HEREIN TAKES INTO ACCOUNT CHARGES INCURRED BUT NOT
     BILLED." A substring search bound the *label* to whatever number sat near that
     sentence. Fixed by requiring the matched span to be a CLEAN LABEL -- its text, once
     whitespace-stripped, may exceed the label itself by only a few characters -- so a
     label embedded in a sentence is rejected.
  2. LETTER-SPACED LABELS. The Pixel Play layout emits labels with spaces injected inside
     words: 'T OTAL CRED IT  LIMIT', 'T OTAL AMOUNT  D UE', 'D UE D AT E'. A normal
     whitespace-collapse still leaves 'T OTAL CRED IT LIMIT' != 'TOTAL CREDIT LIMIT'.
     Fixed by comparing with ALL whitespace removed on both sides.
  3. LABELS AND VALUES IN SEPARATE ROWS. On both layouts the headline block emits N
     labels as one row and their N values as the next row, column-aligned. A
     reading-order regex pairs label[0] with value[0] correctly but silently
     mis-pairs the rest whenever a label spans two rows. Fixed by binding each label to
     the value whose x-centre is nearest, on the nearest row BELOW it within 45pt.

ITFRUPEE AWARENESS IS MANDATORY HERE
------------------------------------
The rupee sign is a separate `ITFRupee` span reading "C", so an amount arrives as the
span pair ('C', ' 5,348.00'). A leading standalone 'C' is stripped ONLY when that span's
font is ITFRupee -- never by pattern-matching a literal 'C', which would eat a real
letter. Indian digit grouping (1,94,022.00) is handled by plain comma removal, which is
grouping-agnostic, so the lakh digit cannot be lost.
"""

import re
from collections import defaultdict

import fitz

# Commas are stripped BEFORE matching, never matched around. Two reasons, both measured:
# Indian grouping ("3,60,000" -> 360000) must not lose the lakh digit, and HDFC's own
# template emits a NEGATIVE total as "-,208.34" -- a comma at the thousands position of a
# 3-digit number, straight after the sign. Arithmetic on decrypt_1741303904 confirms the
# intended value is -208.34 (PREVIOUS DUES 2,057.66 - PAYMENTS 2,266.00). A regex written
# to match commas in place rejects that string and the field silently becomes null.
AMOUNT = re.compile(r"^-?\d+(?:\.\d{1,2})?$")
DATE_TXT = re.compile(r"^\s*(\d{1,2}\s+[A-Za-z]{3,9},?\s+\d{4})\s*$")
# Non-date text that legitimately occupies a date slot, per the dueDate rule.
NONDATE = re.compile(r"^(?:NIL|N/?A|IMMEDIATELY|PAY\s+IMMEDIATELY|DUE\s+IMMEDIATELY|-{1,2})$",
                     re.I)

# label -> schema leaf. `_`-prefixed leaves are decoys captured only to keep them from
# being mistaken for the real field (AVAILABLE CASH LIMIT vs AVAILABLE CREDIT LIMIT).
LABELS = [
    ("TOTAL AMOUNT DUE", "totalAmountDue"),
    ("MINIMUM AMOUNT DUE", "totalMinimumAmountDue"),
    ("MINIMUM DUE", "totalMinimumAmountDue"),
    ("TOTAL CREDIT LIMIT", "totalCreditLimit"),
    ("AVAILABLE CREDIT LIMIT", "availableCreditLimit"),
    ("AVAILABLE CASH LIMIT", "_availableCashLimit"),
    ("STATEMENT DATE", "statementDate"),
    ("PAYMENT DUE DATE", "dueDate"),
    ("DUE DATE", "dueDate"),
]
_DATE_LEAVES = {"statementDate", "dueDate"}
# 'MINIMUM DUES' / 'CURRENT DUES' belong to the Past-Dues grid, not the headline.
_BLOCK = {"MINIMUMDUES", "CURRENTDUES", "PREVIOUSSTATEMENTDUES"}


def _sq(s):
    """Squeeze: uppercase with ALL whitespace removed -- defeats letter-spacing."""
    return re.sub(r"\s+", "", s).upper()


def _rows(page, ytol=2.5):
    b = defaultdict(list)
    for blk in page.get_text("dict").get("blocks", []):
        for ln in blk.get("lines", []):
            for sp in ln.get("spans", []):
                if sp["text"].strip():
                    b[round(sp["bbox"][1] / ytol)].append(sp)
    return [(k * ytol, sorted(v, key=lambda s: s["bbox"][0])) for k, v in sorted(b.items())]


def _values_in_row(spans):
    """-> [(x_centre, kind, value, had_currency_marker)] for amounts and dates."""
    out, i = [], 0
    while i < len(spans):
        sp = spans[i]
        cur = False
        if "ITFRupee" in sp["font"] and sp["text"].strip() == "C":
            cur = True
            i += 1
            if i >= len(spans):
                break
            sp = spans[i]
        txt = sp["text"]
        t2 = txt.replace("₹", "").replace("+", "").replace(",", "").strip()
        x = (sp["bbox"][0] + sp["bbox"][2]) / 2
        m = AMOUNT.match(t2)
        if m:
            out.append((x, "amount", float(t2), cur))
        else:
            flat = re.sub(r"\s+", " ", txt).strip()
            d = DATE_TXT.match(flat)
            if d:
                out.append((x, "date", d.group(1).strip(), cur))
            elif NONDATE.match(flat):
                # A NON-DATE dueDate is real data, not a missing value: two files print
                # "DUE DATE / Nil" (both are credit-balance statements with nothing due).
                # The dueDate rule says preserve such text verbatim, so the reference
                # value must be "Nil" -- recording null here would make a correct model
                # answer look wrong, and a null answer look right.
                out.append((x, "date", flat, cur))
        i += 1
    return out


def _label_hits(rows):
    """Clean-label occurrences only. -> [(leaf, row_index, span, slack)]"""
    hits = []
    for ri, (_, spans) in enumerate(rows):
        for sp in spans:
            sq = _sq(sp["text"])
            if sq in _BLOCK:
                continue
            for lab, leaf in LABELS:
                lsq = _sq(lab)
                if lsq not in sq:
                    continue
                # CLEAN-LABEL TEST: reject labels embedded in a sentence.
                slack = len(sq) - len(lsq)
                if slack > 8:
                    continue
                hits.append((leaf, ri, sp, slack))
                break
    return hits


def extract(path):
    """-> {leaf: {value, page, label_text, had_currency_marker, dy}}"""
    doc = fitz.open(path)
    best = {}
    for pno in range(len(doc)):
        rows = _rows(doc[pno])
        for leaf, ri, sp, slack in _label_hits(rows):
            want = "date" if leaf in _DATE_LEAVES else "amount"
            lx = (sp["bbox"][0] + sp["bbox"][2]) / 2
            ly = sp["bbox"][3]
            cands = []
            # same row, to the right of the label
            for v in _values_in_row(rows[ri][1]):
                if v[1] == want and v[0] > sp["bbox"][2] - 1:
                    cands.append((0.0, abs(v[0] - lx), v))
            # nearest rows below, column-aligned
            for rj in range(ri + 1, len(rows)):
                dy = rows[rj][0] - ly
                if dy < -1:
                    continue
                if dy > 45:
                    break
                for v in _values_in_row(rows[rj][1]):
                    if v[1] == want:
                        cands.append((dy, abs(v[0] - lx), v))
            # COLUMN ALIGNMENT DOMINATES, not row proximity. A first version sorted by
            # (row, column) and so bound AVAILABLE CREDIT LIMIT to the MINIMUM DUE value
            # sitting 242pt away in x but one text-row nearer in y, on 11 of 15 files.
            # In these headline grids the value is essentially always x-aligned with its
            # label (dx ~ 2pt) while the decoy is a different column entirely, so dx is
            # the discriminating signal and dy only breaks ties within a column.
            # Per-kind dx cap. Amounts sit in narrow grid columns (~80-130pt) so a tight
            # cap is what rejects the neighbouring column. DATE labels are different:
            # "Statement Date"@193 pairs with "17 Mar, 2026"@339 on the SAME row, ~160pt
            # away, so a 70pt cap silently nulls statementDate on every file.
            cands = [c for c in cands if c[1] <= (250 if want == "date" else 70)]
            if not cands:
                continue
            cands.sort(key=lambda t: (round(t[1] / 8.0), t[0]))
            dy, dx, v = cands[0]
            cand = {"value": v[2], "page": pno + 1, "label_text": sp["text"].strip(),
                    "had_currency_marker": v[3], "dy": round(dy, 1),
                    "dx": round(dx, 1), "slack": slack}
            # keep the tightest label match for each leaf
            prev = best.get(leaf)
            if prev is None or (slack, dy, dx) < (prev["slack"], prev["dy"], prev["dx"]):
                best[leaf] = cand
    doc.close()
    return best
