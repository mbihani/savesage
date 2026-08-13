"""ARM E: all 12 SBI PDFs under the row-completeness-fixed SBI_PROMPT.txt.

Arm D (the pre-fix prompt) is left untouched on disk so E-vs-D is a real comparison and
not a comparison against something that was overwritten.

runner.py is imported and used UNCHANGED -- same payload shape, same schema, same
max_tokens/effort, same 'statement.pdf' filename, same concurrency cap, same atomic
writes and terminal-only resume. Arm 'E' is registered into runner.PROMPTS so run_one's
prompt_path lookup resolves; no runner logic is touched.

The long statement is repeated (--reps) because its defect is STOCHASTIC: the pre-fix
prompt was row-exact on only 4 of 12 samples, so one sample cannot characterise either
arm. Repeats land in json_armE_rep{n}/.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import runner as R      # noqa: E402  -- UNCHANGED

LONG_SID = "1707857175"
PROMPT_PATH = os.path.join(R.SBI_DIR, "SBI_PROMPT.txt")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3,
                    help="extra repeats of the long statement, in json_armE_rep{n}/")
    ap.add_argument("--concurrency", type=int, default=2)
    a = ap.parse_args()

    prompt = open(PROMPT_PATH, encoding="utf-8").read()
    psha = hashlib.sha256(prompt.encode()).hexdigest()
    items = R.corpus()

    jobs = [("E", sid, fn, path) for sid, fn, path in items]
    long_item = [t for t in items if t[0] == LONG_SID][0]
    for rep in range(1, a.reps + 1):
        jobs.append((f"E_rep{rep}", *long_item))

    for arm, *_ in jobs:
        R.PROMPTS.setdefault(arm, PROMPT_PATH)

    log = os.path.join(HERE, "logs", "run_armE.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)

    def emit(m):
        line = f"{time.strftime('%H:%M:%S')} {m}"
        open(log, "a").write(line + "\n")
        print(line, flush=True)

    emit(f"START armE prompt_sha={psha[:12]} n_jobs={len(jobs)} conc={a.concurrency}")

    def work(j):
        arm, sid, fn, path = j
        try:
            rec, cached = R.run_one(arm, sid, fn, path, prompt, psha)
        except Exception as e:
            emit(f"EXC {arm} {sid} {type(e).__name__}: {e}")
            return None
        u = rec.get("usage_raw") or {}
        emit(f"{arm:<10} {sid:<12} n={rec.get('n_transactions')} "
             f"fr={rec.get('finish_reason')} outcome={rec.get('outcome')} "
             f"fc={rec.get('failure_class')} rl={(rec.get('meta') or {}).get('rate_limited')} "
             f"ct={u.get('completion_tokens')} {'CACHED' if cached else ''}")
        return rec

    with ThreadPoolExecutor(max_workers=min(a.concurrency, 2)) as ex:
        recs = [r for r in ex.map(work, jobs) if r]

    ok = sum(1 for r in recs if r.get("outcome") == "OK")
    rl = sum(1 for r in recs if (r.get("meta") or {}).get("rate_limited"))
    emit(f"DONE armE ok={ok}/{len(jobs)} calls_that_saw_429={rl}")


if __name__ == "__main__":
    main()
