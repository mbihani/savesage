#!/usr/bin/env python3
"""Phase 3 scoring: Luna(refined) / Luna(client-baseline) / incumbent CSV vs the Opus-5 GT.

Emits scores_phase3.json with, for every arm x reference pair:
  * per-field n / accuracy / wrong_value / null_when_populated / hallucinated_when_GT_null
  * transaction precision / recall / F1 + description fidelity
  * the SAME numbers recomputed on the held-out set (all statements MINUS the 10 tuned on)
  * outcome tallies and verbatim token accounting

Reference semantics are never conflated:
  vs GT  -> ACCURACY   (Opus-5 native PDF; shares a prompt instrument with neither Luna arm,
                        since both Luna arms use the client prompt lineage, not GT_PROMPT)
  vs CSV -> AGREEMENT  (the incumbent gemini-3-flash parser's own output, NOT truth)
"""
import json
import os
import statistics
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import icici_lib as L
import score_lib as S

HERE = L.HERE
TUNING_IDS = set(json.load(open(os.path.join(HERE, "phase1_sample.json")))["sample_ids"])

ARMS = {
    "luna_refined": os.path.join(HERE, "luna_refined"),
    "luna_client_p1": os.path.join(HERE, "phase1_luna_client"),  # the 10 tuning ones only
    "opus_gt": os.path.join(HERE, "opus_gt"),
}


def usable(rec):
    return S.model_as_extraction(rec) is not None


def token_stats(recs):
    """Verbatim usage -> in/out/reasoning/total + per-statement mean/median/max, AND the
    determination of whether reasoning sits INSIDE or OUTSIDE completion_tokens."""
    rows, inside, outside, unknown = [], 0, 0, 0
    for sid, r in recs.items():
        u = r.get("usage_raw") or {}
        d = u.get("completion_tokens_details") or {}
        pt, ct, tt = u.get("prompt_tokens"), u.get("completion_tokens"), u.get("total_tokens")
        rt = u.get("reasoning_tokens", d.get("reasoning_tokens"))
        rows.append({"sid": sid, "prompt": pt, "completion": ct, "total": tt, "reasoning": rt})
        if pt is None or ct is None or tt is None:
            unknown += 1
        elif pt + ct == tt:
            inside += 1          # OpenAI convention: reasoning is INSIDE completion
        elif rt is not None and pt + ct + rt == tt:
            outside += 1
        else:
            unknown += 1

    def agg(k):
        v = [r[k] for r in rows if isinstance(r[k], int)]
        if not v:
            return None
        return {"n": len(v), "sum": sum(v), "mean": round(statistics.mean(v), 1),
                "median": statistics.median(v), "max": max(v), "min": min(v)}
    return {
        "per_statement": rows,
        "prompt_tokens": agg("prompt"), "completion_tokens": agg("completion"),
        "total_tokens": agg("total"), "reasoning_tokens": agg("reasoning"),
        "reasoning_placement": {
            "prompt+completion==total (reasoning INSIDE completion)": inside,
            "prompt+completion+reasoning==total (reasoning OUTSIDE)": outside,
            "unresolved": unknown,
            "n": len(rows),
        },
    }


def run_pair(pred_recs, ref_getter, ids, label):
    """-> {"agg":..., "per_statement":[...], "txn":...} over `ids`."""
    per = []
    for sid in ids:
        p = S.model_as_extraction(pred_recs.get(sid) or {})
        g = ref_getter(sid)
        if p is None or g is None:
            continue
        per.append(S.score_statement(p, g, sid))
    agg = S.aggregate(per)
    tx = [s["txn"] for s in per if s.get("txn")]
    txn = None
    if tx:
        npred, nref, nm = sum(t["n_pred"] for t in tx), sum(t["n_ref"] for t in tx), sum(t["matched"] for t in tx)
        prec = nm / npred if npred else None
        reca = nm / nref if nref else None
        f1 = (2 * prec * reca / (prec + reca)) if (prec and reca) else None
        sims = [t["mean_desc_sim"] for t in tx if t["mean_desc_sim"] is not None]
        txn = {
            "statements": len(tx), "rows_pred": npred, "rows_ref": nref, "rows_matched": nm,
            "micro_precision": prec, "micro_recall": reca, "micro_f1": f1,
            "macro_f1": round(statistics.mean([t["f1"] for t in tx]), 4),
            "mean_desc_sim": round(statistics.mean(sims), 4) if sims else None,
            "desc_exact_char_for_char": sum(t["desc_exact_char_for_char"] for t in tx),
            "desc_exact_casefold": sum(t["desc_exact_casefold"] for t in tx),
            "row_count_exact_match_statements": sum(1 for t in tx if t["n_pred"] == t["n_ref"]),
        }
    return {"label": label, "n_statements": len(per), "fields": agg, "txn": txn,
            "per_statement": per}


def main():
    corpus = L.discover_pdfs()
    name_by_sid = {sid: f for sid, f, _ in corpus}
    by_csv, join = L.load_csv_incumbent()

    arms = {k: S.load_arm(v) for k, v in ARMS.items()}
    gt = arms["opus_gt"]

    def gt_get(sid):
        return S.model_as_extraction(gt.get(sid) or {})

    def csv_get(sid):
        e = by_csv.get(name_by_sid.get(sid))
        return S.csv_as_extraction(e) if e else None

    # ---- the scoreable intersection, stated explicitly
    have_gt = {sid for sid in gt if gt_get(sid)}
    have_csv = {sid for sid in name_by_sid if csv_get(sid)}
    have_ref = sorted(have_gt & have_csv, key=lambda s: (len(s), s))
    heldout = [s for s in have_ref if s not in TUNING_IDS]

    out = {
        "corpus_pdfs": len(corpus),
        "csv_join": {k: (v if not isinstance(v, list) else len(v)) for k, v in join.items()},
        "csv_unmatched_rows_detail": join["unmatched_csv_rows"],
        "scoreable": {
            "with_opus_gt": len(have_gt), "with_csv": len(have_csv),
            "intersection_gt_and_csv": len(have_ref),
            "held_out_excl_10_tuning": len(heldout),
            "tuning_ids": sorted(TUNING_IDS),
        },
        "outcomes": {k: dict(Counter(r.get("outcome") for r in v.values())) for k, v in arms.items()},
        "failure_classes": {k: dict(Counter(r.get("failure_class") for r in v.values()))
                            for k, v in arms.items()},
        "rate_limited_calls": {k: sum(1 for r in v.values()
                                      if (r.get("meta") or {}).get("rate_limited"))
                               for k, v in arms.items()},
        "tokens": {k: token_stats(v) for k, v in arms.items()},
        "prompt_provenance": {k: sorted({(r.get("prompt_sha256"), r.get("prompt_chars"))
                                         for r in v.values()}) for k, v in arms.items()},
        "comparisons": {},
    }

    # Opus published rate only. Luna price is UNPUBLISHED -> token counts only.
    ug = out["tokens"]["opus_gt"]
    if ug["prompt_tokens"] and ug["completion_tokens"]:
        out["opus_gt_cost_usd_at_published_rate"] = round(
            ug["prompt_tokens"]["sum"] / 1e6 * L.OPUS_PRICE_IN_PER_M
            + ug["completion_tokens"]["sum"] / 1e6 * L.OPUS_PRICE_OUT_PER_M, 2)
    out["luna_cost_usd"] = "UNPUBLISHED_PRICE__TOKEN_COUNTS_ONLY"

    for scope, ids in (("all", have_ref), ("heldout", heldout)):
        for arm in ("luna_refined",):
            out["comparisons"][f"{arm}_vs_GT__{scope}"] = run_pair(
                arms[arm], gt_get, ids, f"{arm} vs Opus-GT ({scope}) = ACCURACY")
            out["comparisons"][f"{arm}_vs_CSV__{scope}"] = run_pair(
                arms[arm], csv_get, ids, f"{arm} vs incumbent CSV ({scope}) = AGREEMENT")
        out["comparisons"][f"CSV_vs_GT__{scope}"] = run_pair(
            {sid: {"parsed_json": csv_get(sid)} for sid in ids}, gt_get, ids,
            f"incumbent CSV vs Opus-GT ({scope}) = INCUMBENT ACCURACY")

    # baseline (client prompt) vs refined, on the 10 tuning statements only -- the ONLY
    # set where both arms exist, so the lift is reported for exactly that set and labelled.
    p1 = arms["luna_client_p1"]
    p1_ids = sorted(set(p1) & have_gt & have_csv)
    out["baseline_vs_refined_on_10_tuning"] = {
        "ids": p1_ids,
        "luna_client_vs_GT": run_pair(p1, gt_get, p1_ids, "client-baseline vs GT (10 tuning)"),
        "luna_refined_vs_GT": run_pair(arms["luna_refined"], gt_get, p1_ids,
                                       "refined vs GT (10 tuning)"),
        "luna_client_vs_CSV": run_pair(p1, csv_get, p1_ids, "client-baseline vs CSV (10 tuning)"),
        "luna_refined_vs_CSV": run_pair(arms["luna_refined"], csv_get, p1_ids,
                                        "refined vs CSV (10 tuning)"),
    }

    dest = os.path.join(HERE, "scores_phase3.json")
    with open(dest, "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(f"wrote {dest}")

    # ---- console summary
    print(f"\ncorpus={len(corpus)} scoreable(GT&CSV)={len(have_ref)} heldout={len(heldout)}")
    for k, v in out["outcomes"].items():
        print(f"  outcomes {k}: {v}")
    for k in ("luna_refined_vs_GT__all", "CSV_vs_GT__all", "luna_refined_vs_CSV__all"):
        c = out["comparisons"].get(k)
        if not c:
            continue
        print(f"\n=== {c['label']}  n={c['n_statements']} ===")
        for f in S.PRIORITY:
            keys = [f] if f != "statementLevelSummary.utilisationPercent" else [
                f + "@extracted", f + "@derived"]
            for kk in keys:
                a = c["fields"].get(kk)
                if not a:
                    continue
                acc = a["accuracy"]
                print(f"  {kk:<52} n={a['n']:>5} scored={a['scored_n']:>5} "
                      f"acc={'None' if acc is None else format(acc,'.4f')} "
                      f"wrong={a['wrong_value']:>4} null={a['null_when_populated']:>4} "
                      f"halluc={a['hallucinated_when_null']:>4} bothnull={a['both_null']:>5}")
        if c["txn"]:
            t = c["txn"]
            print(f"  TXN rows pred={t['rows_pred']} ref={t['rows_ref']} matched={t['rows_matched']} "
                  f"P={t['micro_precision']:.4f} R={t['micro_recall']:.4f} F1={t['micro_f1']:.4f}")


if __name__ == "__main__":
    main()
