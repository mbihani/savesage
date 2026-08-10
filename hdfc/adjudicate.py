#!/usr/bin/env python3
"""Adjudicate every Luna-vs-CSV disagreement against the PDF itself.

The CSV is the incumbent Gemini parser's output, not ground truth, so a disagreement
is not evidence about either side until the PDF is consulted. Each disagreement is
classified LUNA_WRONG / CSV_WRONG / BOTH_WRONG / AMBIGUOUS_IN_PDF using PyMuPDF
page+coordinate evidence, and a CORRECTED score is reported from the verdicts.

Adjudication is mechanical and conservative:
  * numbers  -> is the value printed in the PDF, next to its expected label?
  * strings  -> does the exact string appear in the page text?
Anything the mechanical test cannot separate is AMBIGUOUS_IN_PDF rather than being
guessed, and ambiguous cases are excluded from the corrected numerator/denominator
(and reported separately) so they cannot silently inflate either side.
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

# Printed labels that anchor each numeric statement-level field on HDFC layouts.
LABELS = {
    "statementLevelSummary.totalAmountDue": ["TOTAL AMOUNT DUE", "Total Amount Due",
                                             "Total Dues"],
    "statementLevelSummary.totalMinimumAmountDue": ["MINIMUM DUE", "Minimum Amount Due",
                                                    "MINIMUM AMOUNT DUE"],
    "statementLevelSummary.totalCreditLimit": ["TOTAL CREDIT LIMIT", "Credit Limit"],
    "statementLevelSummary.availableCreditLimit": ["AVAILABLE CREDIT LIMIT",
                                                   "Available Credit Limit"],
}


def page_texts(path):
    d = fitz.open(path)
    try:
        pages = [d[i].get_text() for i in range(d.page_count)]
    finally:
        d.close()
    # Fold C0 controls to spaces: HDFC uses \x01 as a word separator in 7/281 PDFs and
    # Python's \s does not match it, so a correct value can otherwise read as UNPRINTED.
    # Same normalisation as adjudicate_txn.flat_text.
    return [re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", p) for p in pages]


def num_variants(v):
    """Indian-grouped and plain renderings of a number, as PRINTED."""
    if v is None:
        return []
    out = set()
    f = float(v)
    for base in {f, round(f, 2)}:
        if float(base).is_integer():
            n = int(base)
            out.add(str(n))
            out.add(f"{n}.00")
            out.add(indian_group(n))
            out.add(indian_group(n) + ".00")
        out.add(f"{base:.2f}")
    return [o for o in out if o]


def indian_group(n):
    """1234567 -> '12,34,567' (lakh/crore grouping, as HDFC prints)."""
    s = str(abs(int(n)))
    if len(s) <= 3:
        g = s
    else:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        g = ",".join(parts + [tail])
    return ("-" if n < 0 else "") + g


def find_num_near_label(path, field, value):
    """-> (found, evidence). A number counts as supported when it is printed within a
    short window after one of the field's labels, on the same page."""
    if value is None:
        return False, None
    variants = num_variants(value)
    if not variants:
        return False, None
    labels = LABELS.get(field, [])
    for pno, t in enumerate(page_texts(path)):
        flat = re.sub(r"[ \t]", "", t)
        for lab in labels:
            fl = re.sub(r"[ \t]", "", lab)
            for mm in re.finditer(re.escape(fl), flat, re.I):
                window = flat[mm.end():mm.end() + 60]
                for v in variants:
                    if re.sub(r"[ \t]", "", v) in window:
                        return True, {"page": pno + 1, "label": lab, "printed": v,
                                      "window": window[:48].replace("\n", "|")}
    # fall back: value printed anywhere at all (weaker support)
    for pno, t in enumerate(page_texts(path)):
        flat = re.sub(r"[ \t]", "", t)
        for v in variants:
            if re.sub(r"[ \t]", "", v) in flat:
                return "ELSEWHERE", {"page": pno + 1, "label": None, "printed": v}
    return False, None


def find_str(path, value, limit=2):
    if value is None or str(value).strip() == "":
        return False, None
    s = str(value).strip()
    d = fitz.open(path)
    try:
        for pno in range(d.page_count):
            for r in (d[pno].search_for(s) or [])[:limit]:
                return True, {"page": pno + 1,
                              "rect": [round(x, 1) for x in (r.x0, r.y0, r.x1, r.y1)]}
    except Exception:
        pass
    finally:
        d.close()
    return False, None


def verdict(luna_ok, csv_ok, luna_null, csv_null):
    """Map (is each side supported by the PDF?) -> verdict."""
    strong = lambda x: x is True
    weak = lambda x: x == "ELSEWHERE"
    if strong(luna_ok) and not (strong(csv_ok) or weak(csv_ok)):
        return "CSV_WRONG"
    if strong(csv_ok) and not (strong(luna_ok) or weak(luna_ok)):
        return "LUNA_WRONG"
    if strong(luna_ok) and strong(csv_ok):
        return "AMBIGUOUS_IN_PDF"      # both printed somewhere valid -> cannot separate
    if not luna_ok and not csv_ok:
        # neither value is printed: if one side said null and null is plausible, the
        # other side fabricated. If both non-null, both are wrong.
        if luna_null and not csv_null:
            return "CSV_WRONG"
        if csv_null and not luna_null:
            return "LUNA_WRONG"
        return "BOTH_WRONG"
    if weak(luna_ok) and strong(csv_ok):
        return "LUNA_WRONG"
    if weak(csv_ok) and strong(luna_ok):
        return "CSV_WRONG"
    return "AMBIGUOUS_IN_PDF"


def main():
    matched, _, _ = H.build_join()
    luna = S.load_run(os.path.join(HERE, "phase3_refined"))
    prof = json.load(open(os.path.join(HERE, "corpus_profile.json")))
    tune = {p["sid"] for p in prof["sample"]}

    findings = []
    counts = defaultdict(Counter)

    for m in matched:
        r = luna.get(m["sid"])
        pj = (r or {}).get("parsed_json")
        if not isinstance(pj, dict):
            continue
        csv_x = S.csv_extraction(m["csv_row"])

        for name, scope, path, kind in S.STMT_FIELDS:
            if name.endswith("utilisationPercent"):
                continue           # not printed anywhere in the corpus; handled as derived
            lv = S.get_field(pj, scope, path)
            cv = S.get_field(csv_x, scope, path)
            if S.values_equal(kind, lv, cv):
                continue
            lnull = S.canon(kind, lv) is None
            cnull = S.canon(kind, cv) is None
            if kind == "num":
                lok, lev = find_num_near_label(m["path"], name, S.norm_num(lv))
                cok, cev = find_num_near_label(m["path"], name, S.norm_num(cv))
            else:
                lok, lev = find_str(m["path"], lv)
                cok, cev = find_str(m["path"], cv)
            if lnull:
                lok, lev = False, None
            if cnull:
                cok, cev = False, None
            v = verdict(lok, cok, lnull, cnull)
            counts[name][v] += 1
            findings.append({
                "sid": m["sid"], "pdf": m["filename"], "field": name,
                "luna": lv, "csv": cv, "verdict": v,
                "luna_supported": lok, "csv_supported": cok,
                "luna_evidence": lev, "csv_evidence": cev,
                "heldout": m["sid"] not in tune,
            })

    # corrected score per field: of the separable disagreements, who was right?
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
        "method": ("each Luna-vs-CSV disagreement adjudicated against the PDF with "
                   "PyMuPDF page/coordinate evidence; AMBIGUOUS_IN_PDF excluded from "
                   "the corrected share rather than guessed"),
        "statements_examined": len(matched),
        "total_disagreements": len(findings),
        "by_field": corrected,
        "overall": dict(Counter(f["verdict"] for f in findings)),
        "overall_heldout": dict(Counter(f["verdict"] for f in findings if f["heldout"])),
        "findings": findings,
    }
    H.G.atomic_write_json(os.path.join(HERE, "adjudication_stmt.json"), out)

    print(f"statements={len(matched)} disagreements={len(findings)}")
    print("overall:", out["overall"])
    print("held-out:", out["overall_heldout"])
    for f, c in sorted(corrected.items(), key=lambda kv: -kv[1]["disagreements"]):
        print(f"  {f:50s} n={c['disagreements']:4d} LUNA_WRONG={c['LUNA_WRONG']:4d} "
              f"CSV_WRONG={c['CSV_WRONG']:4d} BOTH={c['BOTH_WRONG']:3d} "
              f"AMBIG={c['AMBIGUOUS_IN_PDF']:4d}")


if __name__ == "__main__":
    main()
