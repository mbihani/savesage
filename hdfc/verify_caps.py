#!/usr/bin/env python3
"""Audit output-cap safety, and force re-run of any record that hit its cap.

Two reasons this exists:

  1. `GT_MAX_TOKENS` was raised 32000 -> 64000 mid-run. The already-running driver had
     imported 32000, so records written before the raise carry the lower ceiling. This
     script proves whether that mattered: a ceiling can only have shaped a record that
     actually reached it, i.e. finish_reason in (length, max_tokens). Records that
     stopped naturally are unaffected -- max_tokens is a ceiling, not a sampling
     parameter.

  2. `run_one` treats failure_class 'cap' as TERMINAL, so a truncated record is
     returned untouched on resume rather than retried. That is right for idempotency
     but wrong after the ceiling changes, so any capped record is re-run with --force
     here rather than being silently kept.

A truncated GT record would silently PENALISE the challenger (rows the GT never emitted
count as challenger false positives), which is why this is audited rather than assumed.
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hdfc_lib as H

HERE = os.path.dirname(os.path.abspath(__file__))
CAPPED_FR = ("length", "max_tokens")


def audit(run_dir, cap_now):
    recs = []
    for f in sorted(glob.glob(os.path.join(HERE, run_dir, "json", "*.json"))):
        try:
            recs.append(json.loads(open(f).read()))
        except Exception:
            pass
    if not recs:
        return None
    capped = [r for r in recs
              if r.get("finish_reason") in CAPPED_FR
              or r.get("failure_class") == "cap"]
    comp = [(r.get("usage_raw") or {}).get("completion_tokens") or 0 for r in recs]
    caps_used = sorted({r.get("max_tokens") for r in recs})
    worst = max(recs, key=lambda r: (r.get("usage_raw") or {}).get("completion_tokens") or 0)
    return {
        "run_dir": run_dir,
        "records": len(recs),
        "max_tokens_values_in_records": caps_used,
        "max_tokens_now": cap_now,
        "max_completion_observed": max(comp) if comp else 0,
        "headroom_vs_lowest_cap_pct": (
            round(100 * (1 - max(comp) / min(c for c in caps_used if c)), 1)
            if comp and any(caps_used) else None),
        "capped_records": [r["sid"] for r in capped],
        "worst_sid": worst.get("sid"),
        "worst_txn": worst.get("n_transactions"),
        "finish_reasons": sorted({r.get("finish_reason") for r in recs}),
    }


def main():
    force = "--force-recap" in sys.argv
    out = {}
    for run_dir, cap in (("gt_full", H.GT_MAX_TOKENS),
                         ("phase3_refined", H.LUNA_MAX_TOKENS),
                         ("phase3_generic", H.LUNA_MAX_TOKENS),
                         ("phase1_baseline", H.LUNA_MAX_TOKENS),
                         ("phase2_refined", H.LUNA_MAX_TOKENS)):
        a = audit(run_dir, cap)
        if a:
            out[run_dir] = a
            flag = "  <-- CAPPED RECORDS PRESENT" if a["capped_records"] else ""
            print(f"{run_dir:18s} n={a['records']:4d} caps_in_records={a['max_tokens_values_in_records']} "
                  f"max_completion={a['max_completion_observed']:6d} "
                  f"headroom={a['headroom_vs_lowest_cap_pct']}% "
                  f"fr={a['finish_reasons']} capped={len(a['capped_records'])}{flag}")
            print(f"{'':18s}   densest: {a['worst_txn']} txn -> {a['max_completion_observed']} tok "
                  f"({a['worst_sid'][:44]})")

    H.G.atomic_write_json(os.path.join(HERE, "cap_audit.json"), out)

    capped = {d: a["capped_records"] for d, a in out.items() if a["capped_records"]}
    if not capped:
        print("\nNo record in any run reached its output cap. The mid-run "
              "GT_MAX_TOKENS 32000->64000 raise therefore changed no result: every "
              "record stopped naturally, and a ceiling cannot shape a completion that "
              "never reached it.")
        return

    print(f"\nCAPPED: {capped}")
    if not force:
        print("re-run them with:  python3 verify_caps.py --force-recap")
        return
    for run_dir, sids in capped.items():
        kind = "gt" if run_dir == "gt_full" else "luna"
        prompt = None if kind == "gt" else H.load_prompt(
            "generic" if "generic" in run_dir else "hdfc")
        matched, _, _ = H.build_join()
        by = {m["sid"]: m for m in matched}
        for sid in sids:
            m = by.get(sid)
            if not m:
                continue
            print(f"  re-running {run_dir}/{sid[:48]} at cap "
                  f"{H.GT_MAX_TOKENS if kind=='gt' else H.LUNA_MAX_TOKENS}")
            r = H.run_one(kind, sid, m["filename"], m["path"],
                          os.path.join(HERE, run_dir), prompt, force=True)
            print(f"    -> {r.get('outcome')} fr={r.get('finish_reason')} "
                  f"txn={r.get('n_transactions')}")


if __name__ == "__main__":
    main()
