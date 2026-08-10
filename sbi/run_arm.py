#!/usr/bin/env python3
"""Run one arm over the SBI corpus. Resumable: re-run to continue.

CONCURRENCY 1 BY DEFAULT AND HARD-CAPPED AT 2. The binding workspace limit is
OUTPUT TOKENS PER MINUTE, not QPS, and THREE workers share that one budget while
this SBI arm is the highest-output-volume of the three.
"""
import argparse
import hashlib
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sbi_lib as L

ROOT = os.path.dirname(os.path.abspath(__file__))
_p = threading.Lock()

ARMS = {
    # THE Phase-1 baseline: the client's own production prompt (SYSTEM PROMPT.txt).
    "luna_client": ("client", "run_luna_client"),
    "luna_refined": ("refined", "run_luna_refined"),
    # kept only as a clearly-labelled extra arm; NOT the baseline (it is
    # ground-truth-flavoured and shares the GT's prompt instrument)
    "luna_gtflavour": ("gtflavour", "run_luna_generic"),
    "gt": (None, "run_gt"),
}


def _sha(t):
    return hashlib.sha256((t or "").encode()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("arm", choices=sorted(ARMS))
    ap.add_argument("--par", type=int, default=1)
    ap.add_argument("--only", nargs="*", help="statement ids")
    ap.add_argument("--only-file", help="json file with a sample_ids list")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--outdir")
    a = ap.parse_args()
    if a.par > 2:
        raise SystemExit("3 workers share one workspace output-TPM budget; par>2 refused")

    which, default_dir = ARMS[a.arm]
    outdir = a.outdir or os.path.join(ROOT, default_dir)
    os.makedirs(os.path.join(outdir, "json"), exist_ok=True)

    prompt = L.load_prompt(which) if which else None
    corpus = L.discover_pdfs()
    if len(corpus) != 300:
        print(f"WARNING: expected 300 PDFs, discovered {len(corpus)}", flush=True)

    only = set(a.only or [])
    if a.only_file:
        only |= set(json.load(open(a.only_file))["sample_ids"])
    if only:
        corpus = [c for c in corpus if c[0] in only]
        missing = only - {c[0] for c in corpus}
        if missing:
            raise SystemExit(f"ids not in corpus: {sorted(missing)}")

    # PROMPT PROVENANCE GUARD. The prompt is read ONCE, here, and pinned for the whole
    # arm. Any record already in outdir must carry the same prompt hash, or the arm
    # would silently mix two prompts and its numbers would mean nothing. This exact
    # hazard occurred once: SBI_PROMPT.txt was edited while the arm was in flight.
    if prompt is not None:
        want = _sha(prompt)
        # NB: do NOT name a loop variable `_p` here -- that is the module-level print
        # lock, and rebinding it makes it a function-local, so the `with _p:` calls
        # below raise UnboundLocalError. Cost a full luna_refined sweep once.
        for sid, _f, _path in corpus:
            q = os.path.join(outdir, "json", f"{sid}.json")
            if not os.path.exists(q):
                continue
            try:
                got = (json.loads(open(q).read()) or {}).get("prompt_sha256")
            except Exception:
                continue
            if got and got != want:
                raise SystemExit(
                    f"PROMPT PROVENANCE MISMATCH in {outdir}: record {sid} was produced "
                    f"with prompt {got[:16]} but the prompt on disk is {want[:16]}. "
                    f"Either restore that prompt or use a fresh --outdir; refusing to "
                    f"mix two prompts inside one arm.")

    todo = [c for c in corpus if a.force or not _terminal(outdir, c[0])]
    if a.limit:
        todo = todo[:a.limit]
    print(f"arm={a.arm} outdir={outdir} corpus={len(corpus)} todo={len(todo)} par={a.par} "
          f"prompt={'GT_PROMPT' if which is None else which} "
          f"prompt_chars={len(prompt) if prompt else len(L.G.GT_PROMPT)}", flush=True)

    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=a.par) as ex:
        futs = {ex.submit(L.run_one, a.arm, sid, f, p, outdir, prompt, a.force): sid
                for sid, f, p in todo}
        for fut in as_completed(futs):
            sid = futs[fut]
            done += 1
            try:
                r = fut.result()
            except Exception as e:
                with _p:
                    print(f"{done}/{len(todo)} {sid} DRIVER_EXCEPTION {type(e).__name__}: {e}",
                          flush=True)
                continue
            u = r.get("usage_raw") or {}
            m = r.get("meta") or {}
            with _p:
                print(f"{done}/{len(todo)} {sid} {r.get('outcome')} fr={r.get('finish_reason')} "
                      f"txn={r.get('n_transactions')} att={m.get('attempts')} "
                      f"rl={m.get('rate_limited')} lat={m.get('latency_ms')}ms "
                      f"in={u.get('prompt_tokens')} out={u.get('completion_tokens')} "
                      f"elapsed={int(time.time() - t0)}s", flush=True)


def _terminal(outdir, sid):
    p = os.path.join(outdir, "json", f"{sid}.json")
    if not os.path.exists(p):
        return False
    try:
        r = json.loads(open(p).read())
        return bool(r.get("outcome")) and r.get("failure_class") in (None, "cap", "model")
    except Exception:
        return False


if __name__ == "__main__":
    main()
