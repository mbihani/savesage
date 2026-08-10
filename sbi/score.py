#!/usr/bin/env python3
"""Score an SBI Luna arm against BOTH references, plus CSV-vs-GT, and emit JSON.

KNOWN LIMITS OF THE GT INSTRUMENT (verified against the PDFs; they cap the ceiling
of `luna_vs_gt` and must be read off the report, not silently absorbed):

  * dueDate = "NO PAYMENT REQUIRED". SBI prints this literal string in the Payment
    Due Date cell on credit-balance statements. The shared GT prompt has NO rule for
    a non-date due date (the client prompt does), so Opus returns null while BOTH
    Luna and the incumbent return the printed text. Those cells score
    `hallucinated_when_null` against the GT even though Luna is RIGHT and the GT is
    wrong. Same count for luna_vs_gt and csv_vs_gt, so it does not bias the
    comparison BETWEEN them -- but it does depress both, and it is why
    `luna_vs_csv` dueDate agreement is higher than either accuracy figure.
  * transactions[].date = null on continuation rows. SBI omits the date on a row
    that continues the previous date (e.g. "IGST DB @ 18.00%" directly under
    "INTEREST ON EMI"). Opus reports null (literal); Luna carries the date down.
    Neither is a misread of a printed glyph.


Three scorings, never conflated:
  luna_vs_gt  -> accuracy against the Opus-5 native-PDF pass  (the accuracy figure)
  csv_vs_gt   -> the incumbent's OWN accuracy against the same reference
  luna_vs_csv -> AGREEMENT with the incumbent Gemini parser (NOT correctness)

Every table is emitted twice: over ALL scoreable statements, and over the
HELD-OUT set (all statements minus the 10 Phase-1 tuning statements), so an
overfit refinement is visible rather than assumed away.
"""
import argparse
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import score_lib_sbi as S

ROOT = os.path.dirname(os.path.abspath(__file__))


def score_statement(sid, pred, ref, refname):
    cells = []

    def add(field, verdict, kind, a, g, ctx=None):
        cells.append({"statement_id": sid, "ref": refname, "field": field,
                      "verdict": verdict, "kind": kind, "pred": a, "refv": g, "ctx": ctx})

    for field in S.PRIORITY + S.SECONDARY:
        if field.startswith("cards[]") or field.startswith("transactions[]"):
            continue
        if field == "statementLevelSummary.utilisationPercent":
            continue  # handled below, BOTH as-extracted and as-derived
        v, k = S.cmp_scalar(field, S.dig(pred, field), S.dig(ref, field))
        add(field, v, k, S.dig(pred, field), S.dig(ref, field))

    # utilisationPercent is STRUCTURALLY ASYMMETRIC and its as-extracted numbers must
    # not be read as a capability difference. MEASURED: 0 of 300 SBI PDFs print the
    # string 'utilis'/'utiliz' anywhere, so nobody can extract it. The shared
    # response_format schema sets additionalProperties:false and does NOT list the key,
    # so Luna and the GT are FORBIDDEN from emitting it (0 records do). The incumbent
    # ran under no such constraint and emits it on 180/315 rows -- 151 of those 180
    # equal its own derived totalAmountDue/totalCreditLimit*100, i.e. it is computing,
    # not reading. As-extracted therefore scores the incumbent as
    # `hallucinated_when_null` ~180x purely because of the schema it was NOT bound by.
    # The as_derived variant (same formula applied to all three sources) is the only
    # comparable one.
    for tag, av, gv in (
        ("as_extracted", S.dig(pred, "statementLevelSummary.utilisationPercent"),
         S.dig(ref, "statementLevelSummary.utilisationPercent")),
        ("as_derived", S.util_derive(pred), S.util_derive(ref)),
    ):
        v, k = S.cmp_scalar("statementLevelSummary.utilisationPercent", av, gv)
        add("statementLevelSummary.utilisationPercent", v, k, av, gv, ctx=tag)

    pairs, npred, nref = S.align_cards(pred.get("cards"), ref.get("cards"))
    for p, r in pairs:
        for field in [f for f in S.PRIORITY + S.SECONDARY if f.startswith("cards[]")]:
            leaf = field.split("[].", 1)[1]
            a, g = S.dig(p or {}, leaf), S.dig(r or {}, leaf)
            if p is None:
                add(field, "null_when_populated" if g is not None else "both_null",
                    "CARD_COUNT_SHORT", None, g)
                continue
            if r is None:
                add(field, "hallucinated_when_null" if a is not None else "both_null",
                    "CARD_COUNT_EXTRA", a, None)
                continue
            v, k = S.cmp_scalar(field, a, g)
            add(field, v, k, a, g)

    ptx = pred.get("transactions") if isinstance(pred.get("transactions"), list) else []
    rtx = ref.get("transactions") if isinstance(ref.get("transactions"), list) else []
    mpairs, up, ur = S.match_txns_by_description(ptx, rtx)
    for m in mpairs:
        # Row locators travel with every txn cell so the adjudicator can find the
        # physical glyph row in the PDF and read its own C/D marker.
        row_ctx = {"sim": round(m["sim"], 4), "amount": m["ref"].get("amount"),
                   "pred_amount": m["pred"].get("amount"),
                   "row_desc": m["ref"].get("description"), "row_date": m["ref"].get("date")}
        for leaf in S.TXN_FIELDS:
            field = "transactions[]." + leaf
            a, g = m["pred"].get(leaf), m["ref"].get(leaf)
            if leaf == "direction":
                v = "correct" if S.direction(m["pred"]) == S.direction(m["ref"]) else "wrong_value"
                add(field, v, None, a, g, ctx=row_ctx)
                continue
            v, k = S.cmp_scalar(field, a, g)
            add(field, v, k, a, g, ctx=row_ctx)

    exact = sum(1 for m in mpairs if str(m["pred"].get("description") or "")
                == str(m["ref"].get("description") or ""))
    ws = sum(1 for m in mpairs if S.text(m["pred"].get("description"))
             == S.text(m["ref"].get("description")))
    txn = {"statement_id": sid, "ref": refname, "n_pred": len(ptx), "n_ref": len(rtx),
           "matched": len(mpairs), "unmatched_pred": len(up), "unmatched_ref": len(ur),
           "desc_exact": exact, "desc_exact_ws_insensitive": ws,
           "sim_sum": sum(m["sim"] for m in mpairs),
           "n_cards_pred": npred, "n_cards_ref": nref,
           "unmatched_ref_rows": [{"date": r.get("date"), "amount": r.get("amount"),
                                   "description": r.get("description"),
                                   "direction": r.get("direction")} for r in ur][:200],
           "unmatched_pred_rows": [{"date": r.get("date"), "amount": r.get("amount"),
                                    "description": r.get("description"),
                                    "direction": r.get("direction")} for r in up][:200]}
    return cells, txn


def aggregate(cells, fields, util_ctx="as_extracted"):
    """`both_null` is EXCLUDED from the denominator: a field null on both sides is
    not evidence either way and would inflate any score. utilisationPercent exists
    twice per statement so it must be filtered to one variant or the two pool."""
    out = {}
    for f in fields:
        xs = [c for c in cells if c["field"] == f]
        if f == "statementLevelSummary.utilisationPercent" and util_ctx is not None:
            xs = [c for c in xs if c.get("ctx") == util_ctx]
        cnt = Counter(c["verdict"] for c in xs)
        n = sum(v for k, v in cnt.items() if k != "both_null")
        out[f] = {"n_compared": n, "correct": cnt.get("correct", 0),
                  "wrong_value": cnt.get("wrong_value", 0),
                  "null_when_populated": cnt.get("null_when_populated", 0),
                  "hallucinated_when_null": cnt.get("hallucinated_when_null", 0),
                  "both_null": cnt.get("both_null", 0),
                  "pct": (cnt.get("correct", 0) / n * 100) if n else None,
                  "format_only": sum(1 for c in xs if c["kind"] == "FORMAT"),
                  "lenient_credit": sum(1 for c in xs if c["kind"] == "LENIENT"),
                  "mask_depth_credit": sum(1 for c in xs if c["kind"] == "MASK_DEPTH")}
    return out


def txn_block(ts):
    tp = sum(t["matched"] for t in ts)
    fp = sum(t["unmatched_pred"] for t in ts)
    fn = sum(t["unmatched_ref"] for t in ts)
    prec = tp / (tp + fp) if tp + fp else None
    rec = tp / (tp + fn) if tp + fn else None
    nref = sum(t["n_ref"] for t in ts)
    return {"n_pred_total": sum(t["n_pred"] for t in ts), "n_ref_total": nref,
            "matched": tp, "unmatched_pred": fp, "unmatched_ref": fn,
            "precision": prec, "recall": rec,
            "f1": (2 * prec * rec / (prec + rec)) if prec and rec else None,
            "match_rate_vs_ref": tp / nref if nref else None,
            "desc_exact": sum(t["desc_exact"] for t in ts),
            "desc_exact_ws_insensitive": sum(t["desc_exact_ws_insensitive"] for t in ts),
            "desc_mean_similarity": (sum(t["sim_sum"] for t in ts) / tp) if tp else None,
            "statements_txn_count_equal": sum(1 for t in ts if t["n_pred"] == t["n_ref"]),
            "cards_pred_total": sum(t["n_cards_pred"] for t in ts),
            "cards_ref_total": sum(t["n_cards_ref"] for t in ts)}


def build(cells, txns, ids):
    ids = set(ids)
    cs = [c for c in cells if c["statement_id"] in ids]
    ts = [t for t in txns if t["statement_id"] in ids]
    return {"n_statements": len(ids),
            "priority": aggregate(cs, S.PRIORITY),
            "secondary": aggregate(cs, S.SECONDARY),
            "utilisation_derived": aggregate(
                [c for c in cs if c.get("ctx") == "as_derived"],
                ["statementLevelSummary.utilisationPercent"], util_ctx=None),
            "txn": txn_block(ts)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--luna", default=os.path.join(ROOT, "run_luna_refined"))
    ap.add_argument("--gt", default=os.path.join(ROOT, "run_gt"))
    ap.add_argument("--out", default=os.path.join(ROOT, "scores.json"))
    ap.add_argument("--tag", default="refined")
    a = ap.parse_args()

    corpus = S.load_corpus()
    ids = [c[0] for c in corpus]
    csvref, csvmeta = S.load_csv_incumbent()
    gtrecs = S.load_arm(a.gt)
    lunarecs = S.load_arm(a.luna)
    tuning = set(json.load(open(os.path.join(ROOT, "phase1_sample.json")))["sample_ids"])

    gtparsed = {k: S.parsed_of(v) for k, v in gtrecs.items()}
    gtok = {k for k, v in gtparsed.items() if v}
    # Scoreable = PDF exists AND a CSV row joins AND the GT produced a usable record
    # AND the Luna arm has a record for it.
    #
    # Requiring a Luna record is what stops a PARTIAL run from being reported as
    # model failure: a statement Luna has not been called on yet is NOT_RUN
    # (harness state), and scoring it as a total miss would understate accuracy in
    # exact proportion to how much of the run is left. Statements Luna genuinely
    # FAILED on (429 / parse fail / truncation) DO have records and DO stay in the
    # denominator scoring as misses -- that is a real defect and must not be dropped.
    # Every exclusion is counted and reported, never silent.
    joined = [s for s in ids if s in csvref]
    scoreable = [s for s in joined if s in gtok and s in lunarecs]
    excl = {
        "pdf_total": len(ids),
        "csv_join": len(joined),
        "pdf_without_csv_row": [s for s in ids if s not in csvref],
        "gt_missing_or_unusable": [s for s in joined if s not in gtok],
        "luna_not_run": [s for s in joined if s in gtok and s not in lunarecs],
    }
    print(f"corpus={len(ids)} csv_join={len(joined)} gt_records={len(gtrecs)} "
          f"gt_parsed={len(gtok)} luna_records={len(lunarecs)} "
          f"scoreable={len(scoreable)}  excluded: "
          f"no_csv={len(excl['pdf_without_csv_row'])} "
          f"gt_bad={len(excl['gt_missing_or_unusable'])} "
          f"luna_not_run={len(excl['luna_not_run'])}")

    health, cells, txns = [], [], []
    for sid in scoreable:
        rec = lunarecs.get(sid)
        if rec is None:
            health.append({"statement_id": sid, "outcome": "NOT_RUN", "failure_class": "harness"})
        else:
            health.append({k: rec.get(k) for k in
                           ("statement_id", "outcome", "failure_class", "finish_reason",
                            "usage_raw", "n_transactions", "n_cards", "meta",
                            "prompt_sha256", "escaped_transactions_recovered")})
        # A failed call scores as a total miss -- never dropped from the denominator.
        luna = S.parsed_of(rec)
        gt = gtparsed[sid]
        for pred, refobj, refname in (
            (luna, gt, "luna_vs_gt"),
            (S.parsed_of({"outcome": "OK", "parsed_json": csvref[sid]}), gt, "csv_vs_gt"),
            (luna, csvref[sid], "luna_vs_csv"),
        ):
            c, t = score_statement(sid, pred, refobj, refname)
            cells += c
            txns.append(t)

    held = [s for s in scoreable if s not in tuning]
    summary = {}
    for refname in ("luna_vs_gt", "csv_vs_gt", "luna_vs_csv"):
        cs = [c for c in cells if c["ref"] == refname]
        ts = [t for t in txns if t["ref"] == refname]
        summary[refname] = {"all": build(cs, ts, scoreable),
                            "held_out": build(cs, ts, held)}

    gthealth = [{k: v.get(k) for k in ("statement_id", "outcome", "failure_class",
                                       "finish_reason", "usage_raw", "n_transactions",
                                       "meta", "escaped_transactions_recovered")}
                for v in gtrecs.values()]
    res = {"tag": a.tag, "scoreable_ids": scoreable, "held_out_ids": held,
           "tuning_ids": sorted(tuning), "exclusions": excl, "summary": summary,
           "luna_health": health, "gt_health": gthealth,
           "cells": cells, "txn_per_statement": txns, "csv_meta": csvmeta}
    with open(a.out, "w") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    for refname in ("luna_vs_gt", "csv_vs_gt", "luna_vs_csv"):
        print(f"\n===== {refname} (all n={len(scoreable)}) =====")
        for f, v in summary[refname]["all"]["priority"].items():
            print(f"  {f:<52} n={v['n_compared']:>5} "
                  f"ok={'  n/a' if v['pct'] is None else format(v['pct'], '6.1f')} "
                  f"wrong={v['wrong_value']:>4} null={v['null_when_populated']:>4} "
                  f"halluc={v['hallucinated_when_null']:>4} bothnull={v['both_null']:>4}")
        t = summary[refname]["all"]["txn"]
        print(f"  TXN P={t['precision']} R={t['recall']} F1={t['f1']} "
              f"pred={t['n_pred_total']} ref={t['n_ref_total']} matched={t['matched']}")
    print("\nluna health:", dict(Counter(h["outcome"] for h in health)))
    print("gt health:", dict(Counter(h["outcome"] for h in gthealth)))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
