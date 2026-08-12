"""Luna 5.6 runner for the 15-file HDFC set under the CONVERTED GEMINI SCHEMA.

Two arms, same schema, same model, same effort -- the ONLY variable is the prompt:
  arm 'hdfc'    : the updated HDFC-specific prompt (hdfc/HDFC_PROMPT.txt)
  arm 'generic' : the client's UNMODIFIED original Gemini prompt, as the CONTROL

Without the control arm, any change in the numbers is unattributable between "the
HDFC-specific porting helped" and "the schema change moved things".

REUSES the verified plumbing in gt298_lib (auth + 20-min token cache, whole-minute
429 backoff, IP-ACL classification, atomic writes, content/JSON extraction) and
hdfc_lib (outcome classification). Nothing about the transport is reinvented here.

OPERATIONAL CONSTRAINTS THAT SHAPED THIS FILE
---------------------------------------------
* The binding limit on this workspace is OUTPUT TOKENS PER MINUTE, WORKSPACE-WIDE --
  not QPS. A prior 188-call run hit 429s at concurrency 14. Default concurrency here
  is 1, max 2, and 429 backoff waits whole MINUTES because the window is a minute.
* 429 is recorded as RATE_LIMITED / 'infrastructure' and NEVER as a model failure.
* Every record is written atomically via a temp file + os.replace, and the runner is
  idempotent: an existing terminal record is left untouched, so a crash costs zero
  work. Multiple sessions on this project have died mid-run; resumability is the
  reason earlier sweeps survived.
* `usage_raw` is persisted VERBATIM. A usage field discarded at write time cannot be
  recovered without paying for the call again.
* The FILENAME SENT TO THE MODEL IS SANITISED to 'statement.pdf'. The real filenames
  embed live card digits and the statement date, and the OpenAI `file` block puts the
  filename on the wire. Prior work measured filename contamination at 0, so this costs
  nothing and removes PII from the request.
"""

import argparse
import json
import os
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/Users/mayanck.bihani/Savesage/apev-wt-gt298/groundtruth298")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gt298_lib as G  # noqa: E402
import hdfc_lib as H  # noqa: E402

import pdf_rows as P  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_PATH = os.path.join(HERE, "GEMINI_SCHEMA.json")
GENERIC_PROMPT_PATH = os.path.join(HERE, "GEMINI_GENERIC_PROMPT.txt")
HDFC_PROMPT_PATH = os.path.join(os.path.dirname(HERE), "HDFC_PROMPT.txt")

MODEL = "databricks-gpt-5-6-luna"
MAX_TOKENS = 96000
EFFORT = "medium"

# The filename the model sees. NOT os.path.basename() -- see module docstring.
SAFE_FILENAME = "statement.pdf"

with open(SCHEMA_PATH) as _fh:
    GEMINI_SCHEMA = json.load(_fh)

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "credit_card_statement", "strict": True,
                    "schema": GEMINI_SCHEMA},
}


def load_prompt(arm):
    path = HDFC_PROMPT_PATH if arm == "hdfc" else GENERIC_PROMPT_PATH
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def build_payload(pdf_b64, prompt):
    """The PROVEN Luna native-PDF shape: OpenAI `file` block + data URL.

    The Anthropic `document` block is a hard 400 on this endpoint, and there is NO
    `model` field in the body -- the model is in the URL path.
    """
    return {
        "messages": [{
            "role": "user",
            "content": [
                {"type": "file", "file": {
                    "filename": SAFE_FILENAME,
                    "file_data": "data:application/pdf;base64," + pdf_b64,
                }},
                {"type": "text", "text": prompt},
            ],
        }],
        "max_tokens": MAX_TOKENS,
        "response_format": RESPONSE_FORMAT,
        "reasoning_effort": EFFORT,
    }


def outdir_for(arm):
    d = os.path.join(HERE, f"json_{arm}")
    os.makedirs(d, exist_ok=True)
    return d


def run_one(arm, sid, filename, path, prompt, prompt_sha, force=False):
    dest = os.path.join(outdir_for(arm), f"{sid}.json")
    if os.path.exists(dest) and not force:
        try:
            prev = json.loads(open(dest).read())
            # Resume only past TERMINAL records. A rate-limited or network-failed
            # record is NOT terminal -- it must be retried, not inherited.
            if prev.get("outcome") and prev.get("failure_class") in (None, "cap", "model"):
                return prev, True
        except Exception:
            pass

    rec = {
        "sid": sid, "arm": arm, "model": MODEL, "pdf_real_filename": filename,
        "filename_sent_to_model": SAFE_FILENAME,
        "statement_id": P.statement_id(filename),
        "schema": "GEMINI_SCHEMA.json", "schema_leaf_count": 26,
        "prompt_path": HDFC_PROMPT_PATH if arm == "hdfc" else GENERIC_PROMPT_PATH,
        "prompt_sha256": prompt_sha,
        "max_tokens": MAX_TOKENS, "reasoning_effort": EFFORT,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    b64 = G.pdf_b64(path)
    resp, meta = G.invoke(build_payload(b64, prompt), model=MODEL,
                          max_attempts=10, timeout=1800)
    rec["meta"] = meta

    raw_text, parsed = "", None
    if resp is not None:
        choice = (resp.get("choices") or [{}])[0]
        rec["finish_reason"] = choice.get("finish_reason")
        rec["usage_raw"] = resp.get("usage")          # VERBATIM
        try:
            raw_text = G.extract_text(resp)
        except Exception as e:
            rec["extract_error"] = f"{type(e).__name__}: {e}"
        rec["raw_response_text"] = raw_text
        try:
            parsed = G.parse_json_strict(raw_text)
        except Exception:
            parsed = None

    rec["outcome"], rec["failure_class"] = H.classify(rec, resp, meta, raw_text, parsed)
    rec["parsed_json"] = parsed
    if isinstance(parsed, dict) and isinstance(parsed.get("transactions"), list):
        rec["n_transactions"] = len(parsed["transactions"])
        rec["n_cards"] = len(parsed.get("cards") or [])

    rec["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    G.atomic_write_json(dest, rec, indent=1, sort_keys=True)
    return rec, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["hdfc", "generic"])
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.concurrency > 2:
        raise SystemExit("concurrency > 2 is banned: the workspace limit is output "
                         "tokens/min, workspace-wide. A prior run hit 429s at 14.")

    prompt = load_prompt(a.arm)
    psha = H.G_sha(prompt)
    items = P.corpus()
    if a.only:
        items = [t for t in items if a.only in t[1]]
    if a.limit:
        items = items[:a.limit]

    log = os.path.join(HERE, "logs", f"run_{a.arm}.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)

    def emit(msg):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        with open(log, "a") as fh:
            fh.write(line + "\n")
        print(line, flush=True)

    emit(f"START arm={a.arm} n={len(items)} conc={a.concurrency} "
         f"prompt_sha={psha[:12]} schema=GEMINI_SCHEMA(26 leaves)")

    def work(t):
        sid, fn, path = t
        try:
            rec, cached = run_one(a.arm, sid, fn, path, prompt, psha, force=a.force)
        except Exception as e:
            emit(f"EXC  {sid[:40]:42s} {type(e).__name__}: {e}")
            return None
        u = rec.get("usage_raw") or {}
        tag = "cached" if cached else rec.get("outcome")
        emit(f"{tag:22s} {P.statement_id(fn) or '-':<12} "
             f"txn={rec.get('n_transactions')} fr={rec.get('finish_reason')} "
             f"pt={u.get('prompt_tokens')} ct={u.get('completion_tokens')} "
             f"tt={u.get('total_tokens')} rl={(rec.get('meta') or {}).get('rate_limited')} "
             f"ms={(rec.get('meta') or {}).get('latency_ms')}")
        if not cached:
            time.sleep(random.uniform(1.0, 3.0))   # gentle spacing between calls
        return rec

    if a.concurrency == 1:
        recs = [work(t) for t in items]
    else:
        with ThreadPoolExecutor(max_workers=a.concurrency) as ex:
            recs = list(ex.map(work, items))

    ok = sum(1 for r in recs if r and r.get("outcome") == "OK")
    rl = sum(1 for r in recs if r and (r.get("meta") or {}).get("rate_limited"))
    emit(f"DONE arm={a.arm} ok={ok}/{len(items)} calls_that_saw_429={rl}")


if __name__ == "__main__":
    main()
