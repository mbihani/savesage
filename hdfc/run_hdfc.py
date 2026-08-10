#!/usr/bin/env python3
"""Driver for the HDFC runs. Resumable; re-run to continue where it stopped.

Concurrency 1 by default and HARD-CAPPED at 2: three workers share ONE workspace
output-tokens-per-minute budget, and that limit (not QPS) is what returns 429.
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hdfc_lib as H

HERE = os.path.dirname(os.path.abspath(__file__))
_lock = threading.Lock()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["luna", "gt"], required=True)
    ap.add_argument("--prompt", choices=["generic", "hdfc"], default="generic",
                    help="luna only; GT always uses the unchanged shared GT_PROMPT")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--par", type=int, default=1)
    ap.add_argument("--sample", action="store_true", help="only the recorded 10-stmt sample")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.par > 2:
        raise SystemExit("3 workers share one workspace TPM budget; concurrency >2 refused")

    outdir = a.outdir if os.path.isabs(a.outdir) else os.path.join(HERE, a.outdir)
    os.makedirs(os.path.join(outdir, "json"), exist_ok=True)

    matched, _, _ = H.build_join()
    if a.sample:
        prof = json.load(open(os.path.join(HERE, "corpus_profile.json")))
        keep = {p["sid"] for p in prof["sample"]}
        matched = [m for m in matched if m["sid"] in keep]

    prompt = H.load_prompt(a.prompt) if a.model == "luna" else None
    todo = [m for m in matched
            if a.force or not os.path.exists(os.path.join(outdir, "json", f"{m['sid']}.json"))]
    if a.limit:
        todo = todo[:a.limit]

    print(f"model={a.model} prompt={a.prompt if a.model=='luna' else 'GT_PROMPT(unchanged)'} "
          f"n={len(matched)} todo={len(todo)} par={a.par} out={outdir}", flush=True)
    if prompt:
        print(f"prompt_sha256={H.G_sha(prompt)} chars={len(prompt)}", flush=True)

    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.par) as ex:
        futs = {ex.submit(H.run_one, a.model, m["sid"], m["filename"], m["path"],
                          outdir, prompt, a.force): m["sid"] for m in todo}
        for fut in as_completed(futs):
            sid = futs[fut]
            done += 1
            try:
                r = fut.result()
            except Exception as e:
                with _lock:
                    print(f"{done}/{len(todo)} DRIVER_EXCEPTION {sid} {type(e).__name__}: {e}",
                          flush=True)
                continue
            u = r.get("usage_raw") or {}
            mt = r.get("meta") or {}
            with _lock:
                print(f"{done}/{len(todo)} [{int(time.time()-t0)}s] {r.get('outcome')} "
                      f"fr={r.get('finish_reason')} txn={r.get('n_transactions')} "
                      f"att={mt.get('attempts')} rl={mt.get('rate_limited')} "
                      f"lat={mt.get('latency_ms')}ms in={u.get('prompt_tokens')} "
                      f"out={u.get('completion_tokens')} {sid[:52]}", flush=True)


if __name__ == "__main__":
    main()
