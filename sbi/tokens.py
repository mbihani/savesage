#!/usr/bin/env python3
"""Token accounting from the VERBATIM `usage` blocks persisted per call.

Two things are DETERMINED here, not assumed:

 1. Whether reasoning tokens sit INSIDE or OUTSIDE completion_tokens. Checked per
    call as `prompt + completion == total`; if that identity holds while
    reasoning_tokens > 0, reasoning is INSIDE completion (the OpenAI convention).
    Reported as a ratio over all calls rather than asserted from a prior run.
 2. Whether any record truncated (finish_reason length/max_tokens) -- a truncated GT
    would silently penalise the challenger.

Luna's price is UNPUBLISHED: token counts only, no dollar figures and no
interpolation from a sibling model. Opus 5 cost IS computed, at its published rate.
"""
import argparse
import glob
import json
import os
import statistics
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.abspath(__file__))
OPUS_IN_PER_M = 5.00
OPUS_OUT_PER_M = 25.00

ARMS = {
    "luna_refined": "run_luna_refined",
    "luna_client_full": "run_luna_client",
    "luna_client_phase1": "run_p1_client",
    "opus_gt": "run_gt",
}


def flat(u):
    """usage -> the fields we report, tolerating provider naming differences."""
    u = u or {}
    d = u.get("completion_tokens_details") or {}
    pd = u.get("prompt_tokens_details") or {}
    return {
        "prompt": u.get("prompt_tokens", u.get("input_tokens")),
        "completion": u.get("completion_tokens", u.get("output_tokens")),
        "total": u.get("total_tokens"),
        "reasoning": u.get("reasoning_tokens", d.get("reasoning_tokens")),
        "cached": u.get("cache_read_input_tokens", pd.get("cached_tokens")),
        "cache_creation": u.get("cache_creation_input_tokens"),
    }


def summarise(recs, name, price=False):
    rows, ident_ok, ident_bad, reason_pos = [], 0, 0, 0
    outcomes, finishes = Counter(), Counter()
    keys_seen = Counter()
    for r in recs:
        outcomes[r.get("outcome")] += 1
        finishes[r.get("finish_reason")] += 1
        u = r.get("usage_raw")
        if u:
            for k in u:
                keys_seen[k] += 1
        f = flat(u)
        if f["prompt"] is None or f["completion"] is None:
            continue
        rows.append(f)
        if f["total"] is not None:
            if f["prompt"] + f["completion"] == f["total"]:
                ident_ok += 1
            else:
                ident_bad += 1
        if (f["reasoning"] or 0) > 0:
            reason_pos += 1

    def st(k):
        xs = [r[k] for r in rows if r.get(k) is not None]
        if not xs:
            return None
        return {"n": len(xs), "sum": sum(xs), "mean": round(statistics.mean(xs), 1),
                "median": statistics.median(xs), "max": max(xs), "min": min(xs)}

    out = {"arm": name, "n_records": len(recs), "n_with_usage": len(rows),
           "outcomes": dict(outcomes.most_common()),
           "finish_reasons": dict(finishes.most_common()),
           "usage_keys_present": dict(keys_seen.most_common()),
           "input": st("prompt"), "output": st("completion"), "total": st("total"),
           "reasoning": st("reasoning"), "cached_input": st("cached"),
           "prompt_plus_completion_equals_total": {
               "holds": ident_ok, "fails": ident_bad,
               "pct": round(100 * ident_ok / (ident_ok + ident_bad), 2)
                      if ident_ok + ident_bad else None},
           "records_with_reasoning_gt_0": reason_pos,
           "reasoning_inside_completion": (
               "YES - prompt+completion==total holds while reasoning>0"
               if ident_bad == 0 and reason_pos > 0 else
               "N/A - 0 records report reasoning>0" if reason_pos == 0 else
               "NO - identity fails on some records"),
           "truncated_records": [r.get("statement_id") for r in recs
                                 if r.get("finish_reason") in ("length", "max_tokens")],
           "rate_limited_calls": sum(1 for r in recs
                                     if (r.get("meta") or {}).get("rate_limited")),
           "attempts_gt_1": sum(1 for r in recs
                                if ((r.get("meta") or {}).get("attempts") or 1) > 1)}
    if price:
        i = out["input"]["sum"] if out["input"] else 0
        o = out["output"]["sum"] if out["output"] else 0
        out["cost_usd"] = {
            "note": "Opus 5 published list price, per 1M tokens",
            "rate_in_per_m": OPUS_IN_PER_M, "rate_out_per_m": OPUS_OUT_PER_M,
            "input_usd": round(i / 1e6 * OPUS_IN_PER_M, 4),
            "output_usd": round(o / 1e6 * OPUS_OUT_PER_M, 4),
            "total_usd": round(i / 1e6 * OPUS_IN_PER_M + o / 1e6 * OPUS_OUT_PER_M, 4),
            "per_statement_usd": round(
                (i / 1e6 * OPUS_IN_PER_M + o / 1e6 * OPUS_OUT_PER_M) / len(recs), 5)
            if recs else None}
    else:
        out["cost_usd"] = ("UNPUBLISHED for databricks-gpt-5-6-luna -- token counts "
                          "only; no dollar estimate and no interpolation from a "
                          "sibling model's rate")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "tokens.json"))
    a = ap.parse_args()
    res = {}
    for name, d in ARMS.items():
        files = sorted(glob.glob(os.path.join(ROOT, d, "json", "*.json")))
        recs = [json.loads(open(f).read()) for f in files]
        if not recs:
            continue
        res[name] = summarise(recs, name, price=(name == "opus_gt"))
    json.dump(res, open(a.out, "w"), indent=1)
    for name, v in res.items():
        print(f"\n===== {name}  n={v['n_records']} =====")
        for k in ("input", "output", "total", "reasoning", "cached_input"):
            print(f"  {k:<14} {v[k]}")
        print(f"  identity      {v['prompt_plus_completion_equals_total']}")
        print(f"  reasoning     {v['reasoning_inside_completion']}"
              f"  (records with reasoning>0: {v['records_with_reasoning_gt_0']})")
        print(f"  usage keys    {list(v['usage_keys_present'])}")
        print(f"  truncated     {len(v['truncated_records'])} {v['truncated_records'][:6]}")
        print(f"  rate_limited  {v['rate_limited_calls']}  attempts>1 {v['attempts_gt_1']}")
        print(f"  outcomes      {v['outcomes']}")
        print(f"  cost          {v['cost_usd']}")
    print("\nwrote", a.out)


if __name__ == "__main__":
    main()
