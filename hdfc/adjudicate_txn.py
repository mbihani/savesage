#!/usr/bin/env python3
"""Adjudicate Luna-vs-CSV TRANSACTION disagreements against the PDF.

`description` is the dominant disagreement class, and it is the one class where the
PDF gives a clean mechanical verdict: the prompt requires the narration VERBATIM, so
whichever side's string is actually printed in the page text is the correct one.

For `amount` the verdict rests on the schema contract (transactions[].amount is ALWAYS
POSITIVE, sign lives in `direction`) plus whether the magnitude is printed.
For `direction` the verdict rests on the printed credit markers established in Phase 2:
a leading `+` or a trailing `Cr` means CREDIT; a leading `C` is the rupee sign and
carries no direction information.

Pairing is description-only 1:1 (score_lib), so `date`/`amount`/`direction` verdicts
are not circular.
"""
import json
import os
import re
import sys
from collections import Counter, defaultdict

import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hdfc_lib as H
import score_lib as S

HERE = os.path.dirname(os.path.abspath(__file__))


def flat_text(path):
    d = fitz.open(path)
    try:
        t = "\n".join(d[i].get_text() for i in range(d.page_count))
    finally:
        d.close()
    # HDFC separates some words with C0 CONTROL characters rather than spaces --
    # measured: 25 occurrences of \x01 (SOH) across 7 of the 281 PDFs, e.g.
    # "AGGREGATOR\x01EMI\x01-\x01OFFUS\x01CREDIT". Python's \s does NOT match \x01, so
    # leaving these in made a correct extraction look UNPRINTED and produced a spurious
    # BOTH_WRONG verdict on a row where both sides were in fact right. Fold every C0
    # control (except the real newline/tab) to a space BEFORE any comparison.
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", t)


def norm_space(s):
    """Whitespace-normalise, treating C0 controls as whitespace (see flat_text)."""
    return re.sub(r"[\s\x00-\x08\x0b\x0c\x0e-\x1f]+", " ", (s or "")).strip()


def printed_verbatim(pdf_txt, value):
    """True  = the string is printed as part of a SINGLE printed line.
    'FLAT' = present only once whitespace is removed entirely (i.e. the side altered
             internal spacing, or spans a line break).
    False  = not printed at all.

    Matching is per-LINE, not against the whole page flattened to one string. HDFC
    prints row-type badges on their own line ("EMI\\nPRESTIGEGHAZIABAD"), so a
    page-level match would treat the fabricated "EMI PRESTIGEGHAZIABAD" as genuinely
    printed and invert a verdict that PDF inspection settles the other way.
    """
    if not value:
        return False
    v = norm_space(value)
    if not v:
        return False
    for line in pdf_txt.split("\n"):
        if v in norm_space(line):
            return True
    # Not on one line. A narration may legitimately WRAP mid-string -- HDFC breaks long
    # rows inside the reference, e.g. "IGST-...-RATE 18.0 -06 (Ref#\n0999...587)". That
    # is still the printed narration. What is NOT legitimate is joining a standalone
    # row-type BADGE line to the narration below it ("EMI\nPRESTIGEGHAZIABAD").
    # The two cases are separated by what sits before the break: a badge is a short
    # all-caps token that stands alone on its line.
    if _spans_break_legitimately(pdf_txt, v):
        return True
    nov = re.sub(r"\s", "", v)
    if nov and nov in re.sub(r"\s", "", pdf_txt):
        return "FLAT"
    return False


BADGE_LINE = re.compile(r"^(?:EMI|UPI|POS|ATM|INT|NEFT|IMPS|CASH|FEE|REV)$", re.I)

# A line that is a VALUE FROM ANOTHER COLUMN, not narration. HDFC prints the
# foreign-currency amount on its own line between the narration and the rupee amount
# ("CURSOR, AI POWERED IDECURSOR.COM" / "USD 20.00" / " C 1,849.76"), and also puts
# bare reward-point counts there ("+ 76"). Joining such a line onto the narration is a
# column-bleed defect, not a legitimate wrap, so a side that does it must not be
# credited as having reproduced the printed narration.
COLUMN_VALUE_LINE = re.compile(
    r"^(?:[A-Z]{3}\s*[\d,]+(?:\.\d+)?"        # 'USD 20.00'
    r"|\+?\s*[\d,]+(?:\.\d+)?"                 # '+ 76', '1,849.76'
    r"|C\s?[\d,]+(?:\.\d+)?"                   # rupee-glyph amount
    r"|l)$", re.I)


def _spans_break_legitimately(pdf_txt, v):
    """True when `v` matches consecutive printed lines AND every line it swallows after
    the first is genuinely a continuation of the narration -- not a badge line and not
    another column's value."""
    lines = [norm_space(l) for l in pdf_txt.split("\n")]
    for i, first in enumerate(lines):
        if not first or not v.startswith(first):
            continue
        if BADGE_LINE.match(first) or COLUMN_VALUE_LINE.match(first):
            return False
        acc = first
        for nxt in lines[i + 1:i + 4]:
            if not nxt or BADGE_LINE.match(nxt) or COLUMN_VALUE_LINE.match(nxt):
                break             # swallowing a column value is not a wrap
            acc = norm_space(acc + " " + nxt)
            if acc == v:
                return True
            if not v.startswith(acc):
                break
    return False


def desc_verdict(pdf_txt, lv, cv):
    l = printed_verbatim(pdf_txt, lv)
    c = printed_verbatim(pdf_txt, cv)
    if l is True and c is not True:
        return "CSV_WRONG", l, c
    if c is True and l is not True:
        return "LUNA_WRONG", l, c
    if l is True and c is True:
        # BOTH are printed -- but a substring test alone is too weak here: a side that
        # TRUNCATES the narration ("...RATE 18.0" for "...RATE 18.0 -06", or a dropped
        # "(Ref# ...)") is still trivially a substring of the printed text, so scoring
        # it AMBIGUOUS would hide a real fidelity defect. The prompt requires the
        # COMPLETE printed narration, so the longer string wins whenever one side's
        # value is a strict prefix/substring of the other's.
        ln, cn = norm_space(lv), norm_space(cv)
        if ln != cn:
            if cn in ln:
                return "CSV_WRONG", l, c          # CSV is a truncation of Luna's
            if ln in cn:
                return "LUNA_WRONG", l, c         # Luna is a truncation of the CSV's
        return "AMBIGUOUS_IN_PDF", l, c
    # neither is verbatim: prefer the one that is at least present when de-spaced
    if l == "FLAT" and not c:
        return "CSV_WRONG", l, c
    if c == "FLAT" and not l:
        return "LUNA_WRONG", l, c
    if not l and not c:
        return "BOTH_WRONG", l, c
    return "AMBIGUOUS_IN_PDF", l, c


def indian_amt(v):
    """HDFC prints amounts with lakh/crore grouping: 194022.0 -> '1,94,022.00'."""
    if v is None:
        return ""
    neg = v < 0
    s = f"{abs(float(v)):.2f}"
    whole, frac = s.split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return ("-" if neg else "") + whole + "." + frac


_AMT_CTX = 90


def amount_verdict(pdf_txt, lv, cv):
    """The schema mandates a positive magnitude. A negative amount is a contract
    violation regardless of what the PDF prints."""
    ln, cn = S.norm_num(lv), S.norm_num(cv)
    if ln is None and cn is None:
        return "AMBIGUOUS_IN_PDF"
    lneg = ln is not None and ln < 0
    cneg = cn is not None and cn < 0
    if lneg and not cneg:
        return "LUNA_WRONG"
    if cneg and not lneg:
        return "CSV_WRONG"        # CSV negates credits; the schema forbids it
    if lneg and cneg:
        return "BOTH_WRONG"
    # Both positive but different: whose magnitude is printed?
    #
    # This must be a DIGIT-BOUNDARY match, not a substring test. Flattening commas turns
    # the printed '1,94,022.00' into '194022.00', of which the rival value '94022.00' is
    # a suffix -- so a substring test calls BOTH sides printed and returns
    # AMBIGUOUS_IN_PDF, hiding exactly the lakh-digit defect this check exists to catch
    # (that defect was real: a prompt rule ate the leading '1'). Candidates are therefore
    # matched with a guard that the neighbouring characters are not digits/commas.
    def printed(x):
        if x is None:
            return False
        cands = {f"{x:.2f}", indian_amt(x)}
        if float(x).is_integer():
            cands.add(f"{int(x)}")
        for c in cands:
            if not c:
                continue
            # allow the PDF to use any comma grouping (or none) for this candidate
            pat = r"(?<![\d,.])" + r",?".join(re.escape(ch) for ch in c.replace(",", "")) \
                  + r"(?![\d])"
            if re.search(pat, pdf_txt):
                return True
        return False
    lp, cp = printed(ln), printed(cn)
    if lp and not cp:
        return "CSV_WRONG"
    if cp and not lp:
        return "LUNA_WRONG"
    if not lp and not cp:
        return "BOTH_WRONG"
    return "AMBIGUOUS_IN_PDF"


def direction_verdict(pdf_txt, desc, lv, cv, amount=None):
    """Credit iff the row prints a leading '+' or a trailing 'Cr'. A leading 'C' is
    the rupee glyph (established in Phase 2) and is NOT a credit marker.

    HDFC repeats narrations heavily (one statement prints AMAZON WEB SERVICESMUMBAI
    several times), so inspecting only the FIRST occurrence judges a row against some
    other row's marker. Every occurrence is therefore examined:

      * if the row's own `amount` pins exactly one occurrence, that one decides;
      * else if all occurrences carry the same marker, the shared marker decides;
      * else the PDF cannot separate the two sides -> AMBIGUOUS_IN_PDF.

    Using `amount` only to LOCATE the row is not circular for a `direction` verdict:
    the two fields are independent, and no amount is scored by this function.
    """
    if not desc:
        return "AMBIGUOUS_IN_PDF", None
    d = norm_space(desc)
    flat = norm_space(pdf_txt)
    if d not in flat:
        flat = re.sub(r"\s", "", flat)
        d = re.sub(r"\s", "", d)
        if d not in flat:
            return "AMBIGUOUS_IN_PDF", None

    wins = []
    start = 0
    while True:
        i = flat.find(d, start)
        if i < 0:
            break
        wins.append(flat[i + len(d): i + len(d) + 40])
        start = i + 1
    if not wins:
        return "AMBIGUOUS_IN_PDF", None

    def marker(w):
        return "CREDIT" if (re.match(r"\s*\+", w)
                            or re.search(r"\d\s*(?:Cr|CR)\b", w)) else "DEBIT"

    chosen = None
    an = S.norm_num(amount)
    if an is not None:
        cands = {f"{an:.2f}", indian_amt(an), f"{int(an)}" if float(an).is_integer() else ""}
        cands = {re.sub(r"\s", "", c) for c in cands if c}
        hit = [w for w in wins if any(c in re.sub(r"\s", "", w) for c in cands)]
        if len(hit) == 1:
            chosen = hit[0]
    if chosen is None:
        marks = {marker(w) for w in wins}
        if len(marks) > 1:
            return "AMBIGUOUS_IN_PDF", {"occurrences": len(wins),
                                        "markers_disagree": sorted(marks),
                                        "reason": "repeated narration, markers differ"}
        chosen = wins[0]

    win = chosen
    truth = marker(win)
    lk = (S.norm_key(lv) or "").upper()
    ck = (S.norm_key(cv) or "").upper()
    lok, cok = lk == truth, ck == truth
    ev = {"window": win[:32], "inferred": truth, "occurrences": len(wins)}
    if lok and not cok:
        return "CSV_WRONG", ev
    if cok and not lok:
        return "LUNA_WRONG", ev
    if lok and cok:
        return "AMBIGUOUS_IN_PDF", ev
    return "BOTH_WRONG", ev


def main():
    matched, _, _ = H.build_join()
    luna = S.load_run(os.path.join(HERE, "phase3_refined"))
    prof = json.load(open(os.path.join(HERE, "corpus_profile.json")))
    tune = {p["sid"] for p in prof["sample"]}

    counts = defaultdict(Counter)
    findings = []
    rowcount = []

    for m in matched:
        r = luna.get(m["sid"])
        pj = (r or {}).get("parsed_json")
        if not isinstance(pj, dict):
            continue
        csv_x = S.csv_extraction(m["csv_row"])
        lt_all = pj.get("transactions") or []
        ct_all = csv_x.get("transactions") or []
        pairs, un_l, un_c = S.match_transactions(lt_all, ct_all)
        pdf_txt = None

        rowcount.append({"sid": m["sid"], "luna": len(lt_all), "csv": len(ct_all),
                         "pairs": len(pairs), "luna_only": len(un_l),
                         "csv_only": len(un_c), "heldout": m["sid"] not in tune})

        for i, j, sim in pairs:
            lt, ct = lt_all[i] or {}, ct_all[j] or {}
            for f in S.TXN_FIELDS:
                if S.txn_field_equal(f, lt.get(f), ct.get(f)):
                    continue
                if pdf_txt is None:
                    pdf_txt = flat_text(m["path"])
                if f == "description":
                    v, lp, cp = desc_verdict(pdf_txt, lt.get(f), ct.get(f))
                    ev = {"luna_printed": lp, "csv_printed": cp}
                elif f == "amount":
                    v = amount_verdict(pdf_txt, lt.get(f), ct.get(f))
                    ev = None
                elif f == "direction":
                    # amount is passed ONLY to disambiguate which printed occurrence of a
                    # repeated narration this row is; it is not a scored input here.
                    v, ev = direction_verdict(pdf_txt,
                                              lt.get("description") or ct.get("description"),
                                              lt.get(f), ct.get(f),
                                              amount=lt.get("amount") or ct.get("amount"))
                else:
                    # date / currency: mechanically check whether either value is printed
                    v, ev = "AMBIGUOUS_IN_PDF", None
                    if f == "date":
                        lv, cv = S.norm_date(lt.get(f)), S.norm_date(ct.get(f))
                        def dpr(x):
                            if not x:
                                return False
                            dd, mm_, yy = x.split("/")
                            return any(p in pdf_txt for p in
                                       (f"{dd}/{mm_}/{yy}", f"{dd}/{mm_}/{yy[2:]}"))
                        lp, cp = dpr(lv), dpr(cv)
                        if lp and not cp:
                            v = "CSV_WRONG"
                        elif cp and not lp:
                            v = "LUNA_WRONG"
                        elif not lp and not cp:
                            v = "BOTH_WRONG"
                        ev = {"luna_printed": lp, "csv_printed": cp}
                counts[f][v] += 1
                findings.append({
                    "sid": m["sid"], "field": f, "verdict": v, "sim": round(sim, 3),
                    "luna": lt.get(f), "csv": ct.get(f),
                    "luna_desc": lt.get("description"), "csv_desc": ct.get("description"),
                    "evidence": ev, "heldout": m["sid"] not in tune,
                })

    corrected = {}
    for f, c in counts.items():
        sep = c["LUNA_WRONG"] + c["CSV_WRONG"] + c["BOTH_WRONG"]
        corrected[f] = {
            "disagreements": sum(c.values()),
            "LUNA_WRONG": c["LUNA_WRONG"], "CSV_WRONG": c["CSV_WRONG"],
            "BOTH_WRONG": c["BOTH_WRONG"], "AMBIGUOUS_IN_PDF": c["AMBIGUOUS_IN_PDF"],
            "separable": sep,
            "luna_right_share_of_separable": round(c["CSV_WRONG"] / sep, 4) if sep else None,
        }

    out = {
        "method": ("transaction pairing is DESCRIPTION-ONLY 1:1; each disagreement then "
                   "adjudicated against PDF text. description: whichever side is printed "
                   "verbatim wins. amount: schema mandates positive magnitude. direction: "
                   "credit iff leading '+' or trailing 'Cr' (a leading 'C' is the rupee "
                   "glyph, not a credit marker)."),
        "by_field": corrected,
        "overall": dict(Counter(f["verdict"] for f in findings)),
        "row_counts": rowcount,
        "findings": findings,
    }
    H.G.atomic_write_json(os.path.join(HERE, "adjudication_txn.json"), out)

    print("txn-level adjudication (Luna vs CSV), verdicts by field:")
    for f, c in sorted(corrected.items(), key=lambda kv: -kv[1]["disagreements"]):
        print(f"  {f:12s} n={c['disagreements']:5d} LUNA_WRONG={c['LUNA_WRONG']:5d} "
              f"CSV_WRONG={c['CSV_WRONG']:5d} BOTH={c['BOTH_WRONG']:4d} "
              f"AMBIG={c['AMBIGUOUS_IN_PDF']:5d}  luna_right_share={c['luna_right_share_of_separable']}")
    tot_l = sum(c["luna"] for c in rowcount)
    tot_c = sum(c["csv"] for c in rowcount)
    print(f"\nrows: luna={tot_l} csv={tot_c} over {len(rowcount)} statements")


if __name__ == "__main__":
    main()
