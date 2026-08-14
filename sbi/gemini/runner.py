"""Luna 5.6 runner for the 12-file SBI set under the CONVERTED CLIENT GEMINI SCHEMA.

THREE ARMS, one schema, one model, one effort. The only variable is the prompt, so any
movement in the numbers is attributable:

  arm 'A' -- the NEW refined SBI prompt        (sbi/SBI_PROMPT.txt)
  arm 'B' -- the PREVIOUS refined SBI prompt   (gemini/SBI_PROMPT_PREV.txt)
  arm 'C' -- the client's UNMODIFIED generic prompt, the client baseline

B is what isolates the prompt edit from the schema change: A-vs-B is the prompt, and
B-vs-C is the refinement-vs-client comparison under an identical schema. Without B a
movement between A and C could not be attributed to either.

Reuses the verified plumbing in gt298_lib (OAuth + token cache, whole-minute 429
backoff, IP-ACL classification, atomic writes, content/JSON extraction) and sbi_lib
(outcome classification). Nothing about the transport is reinvented.

OPERATIONAL CONSTRAINTS THAT SHAPED THIS FILE
---------------------------------------------
* The binding limit on this workspace is OUTPUT TOKENS PER MINUTE, WORKSPACE-WIDE --
  not QPS. A prior 188-call run hit 429s at concurrency 14. Concurrency is capped at 2.
* 429 and IP-ACL 403 are recorded as INFRASTRUCTURE, never as a model failure.
* Every record is written atomically and the runner resumes only past TERMINAL records:
  a rate-limited or network-failed record is retried, not inherited.
* `usage_raw` is persisted VERBATIM -- a usage field discarded at write time cannot be
  recovered without paying for the call again.
* THE FILENAME SENT TO THE MODEL IS THE NEUTRAL 'statement.pdf', never
  os.path.basename(). The real filenames embed a 16-digit number and the statement
  date, and the OpenAI `file` block puts the filename on the wire. (11 of these 12
  numbers fail the Luhn checksum and several share the template 879?????3479959???,
  so they are not card PANs; prior work also measured that the model does not read
  them -- lastFourDigit disagreed with the filename 12/12 while matching the PDF
  12/12. Sanitising costs nothing and keeps the digits out of the request.)
"""

import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
SBI_DIR = os.path.dirname(HERE)
sys.path.insert(0, SBI_DIR)

import gt298_lib as G      # noqa: E402
import sbi_lib as S        # noqa: E402

PDF_DIR = "/Users/mayanck.bihani/Downloads/output/SBI/PDF"
SCHEMA_PATH = os.path.join(HERE, "GEMINI_SCHEMA.json")
PROMPTS = {
    "A": os.path.join(SBI_DIR, "SBI_PROMPT.txt"),
    "B": os.path.join(HERE, "SBI_PROMPT_PREV.txt"),
    "C": os.path.join(HERE, "GEMINI_GENERIC_PROMPT.txt"),
}

MODEL = "databricks-gpt-5-6-luna"
MAX_TOKENS = 96000
EFFORT = "medium"
SAFE_FILENAME = "statement.pdf"        # NOT os.path.basename() -- see module docstring

with open(SCHEMA_PATH) as _fh:
    GEMINI_SCHEMA = json.load(_fh)

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "credit_card_statement", "strict": True,
                    "schema": GEMINI_SCHEMA},
}


def corpus(pdf_dir=PDF_DIR):
    """Return `(sid, filename, path)` for a PDF corpus in deterministic order."""
    out = []
    for f in sorted(os.listdir(pdf_dir)):
        if not f.lower().endswith(".pdf"):
            continue
        m = re.match(r"^decrypt_(?:encrypt_)?(\d+)_", f)
        if not m:
            raise RuntimeError(f"PDF off-convention: {f}")
        out.append((m.group(1), f, os.path.join(pdf_dir, f)))
    ids = [t[0] for t in out]
    if len(set(ids)) != len(ids):
        raise RuntimeError("duplicate statement ids in the corpus")
    return out


def load_prompt(arm):
    with open(PROMPTS[arm], encoding="utf-8") as fh:
        return fh.read()


def build_payload(pdf_b64, prompt):
    """The PROVEN Luna native-PDF shape: ONE user message, NO system message, an
    OpenAI `file` block carrying a base64 data URL, then the prompt as text.

    The Anthropic `document` block is a hard 400 on this endpoint, and there is NO
    `model` key in the body -- the model is in the URL path.
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


def outdir_for(arm, output_dir=None):
    d = output_dir or os.path.join(HERE, f"json_arm{arm}")
    os.makedirs(d, exist_ok=True)
    return d


def run_one(arm, sid, filename, path, prompt, prompt_sha, force=False, output_dir=None):
    dest = os.path.join(outdir_for(arm, output_dir), f"{sid}.json")
    if os.path.exists(dest) and not force:
        try:
            prev = json.loads(open(dest).read())
            # Resume ONLY past terminal records. RATE_LIMITED / NETWORK_ERROR are not
            # terminal -- inheriting one would silently bake an infrastructure failure
            # into the measurement.
            if prev.get("outcome") and prev.get("failure_class") in (None, "cap", "model"):
                return prev, True
        except Exception:
            pass

    rec = {
        "sid": sid, "arm": arm, "model": MODEL, "pdf_real_filename": filename,
        "filename_sent_to_model": SAFE_FILENAME,
        "schema": "GEMINI_SCHEMA.json", "schema_leaf_count": 26,
        "prompt_path": PROMPTS[arm], "prompt_sha256": prompt_sha,
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

    rec["outcome"], rec["failure_class"] = S.classify(rec, resp, meta, raw_text, parsed)
    rec["parsed_json"] = parsed
    if isinstance(parsed, dict) and isinstance(parsed.get("transactions"), list):
        rec["n_transactions"] = len(parsed["transactions"])
        rec["n_cards"] = len(parsed.get("cards") or [])

    rec["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    G.atomic_write_json(dest, rec, indent=1, sort_keys=True)
    return rec, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True, choices=["A", "B", "C"])
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--pdf-dir", default=PDF_DIR)
    ap.add_argument("--output-dir", default=None)
    a = ap.parse_args()
    if a.concurrency > 2:
        raise SystemExit("concurrency > 2 is banned: the workspace limit is output "
                         "tokens/min, workspace-wide. A prior run hit 429s at 14.")

    prompt = load_prompt(a.arm)
    psha = hashlib.sha256(prompt.encode()).hexdigest()
    items = corpus(a.pdf_dir)
    if a.only:
        items = [t for t in items if a.only in t[0] or a.only in t[1]]
    if a.limit:
        items = items[:a.limit]

    log = os.path.join(HERE, "logs", f"run_arm{a.arm}.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)

    def emit(msg):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        with open(log, "a") as fh:
            fh.write(line + "\n")
        print(line, flush=True)

    emit(f"START arm={a.arm} prompt={os.path.basename(PROMPTS[a.arm])} n={len(items)} "
         f"conc={a.concurrency} prompt_sha={psha[:12]} schema=GEMINI_SCHEMA(26 leaves)")

    def work(t):
        sid, fn, path = t
        try:
            rec, cached = run_one(a.arm, sid, fn, path, prompt, psha, force=a.force,
                                  output_dir=a.output_dir)
        except Exception as e:
            emit(f"EXC  {sid:12s} {type(e).__name__}: {e}")
            return None
        u = rec.get("usage_raw") or {}
        tag = "cached" if cached else rec.get("outcome")
        emit(f"{tag:22s} {sid:<12} txn={rec.get('n_transactions')} "
             f"fr={rec.get('finish_reason')} pt={u.get('prompt_tokens')} "
             f"ct={u.get('completion_tokens')} tt={u.get('total_tokens')} "
             f"rl={(rec.get('meta') or {}).get('rate_limited')} "
             f"ms={(rec.get('meta') or {}).get('latency_ms')}")
        if not cached:
            time.sleep(random.uniform(1.0, 3.0))
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
