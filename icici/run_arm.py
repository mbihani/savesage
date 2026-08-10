#!/usr/bin/env python3
"""Run one arm over the ICICI corpus (or a subset). Resumable: re-run to continue.

ARMS
  luna_client    Luna 5.6, native PDF, the CLIENT'S OWN production prompt -- the inner
                 string of `SYSTEM PROMPT.txt`, wrapper stripped. THE PHASE-1 BASELINE.
  luna_generic   Luna 5.6, native PDF, Axis-legacy LUNA_PROMPT.txt. Kept for
                 reproducibility only; NOT the ICICI baseline.
  luna_refined   Luna 5.6, native PDF, ICICI_PROMPT.txt
  opus_gt        Opus 5, native PDF, gt298_lib.GT_PROMPT (the shared reference
                 instrument -- unchanged across all three banks)

CONCURRENCY. The binding workspace ceiling is OUTPUT TOKENS PER MINUTE, workspace-wide,
and THREE workers (icici/hdfc/sbi) share it. Default and recommended value is 1; the
script hard-refuses >2.
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import icici_lib as L

_print = threading.Lock()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True,
                    choices=["luna_client", "luna_generic", "luna_refined", "opus_gt"])
    ap.add_argument("--outdir")
    ap.add_argument("--par", type=int, default=1)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sample", action="store_true",
                    help="restrict to the Phase-1 10-statement sample")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.par > 2:
        raise SystemExit("3 workers share one workspace output-TPM budget; par>2 refused")

    outdir = a.outdir or os.path.join(L.HERE, a.arm)
    os.makedirs(os.path.join(outdir, "json"), exist_ok=True)

    # opus_gt uses gt298_lib.GT_PROMPT verbatim (the shared cross-bank instrument);
    # luna_generic uses LUNA_PROMPT.txt verbatim (the stated generic baseline). These
    # differ only by the phrase "GROUND-TRUTH " on line 1 -- see icici_lib for the
    # measured shas and the Axis-Bank contamination note.
    prompt = {"luna_client": L.load_client_prompt,
              "luna_generic": L.load_generic_prompt,
              "opus_gt": L.load_gt_prompt,
              "luna_refined": L.load_refined_prompt}[a.arm]()

    corpus = L.discover_pdfs()
    if a.sample:
        ids = set(json.load(open(os.path.join(L.HERE, "phase1_sample.json")))["sample_ids"])
        corpus = [c for c in corpus if c[0] in ids]
        assert len(corpus) == len(ids), f"sample resolve mismatch {len(corpus)} vs {len(ids)}"
    if a.only:
        corpus = [c for c in corpus if c[0] in set(a.only)]

    todo = [c for c in corpus if a.force or not _terminal(outdir, c[0])]
    if a.limit:
        todo = todo[:a.limit]

    print(f"arm={a.arm} model={'luna' if a.arm.startswith('luna') else 'opus'} "
          f"corpus={len(corpus)} todo={len(todo)} par={a.par} "
          f"prompt_chars={len(prompt)} outdir={outdir}", flush=True)

    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=a.par) as ex:
        futs = {ex.submit(L.run_one, sid, f, p, outdir, a.arm, prompt, a.force): sid
                for sid, f, p in todo}
        for fut in as_completed(futs):
            sid = futs[fut]
            done += 1
            try:
                r = fut.result()
            except Exception as e:
                with _print:
                    print(f"{done}/{len(todo)} {sid} DRIVER_EXCEPTION "
                          f"{type(e).__name__}: {e}", flush=True)
                continue
            u = r.get("usage_raw") or {}
            m = r.get("meta") or {}
            with _print:
                print(f"{done}/{len(todo)} [{int(time.time()-t0)}s] {sid} {r.get('outcome')} "
                      f"fr={r.get('finish_reason')} txn={r.get('n_transactions')} "
                      f"card={r.get('n_cards')} att={m.get('attempts')} "
                      f"rl={m.get('rate_limited')} lat={m.get('latency_ms')}ms "
                      f"in={u.get('prompt_tokens')} out={u.get('completion_tokens')} "
                      f"tot={u.get('total_tokens')}", flush=True)
    print(f"DONE arm={a.arm} in {int(time.time()-t0)}s", flush=True)


def _terminal(outdir, sid):
    p = os.path.join(outdir, "json", f"{sid}.json")
    if not os.path.exists(p):
        return False
    try:
        prev = json.loads(open(p).read())
    except Exception:
        return False
    return bool(prev.get("outcome")) and prev.get("failure_class") in (None, "cap", "model")


if __name__ == "__main__":
    main()
