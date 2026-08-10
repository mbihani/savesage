#!/usr/bin/env python3
"""Enumerate every SUBSTANTIVE error on both sides, with PDF evidence.

"Substantive" excludes the artifacts that are not extraction defects and would
otherwise bury the real errors:
  * pure FORMAT / LENIENT / MASK_DEPTH passes (already scored correct)
  * utilisationPercent as-extracted (structurally asymmetric -- see score.py)
  * dueDate == "NO PAYMENT REQUIRED" vs a GT null (a GT-prompt gap, verified in the
    PDF: SBI really prints that string; Luna and the incumbent are both RIGHT)
  * rewards.programType vs a GT value that is a section HEADER ("SHOP & SMILE
    SUMMARY", "REWARD SUMMARY") rather than a programme name -- verified GT defect

Each surviving error is emitted with statement id, field, BOTH values, and the PDF
evidence that settles it. Writes glaring_misses.json + a markdown section.
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sbi_lib as L          # noqa: E402
import sbi_pdf_evidence as E  # noqa: E402
import score_lib_sbi as S    # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))

GT_ARTIFACT_HEADERS = {"shop & smile", "shop & smile summary", "reward summary",
                       "rewards summary", "cashback summary"}


def is_gt_artifact(c):
    """True if the disagreement is a KNOWN GT-instrument defect rather than a
    challenger/incumbent error."""
    f, pred, ref = c["field"], c["pred"], c["refv"]
    if f == "statementMeta.dueDate" and ref is None and isinstance(pred, str) \
            and not any(ch.isdigit() for ch in pred):
        return "GT_HAS_NO_NONDATE_DUEDATE_RULE"
    if f == "rewards.programType" and isinstance(ref, str) \
            and S.text(ref) in GT_ARTIFACT_HEADERS:
        return "GT_READ_SECTION_HEADER_AS_PROGRAMTYPE"
    if f == "transactions[].date" and ref is None and pred is not None:
        return "GT_LEFT_CONTINUATION_ROW_DATE_NULL"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scores", default=os.path.join(ROOT, "scores_refined.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "glaring_misses.json"))
    ap.add_argument("--md", default=os.path.join(ROOT, "GLARING_MISSES.md"))
    ap.add_argument("--evidence-limit", type=int, default=60,
                    help="how many errors to enrich with PDF evidence (fitz is slow)")
    a = ap.parse_args()

    sc = json.load(open(a.scores))
    corpus = {s: p for s, f, p in L.discover_pdfs()}
    csvref, _ = S.load_csv_incumbent()

    BAD = ("wrong_value", "null_when_populated", "hallucinated_when_null")
    out = {"luna_errors": [], "incumbent_errors": [], "gt_artifacts": Counter(),
           "excluded_asymmetric": 0}

    for side, ref, bucket in (("luna", "luna_vs_gt", "luna_errors"),
                              ("incumbent", "csv_vs_gt", "incumbent_errors")):
        for c in sc["cells"]:
            if c["ref"] != ref or c["verdict"] not in BAD:
                continue
            if c["field"] == "statementLevelSummary.utilisationPercent" \
                    and c.get("ctx") == "as_extracted":
                out["excluded_asymmetric"] += 1
                continue
            art = is_gt_artifact(c)
            if art:
                out["gt_artifacts"][f"{side}:{art}"] += 1
                continue
            out[bucket].append({
                "statement_id": c["statement_id"], "field": c["field"],
                "verdict": c["verdict"], side: c["pred"], "opus_gt": c["refv"],
                "priority_field": c["field"] in S.PRIORITY,
                "row": (c.get("ctx") or {}) if isinstance(c.get("ctx"), dict) else None,
            })

    # ---- enrich the PRIORITY-field errors with PDF evidence
    for bucket in ("luna_errors", "incumbent_errors"):
        prio = [x for x in out[bucket] if x["priority_field"]]
        for x in prio[:a.evidence_limit]:
            sid = x["statement_id"]
            path = corpus.get(sid)
            if not path:
                continue
            f = x["field"]
            try:
                if f.startswith("statementLevelSummary."):
                    key = f.split(".", 1)[1]
                    ev = E.summary_evidence(path)
                    cands = ev.get(key) or []
                    x["pdf_evidence"] = ({"printed_value": cands[0]["value"],
                                          "page": cands[0]["page"],
                                          "value_rect": cands[0]["value_rect"],
                                          "label_rect": cands[0]["label_rect"]}
                                         if cands else {"note": "label not bound"})
                elif f == "cards[].cardMeta.network":
                    v = x.get("luna") if bucket == "luna_errors" else x.get("incumbent")
                    hits = E.find_value_on_page(path, v) if v else []
                    real = [h for h in hits if h["page"] == 1 and h["rect"][1] <= 420.0
                            and "Credit Card Pay" not in h["line"]
                            and "minimum of" not in h["line"]]
                    x["pdf_evidence"] = {"literal_hits": len(hits),
                                         "non_boilerplate_hits": real[:2],
                                         "verdict": ("HALLUCINATION - only boilerplate"
                                                     if v and not real else "printed")}
                elif f.startswith("transactions[]") and x.get("row"):
                    rd = x["row"].get("row_desc")
                    geom = E.txn_rows(path)
                    cand = [g for g in geom if S.desc_sim(rd, g["description_geom"]) > 0.6]
                    x["pdf_evidence"] = [{"page": g["page"], "date": g["date"],
                                          "amount_printed": g["amount_printed"],
                                          "marker": g["marker"],
                                          "desc": g["description_geom"][:70]}
                                         for g in cand[:2]]
                elif f in ("statementMeta.statementDate", "statementMeta.dueDate"):
                    lab = ("Statement Date" if "statementDate" in f
                           else "Payment Due Date")
                    x["pdf_evidence"] = {"label_hits": E.find_value_on_page(path, lab)[:1]}
            except Exception as e:  # evidence is best-effort; never mask the error
                x["pdf_evidence"] = {"error": f"{type(e).__name__}: {e}"}

    out["gt_artifacts"] = dict(out["gt_artifacts"])
    out["counts"] = {
        "luna_total": len(out["luna_errors"]),
        "luna_priority": sum(1 for x in out["luna_errors"] if x["priority_field"]),
        "incumbent_total": len(out["incumbent_errors"]),
        "incumbent_priority": sum(1 for x in out["incumbent_errors"] if x["priority_field"]),
    }
    out["luna_by_field"] = dict(Counter(x["field"] for x in out["luna_errors"]).most_common())
    out["incumbent_by_field"] = dict(
        Counter(x["field"] for x in out["incumbent_errors"]).most_common())
    out["luna_statements_affected"] = len({x["statement_id"] for x in out["luna_errors"]
                                           if x["priority_field"]})
    out["incumbent_statements_affected"] = len({x["statement_id"] for x in
                                                out["incumbent_errors"] if x["priority_field"]})
    json.dump(out, open(a.out, "w"), ensure_ascii=False, indent=1, default=str)

    M = ["## The glaring misses\n",
         f"Substantive errors vs the Opus-5 GT, excluding "
         f"{out['excluded_asymmetric']} structurally-asymmetric "
         f"`utilisationPercent` cells and the GT-instrument artifacts listed below.\n",
         f"- **Luna (refined): {out['counts']['luna_total']} substantive errors "
         f"({out['counts']['luna_priority']} in the 16 priority fields), affecting "
         f"{out['luna_statements_affected']} statements**",
         f"- **Incumbent CSV: {out['counts']['incumbent_total']} substantive errors "
         f"({out['counts']['incumbent_priority']} in the 16 priority fields), affecting "
         f"{out['incumbent_statements_affected']} statements**\n",
         "GT-instrument artifacts excluded (verified GT defects, NOT challenger errors):\n",
         "| artifact | count |", "|---|---:|"]
    for k, v in out["gt_artifacts"].items():
        M.append(f"| {k} | {v} |")

    for bucket, title in (("luna_errors", "Luna (refined) — every substantive error"),
                          ("incumbent_errors", "Incumbent CSV — every substantive error")):
        by = defaultdict(list)
        for x in out[bucket]:
            by[x["field"]].append(x)
        M.append(f"\n### {title}\n")
        if not out[bucket]:
            M.append("_none_\n")
            continue
        M.append("| field | n | statements |")
        M.append("|---|---:|---|")
        for f, xs in sorted(by.items(), key=lambda kv: -len(kv[1])):
            sids = sorted({x["statement_id"] for x in xs})
            M.append(f"| `{f}` | {len(xs)} | {', '.join(sids[:14])}"
                     f"{' …' if len(sids) > 14 else ''} |")
        M.append("\n**Priority-field errors, itemised with PDF evidence:**\n")
        side = "luna" if bucket == "luna_errors" else "incumbent"
        for x in [y for y in out[bucket] if y["priority_field"]][:80]:
            ev = x.get("pdf_evidence")
            M.append(f"- **{x['statement_id']}** `{x['field']}` — {side}="
                     f"`{x.get(side)!r}` vs GT=`{x['opus_gt']!r}` ({x['verdict']})"
                     + (f"  \n  PDF: `{json.dumps(ev, default=str)[:230]}`" if ev else ""))
    open(a.md, "w").write("\n".join(M))
    print("\n".join(M[:60]))
    print(f"\ncounts: {out['counts']}")
    print("wrote", a.out, "and", a.md)


if __name__ == "__main__":
    main()
