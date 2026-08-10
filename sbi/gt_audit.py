#!/usr/bin/env python3
"""Audit the Opus-5 GROUND TRUTH itself against the PDF.

Why this exists
---------------
Every accuracy number in this report is measured against the Opus-5 GT. That makes
the GT an instrument, and an instrument has to be calibrated before its readings are
trusted. If the GT is silently wrong on a field, the challenger is penalised for
being RIGHT -- which produces a confidently wrong verdict, the single worst failure
mode available to this evaluation.

The trigger was concrete. `statementMeta.dueDate` scored 74.4% for Luna with 11
"hallucinated_when_GT_null" cases. On inspection all 11 were Luna emitting
`NO PAYMENT REQUIRED` and AGREEING with the incumbent CSV while the GT returned
null. SBI prints the literal string `NO PAYMENT REQUIRED` in the Payment Due Date
column of the summary band when the account is fully paid. So the GT dropped a value
that is printed verbatim in the document, and the scorer charged it to Luna.

This script adjudicates GT-vs-Luna null-disagreements against the PDF text and
classifies them GT_WRONG / LUNA_WRONG / AMBIGUOUS_IN_PDF. Output feeds the report's
GT-calibration section. It does NOT rewrite the GT or the scores: the primary tables
stay as-measured against the unmodified GT, and this audit is reported alongside them
so the reader can discount the affected field themselves.
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fitz  # noqa: E402
import sbi_lib as L  # noqa: E402
import score_lib_sbi as S  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))

# Scalar fields where a null-vs-populated split is worth adjudicating.
AUDIT_FIELDS = [
    "statementMeta.dueDate",
    "statementMeta.statementDate",
    "statementMeta.issuerName",
    "cards[].cardMeta.network",
    "cards[].cardMeta.cardDisplayName",
    "statementLevelSummary.totalAmountDue",
    "statementLevelSummary.totalMinimumAmountDue",
    "statementLevelSummary.totalCreditLimit",
    "statementLevelSummary.availableCreditLimit",
]

MONS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov",
        "Dec"]


def pdf_text(path, cache={}):
    if path not in cache:
        d = fitz.open(path)
        cache[path] = "".join(p.get_text() for p in d)
        d.close()
    return cache[path]


def variants(v):
    """Surface forms SBI might print for a value."""
    if v is None:
        return []
    s = str(v).strip()
    if not s:
        return []
    out = {s, s.upper()}
    n = S.date_norm(s)
    if n and len(str(n).split("/")) == 3:
        d, m, y = str(n).split("/")
        try:
            mon = MONS[int(m) - 1]
            out |= {f"{d} {mon} {y}", f"{d} {mon} {y[-2:]}",
                    f"{int(d):02d} {mon} {y}", f"{int(d)} {mon} {y}"}
        except (ValueError, IndexError):
            pass
    # money: 12345.67 -> 12,345.67 / 1,23,45.67 (Indian grouping handled loosely)
    try:
        f = float(str(v).replace(",", ""))
        out |= {f"{f:,.2f}", f"{f:.2f}", _indian(f)}
    except (TypeError, ValueError):
        pass
    return [x for x in out if x]


def _indian(f):
    """1234567.89 -> 12,34,567.89 (SBI uses Indian digit grouping)."""
    neg = f < 0
    s = f"{abs(f):.2f}"
    i, d = s.split(".")
    if len(i) > 3:
        head, tail = i[:-3], i[-3:]
        head = re.sub(r"(?<=\d)(?=(\d\d)+$)", ",", head)
        i = f"{head},{tail}"
    return ("-" if neg else "") + f"{i}.{d}"


def printed(path, v):
    """Is any surface form of v present in the PDF text?"""
    t = pdf_text(path)
    tn = re.sub(r"\s+", " ", t)
    for s in variants(v):
        if s in t or re.sub(r"\s+", " ", s) in tn:
            return s
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--luna", default=os.path.join(ROOT, "run_luna_refined"))
    ap.add_argument("--gt", default=os.path.join(ROOT, "run_gt"))
    ap.add_argument("--out", default=os.path.join(ROOT, "gt_audit.json"))
    a = ap.parse_args()

    corpus = {s: p for s, f, p in L.discover_pdfs()}
    csvref, _ = S.load_csv_incumbent()
    gt = S.load_arm(a.gt)
    luna = S.load_arm(a.luna)

    gtp = {k: S.parsed_of(v) for k, v in gt.items()}
    lup = {k: S.parsed_of(v) for k, v in luna.items()}
    ids = [s for s in sorted(corpus, key=lambda x: (0, int(x)) if x.isdigit() else (1, 0, x))
           if gtp.get(s) and lup.get(s)]

    tally = Counter()
    per_field = defaultdict(Counter)
    items = []

    for sid in ids:
        g, l = gtp[sid], lup[sid]
        inc = csvref.get(sid) or {}
        path = corpus[sid]
        for field in AUDIT_FIELDS:
            gv, lv, iv = S.dig(g, field), S.dig(l, field), S.dig(inc, field)
            v, _k = S.cmp_scalar(field, lv, gv)
            if v in ("correct", "both_null"):
                continue
            gp, lp = printed(path, gv), printed(path, lv)
            if lp and not gp:
                verdict = "GT_WRONG"
            elif gp and not lp:
                verdict = "LUNA_WRONG"
            elif gp and lp:
                verdict = "BOTH_PRINTED_AMBIGUOUS"
            else:
                verdict = "NEITHER_PRINTED_AMBIGUOUS"
            tally[verdict] += 1
            per_field[field][verdict] += 1
            items.append({
                "statement_id": sid, "field": field,
                "gt": gv, "luna": lv, "incumbent": iv,
                "verdict": verdict,
                "gt_printed_as": gp, "luna_printed_as": lp,
                "csv_agrees_with": ("luna" if iv is not None and lv is not None
                                    and S.cmp_scalar(field, lv, iv)[0] == "correct"
                                    else "gt" if iv is not None and gv is not None
                                    and S.cmp_scalar(field, gv, iv)[0] == "correct"
                                    else "neither"),
            })

    # ---------- transaction-level: GT rows that carry NO date
    #
    # SBI prints tax/markup lines (IGST DB @ 18.00%, GST, FORGN CURR MARKUP) as
    # CONTINUATION rows of the transaction above them: the date column is simply not
    # repeated. Luna inherits the parent row's date; the GT leaves it null. The
    # scorer then books each one as Luna "hallucinating" a date that is in fact
    # printed on the parent row -- so this class is charged to the challenger for
    # doing the more useful thing. Counted separately here.
    txn_null = {"total": 0, "by_class": Counter(), "verdicts": Counter(), "examples": []}
    for sid in ids:
        g, l = gtp[sid], lup[sid]
        path = corpus[sid]
        gts = g.get("transactions") or []
        lts = l.get("transactions") or []
        for t in gts:
            if t.get("date"):
                continue
            txn_null["total"] += 1
            d = (t.get("description") or "").upper()
            cls = ("IGST" if "IGST" in d else "GST" if "GST" in d
                   else "FORGN_CURR_MARKUP" if "MARKUP" in d else "OTHER")
            txn_null["by_class"][cls] += 1
            # Luna's counterpart row, matched on description prefix
            key = (t.get("description") or "")[:20]
            cand = [x for x in lts if (x.get("description") or "")[:20] == key]
            lv = cand[0].get("date") if cand else None
            lp = printed(path, lv) if lv else None
            v = ("GT_WRONG" if lp else "AMBIGUOUS_IN_PDF" if lv
                 else "BOTH_NULL_AGREE")
            txn_null["verdicts"][v] += 1
            if len(txn_null["examples"]) < 12:
                txn_null["examples"].append({
                    "statement_id": sid, "desc": (t.get("description") or "")[:60],
                    "amount": t.get("amount"), "gt_date": None, "luna_date": lv,
                    "luna_date_printed_as": lp, "verdict": v, "class": cls})

    out = {"gt_arm": a.gt, "luna_arm": a.luna, "n_statements": len(ids),
           "tally": dict(tally.most_common()),
           "per_field": {k: dict(v) for k, v in per_field.items()},
           "txn_rows_gt_null_date": {
               "total": txn_null["total"],
               "by_class": dict(txn_null["by_class"].most_common()),
               "verdicts": dict(txn_null["verdicts"].most_common()),
               "examples": txn_null["examples"]},
           "items": items}
    json.dump(out, open(a.out, "w"), ensure_ascii=False, indent=1, default=str)

    print(f"audited GT on {len(ids)} statements, {len(items)} GT-vs-Luna disagreements")
    for k, v in tally.most_common():
        print(f"  {v:>5}  {k}")
    print("\nper field:")
    for f, c in sorted(per_field.items()):
        print(f"  {f:<46} " + "  ".join(f"{k}={v}" for k, v in c.most_common()))
    tn = out["txn_rows_gt_null_date"]
    print(f"\nGT txn rows with NULL date: {tn['total']}")
    print(f"  by class:  {tn['by_class']}")
    print(f"  verdicts:  {tn['verdicts']}")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
