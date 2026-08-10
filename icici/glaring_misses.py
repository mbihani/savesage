#!/usr/bin/env python3
"""Extract EVERY substantive error, for BOTH sides, with statement id / field / both
values / PDF evidence.

"Substantive" is defined narrowly and applied identically to both sides, so the two
lists are comparable:
  * a genuine value difference on one of the 16 priority fields, or a missing/extra card,
    or a missing/extra transaction row;
  * NOT a pure format difference (ISO vs DD/MM/YYYY, 490 vs 490.0, mask leak in
    lastFourDigit), which the scorer already classifies as `correct`+FORMAT;
  * NOT `both_null`;
  * `utilisationPercent@derived` is arithmetic, not extraction -> excluded;
  * `utilisationPercent@extracted` is not printed in ANY ICICI PDF -> excluded from the
    error lists and reported separately, so declining to fabricate is not scored as a miss.

Errors are measured against the Opus-5 GT, then cross-checked against the PDF-adjudicated
verdicts so a "Luna error vs GT" that adjudication showed to be GT/CSV-wrong is labelled.
"""
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import icici_lib as L
import score_lib as S

HERE = L.HERE
SUBSTANTIVE = ("wrong_value", "null_when_populated", "hallucinated_when_null")
EXCLUDE = ("statementLevelSummary.utilisationPercent@extracted",
           "statementLevelSummary.utilisationPercent@derived",
           # network CANNOT be scored against the Opus GT on this corpus: the GT emits a
           # non-null network 3 times and PDF adjudication shows ALL THREE are unsupported
           # outside the four-network fuel-surcharge disclaimer -- i.e. the reference itself
           # hallucinates this field. Scoring against it would charge Luna's correct null as
           # a miss. network is therefore adjudicated against the PDF and reported separately.
           "cards[].cardMeta.network")


def collect(comp, side):
    """-> list of substantive error rows from one comparison block."""
    out = []
    for st in comp.get("per_statement", []):
        sid = st["statement_id"]
        for f, rows in (st.get("fields") or {}).items():
            if f in EXCLUDE:
                continue
            base = f.replace("@extracted", "").replace("@derived", "")
            if base not in S.PRIORITY:
                continue
            for r in rows:
                if r["verdict"] not in SUBSTANTIVE:
                    continue
                out.append({"statement_id": sid, "field": f, "verdict": r["verdict"],
                            side: r["pred"], "gt": r["ref"], "sim": r.get("sim")})
        t = st.get("txn") or {}
        if t and (t.get("unmatched_pred") or t.get("unmatched_ref")):
            out.append({"statement_id": sid, "field": "transactions[] (row set)",
                        "verdict": "row_count_mismatch",
                        side: f"{t['n_pred']} rows ({t['unmatched_pred']} unmatched)",
                        "gt": f"{t['n_ref']} rows ({t['unmatched_ref']} unmatched)"})
        c = st.get("cards") or {}
        if c and not c.get("count_match"):
            out.append({"statement_id": sid, "field": "cards[] (count)",
                        "verdict": "card_count_mismatch",
                        side: c["n_pred"], "gt": c["n_ref"]})
    return out


def main():
    D = json.load(open(os.path.join(HERE, "scores_phase3.json")))
    adj = json.load(open(os.path.join(HERE, "adjudication.json")))
    adj_idx = {}
    for x in adj["items"]:
        adj_idx[(x["statement_id"], x["field"])] = x

    luna = collect(D["comparisons"]["luna_refined_vs_GT__all"], "luna")
    csvi = collect(D["comparisons"]["CSV_vs_GT__all"], "csv")

    for r in luna:
        a = adj_idx.get((r["statement_id"], r["field"]))
        if a:
            r["pdf_adjudication"] = a["adjudication"]
            r["pdf_reason"] = a["reason"]
            r["pdf_evidence"] = a.get("pdf_evidence")

    out = {
        "definition": __doc__.strip(),
        "luna_refined_errors_vs_GT": {
            "total": len(luna),
            "by_field": dict(Counter(r["field"] for r in luna).most_common()),
            "by_verdict": dict(Counter(r["verdict"] for r in luna).most_common()),
            "statements_with_any": len({r["statement_id"] for r in luna}),
            "items": luna,
        },
        "incumbent_csv_errors_vs_GT": {
            "total": len(csvi),
            "by_field": dict(Counter(r["field"] for r in csvi).most_common()),
            "by_verdict": dict(Counter(r["verdict"] for r in csvi).most_common()),
            "statements_with_any": len({r["statement_id"] for r in csvi}),
            "items": csvi,
        },
    }
    dest = os.path.join(HERE, "glaring_misses.json")
    json.dump(out, open(dest, "w"), indent=1, default=str)
    print(f"wrote {dest}")
    for k in ("luna_refined_errors_vs_GT", "incumbent_csv_errors_vs_GT"):
        b = out[k]
        print(f"\n=== {k}: {b['total']} substantive errors on "
              f"{b['statements_with_any']} statements ===")
        for f, n in b["by_field"].items():
            print(f"  {n:>5}  {f}")


if __name__ == "__main__":
    main()
