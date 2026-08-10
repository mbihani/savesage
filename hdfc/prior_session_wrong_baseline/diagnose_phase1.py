#!/usr/bin/env python3
"""PHASE 2 diagnosis: compare the 10 generic-prompt Luna results against the CSV
incumbent, and against the PDF text itself, to find HDFC-SPECIFIC failure patterns.

The CSV is the incumbent parser, not truth, so a disagreement here is a POINTER to
inspect, not a verdict. Every reported pattern is checked against the PDF text
(PyMuPDF) before it is allowed to become a prompt rule.
"""
import json
import os
import re
import sys
from collections import Counter

import fitz

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hdfc_lib as H
import score_lib as S

HERE = os.path.dirname(os.path.abspath(__file__))


def pdf_text(path):
    d = fitz.open(path)
    t = "\n".join(p.get_text() for p in d)
    d.close()
    return t


def main():
    runs = S.load_run(os.path.join(HERE, "phase1_generic"))
    matched, _, _ = H.build_join()
    by_sid = {m["sid"]: m for m in matched}
    prof = json.load(open(os.path.join(HERE, "corpus_profile.json")))
    sample = {p["sid"]: p for p in prof["sample"]}

    report = {"per_statement": [], "patterns": Counter()}
    print(f"phase1 records: {len(runs)}  sample size: {len(sample)}\n")

    for sid in sorted(sample):
        rec = runs.get(sid)
        if rec is None:
            print(f"{sid[:50]}  NO RECORD YET")
            continue
        luna = rec.get("parsed_json") or {}
        m = by_sid[sid]
        csv = S.csv_extraction(m["csv_row"])
        txt = pdf_text(m["path"])
        low = txt.lower()

        entry = {"sid": sid, "layout": sample[sid]["layout"],
                 "outcome": rec.get("outcome"), "pdf_date_rows": sample[sid]["date_rows"],
                 "luna_txn": len(luna.get("transactions") or []),
                 "csv_txn": len(csv.get("transactions") or []),
                 "fields": {}, "notes": []}

        # ---- statement-level field disagreements (Luna vs CSV) -------------
        for name, scope, path, kind in S.STMT_FIELDS:
            lv = S.get_field(luna, scope, path)
            cv = S.get_field(csv, scope, path)
            if not S.values_equal(kind, lv, cv):
                entry["fields"][name] = {"luna": lv, "csv": cv}
                report["patterns"][f"disagree:{name}"] += 1

        # ---- transaction matching (description-only) ----------------------
        pairs, up, ug = S.match_transactions(luna.get("transactions"),
                                             csv.get("transactions"))
        entry["matched"] = len(pairs)
        entry["luna_only"] = len(up)
        entry["csv_only"] = len(ug)

        lt = luna.get("transactions") or []
        ct = csv.get("transactions") or []

        # cardholder-name / non-transaction rows admitted by Luna?
        NAME_ROWish = re.compile(r"^[A-Z][A-Z\s\.]{4,40}$")
        luna_only_descs = [(lt[i].get("description") or "") for i in up]
        namey = [d for d in luna_only_descs if NAME_ROWish.match(d.strip())]
        if namey:
            entry["notes"].append({"pattern": "luna_extra_rows_look_like_names",
                                   "examples": namey[:6]})
            report["patterns"]["luna_extra_name_rows"] += 1
        if luna_only_descs:
            entry["luna_only_descs"] = luna_only_descs[:12]
        if ug:
            entry["csv_only_descs"] = [(ct[j].get("description") or "") for j in ug][:12]

        # per-field errors within matched pairs
        for f in S.TXN_FIELDS:
            bad = [(lt[i].get(f), ct[j].get(f)) for i, j, _ in pairs
                   if not S.txn_field_equal(f, lt[i].get(f), ct[j].get(f))]
            if bad:
                entry["fields"][f"txn.{f}"] = {"n_disagree": len(bad),
                                               "examples": bad[:5]}
                report["patterns"][f"txn_disagree:{f}"] += 1

        # ---- rewards: HDFC labels these differently per product ----------
        rw = luna.get("rewards") or {}
        labels = [l for l in ("Feature + Bonus Reward Points Earned", "NeuCoins Earned",
                              "Cash Back Summary", "Disbursed", "Reward Points Summary",
                              "Points Earned", "CashBack Earned", "Opening Balance",
                              "Closing Balance", "Points Redeemed", "Adjusted")
                  if l.lower() in low]
        entry["pdf_reward_labels_present"] = labels
        entry["luna_rewards"] = rw
        entry["csv_rewards"] = csv.get("rewards")
        if labels and all(v is None for k, v in rw.items() if k != "programType"):
            entry["notes"].append({"pattern": "reward_labels_in_pdf_but_luna_all_null",
                                   "labels": labels})
            report["patterns"]["rewards_all_null_despite_labels"] += 1

        # ---- issuer bias check: GT prompt hardcodes "Axis Bank" ----------
        iss = S.norm_str(S.get_field(luna, "root", "statementMeta.issuerName"))
        if iss and "axis" in iss.lower():
            entry["notes"].append({"pattern": "issuer_contaminated_by_axis_prompt_rule",
                                   "value": iss})
            report["patterns"]["issuer_says_axis"] += 1

        # ---- row-count sanity vs the PDF's own date-row count ------------
        pdfrows = sample[sid]["date_rows"]
        if pdfrows and entry["luna_txn"] and abs(entry["luna_txn"] - pdfrows) / pdfrows > 0.35:
            entry["notes"].append({"pattern": "luna_txn_count_far_from_pdf_date_rows",
                                   "luna": entry["luna_txn"], "pdf_date_rows": pdfrows})

        report["per_statement"].append(entry)
        print(f"{sample[sid]['layout']:11s} {entry['outcome']:6s} "
              f"luna_txn={entry['luna_txn']:4d} csv_txn={entry['csv_txn']:4d} "
              f"pdfrows={pdfrows:4d} matched={len(pairs):4d} L_only={len(up):3d} "
              f"C_only={len(ug):3d} fielddis={len(entry['fields'])}  {sid[:44]}")

    print("\nPATTERN TALLY")
    for k, v in report["patterns"].most_common():
        print(f"  {v:3d}  {k}")

    H.G.atomic_write_json(os.path.join(HERE, "phase2_diagnosis.json"),
                          {"patterns": dict(report["patterns"]),
                           "per_statement": report["per_statement"]})


if __name__ == "__main__":
    main()
