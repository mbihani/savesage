"""STEP 2 evidence: independently verify the ITFRupee 'C' = rupee-sign claim.

Tests four separate assertions and reports each as MEASURED, not assumed:
  1. How many of the 15 PDFs embed a font named ITFRupee.
  2. Whether EVERY transaction row in those files carries an ITFRupee 'C' before its
     amount -- i.e. whether "C" is universal rather than a credit marker.
  3. The TRUE credit/debit split from the '+'-and-green markers, and whether those two
     independent signals agree. If 'C' meant CREDIT, split would be 100% CREDIT.
  4. Whether the headline TOTAL AMOUNT DUE also carries the 'C' -- a figure that can
     never be a credit, which by itself falsifies "C means credit".

Also reports what a naive `'C' => CREDIT` rule would do per file, which is the
quantified cost of the clause the brief bans from the HDFC prompt.
"""

import json
import os
import re

import fitz

import pdf_rows as P

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "itfrupee_verification.json")
TAD = re.compile(r"TOTAL\s+AMOUNT\s+DUE", re.I)


def tad_marker(path):
    """Is the TOTAL AMOUNT DUE headline figure rendered with an ITFRupee 'C'?

    Located geometrically: find the label, then look for an ITFRupee span whose
    y is within one text-block of it. Returns (found_label, itf_C_nearby, sample).
    """
    doc = fitz.open(path)
    found, near, sample = False, False, None
    for pno in range(len(doc)):
        page = doc[pno]
        text = page.get_text()
        if not TAD.search(re.sub(r"\s+", " ", text)):
            continue
        found = True
        label_ys = []
        for blk in page.get_text("dict").get("blocks", []):
            for ln in blk.get("lines", []):
                joined = re.sub(r"\s+", " ", "".join(s["text"] for s in ln.get("spans", [])))
                if TAD.search(joined):
                    label_ys.append(ln["bbox"][1])
        for blk in page.get_text("dict").get("blocks", []):
            for ln in blk.get("lines", []):
                spans = ln.get("spans", [])
                for i, sp in enumerate(spans):
                    if "ITFRupee" not in sp["font"] or sp["text"].strip() != "C":
                        continue
                    if any(abs(sp["bbox"][1] - ly) < 60 for ly in label_ys):
                        near = True
                        nxt = spans[i + 1]["text"].strip() if i + 1 < len(spans) else ""
                        if sample is None and nxt:
                            sample = "C" + nxt
        break
    doc.close()
    return found, near, sample


def main():
    per_file, tot = [], {
        "files": 0, "files_with_itfrupee": 0, "rows": 0,
        "rows_with_itf_C": 0, "credit": 0, "debit": 0,
        "signal_disagreements": 0, "naive_C_would_call_credit": 0,
    }
    for sid, fn, path in P.corpus():
        ex = P.extract(path)
        rows = ex["rows"]
        itf_c = sum(1 for r in rows if r["currency_marker"] == "ITFRupee_C")
        cr = sum(1 for r in rows if r["direction"] == "CREDIT")
        dis = sum(1 for r in rows if not r["signals_agree"])
        label, near, sample = tad_marker(path)

        # A naive "'C' => CREDIT" reader flips every ITFRupee row to CREDIT.
        naive_cr = itf_c
        wrong = sum(1 for r in rows
                    if r["currency_marker"] == "ITFRupee_C" and r["direction"] == "DEBIT")
        per_file.append({
            "statement_id": P.statement_id(fn), "file": fn, "layout": ex["layout"],
            "itfrupee_spans": ex["itfrupee_spans"], "rows": len(rows),
            "rows_with_itf_C": itf_c, "credit": cr, "debit": len(rows) - cr,
            "signal_disagreements": dis,
            "tad_label_found": label, "tad_has_itf_C": near, "tad_sample": sample,
            "naive_C_rule_would_call_CREDIT": naive_cr,
            "naive_C_rule_wrong_rows": wrong,
        })
        tot["files"] += 1
        tot["files_with_itfrupee"] += 1 if ex["itfrupee_spans"] else 0
        tot["rows"] += len(rows)
        tot["rows_with_itf_C"] += itf_c
        tot["credit"] += cr
        tot["debit"] += len(rows) - cr
        tot["signal_disagreements"] += dis
        tot["naive_C_would_call_credit"] += naive_cr

    tot["naive_C_rule_wrong_rows"] = sum(p["naive_C_rule_wrong_rows"] for p in per_file)
    res = {"totals": tot, "per_file": per_file}
    with open(OUT, "w") as fh:
        json.dump(res, fh, indent=2)

    print(f"files                        : {tot['files']}")
    print(f"files embedding ITFRupee     : {tot['files_with_itfrupee']}")
    print(f"transaction rows (geometric) : {tot['rows']}")
    print(f"rows whose amount carries 'C': {tot['rows_with_itf_C']}")
    print(f"TRUE split  CREDIT / DEBIT   : {tot['credit']} / {tot['debit']}")
    print(f"'+' vs green disagreements   : {tot['signal_disagreements']}")
    print(f"naive C=>CREDIT would flag   : {tot['naive_C_would_call_credit']} rows CREDIT")
    print(f"  ... of which WRONG         : {tot['naive_C_rule_wrong_rows']}")
    print()
    print(f"{'stmt_id':<12}{'lay':<4}{'rows':>5}{'C':>5}{'CR':>4}{'DR':>5}"
          f"{'TAD_C':>7}  naive_wrong")
    for p in per_file:
        print(f"{p['statement_id'] or '-':<12}{p['layout']:<4}{p['rows']:>5}"
              f"{p['rows_with_itf_C']:>5}{p['credit']:>4}{p['debit']:>5}"
              f"{str(p['tad_has_itf_C']):>7}  {p['naive_C_rule_wrong_rows']}"
              f"   {p['tad_sample'] or ''}")


if __name__ == "__main__":
    main()
