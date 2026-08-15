"""Luna 5.6 runner for the 11-file ICICI set under the CONVERTED CLIENT GEMINI SCHEMA.

THREE ARMS, one schema, one model, one effort. The only variable is the prompt text, so
any movement in the numbers is attributable:

  arm 'A' -- the NEW refined ICICI prompt       (icici/ICICI_PROMPT.txt)
  arm 'B' -- the PREVIOUS refined ICICI prompt  (gemini/ICICI_PROMPT_PREV.txt, = committed v2)
  arm 'C' -- the client's UNMODIFIED generic prompt (gemini/GEMINI_GENERIC_PROMPT.txt)

B is what isolates the prompt edit from the schema change: A-vs-B is the prompt, and
B-vs-C is refinement-vs-client under an identical schema. Without B, movement between A
and C could not be attributed to either.

NOTE on arm C's prompt file: it is extracted from the client's own
gemini-3-flash--prompt-shcema.txt SYSTEM_PROMPT block and is md5-identical to the copy
hdfc/ and sbi/ use. icici/GENERIC_PROMPT.txt is NOT the client generic prompt -- it is a
larger, already-enriched intermediate, so it is deliberately not used as the baseline.

SAFETY: the filename sent to the model is the neutral 'statement.pdf', never
os.path.basename(). The real ICICI filenames embed live card digits
(e.g. 4748XXXXXXXX5000) and the OpenAI `file` block puts the filename on the wire.
Both the real filename and the filename sent are recorded.
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
ICICI_DIR = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import luna as L  # noqa: E402

PDF_DIR = "/Users/mayanck.bihani/Downloads/output/ICICI/PDF"
SCHEMA_PATH = os.path.join(HERE, "GEMINI_SCHEMA.json")
PROMPTS = {
    "A": os.path.join(ICICI_DIR, "ICICI_PROMPT.txt"),
    "B": os.path.join(HERE, "ICICI_PROMPT_PREV.txt"),
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


def build_payload(pdf_b64, prompt):
    """The PROVEN Luna native-PDF shape: ONE user message, NO system message, an OpenAI
    `file` block FIRST carrying a base64 data URL, then the prompt as text.

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


def classify(rec, resp, meta, raw_text, parsed):
    """Outcome + failure_class. Infrastructure is NEVER a model failure."""
    if meta.get("ip_acl"):
        return "INFRA_IP_ACL", "infra"
    if resp is None:
        if meta.get("rate_limited"):
            return "INFRA_RATE_LIMITED", "infra"
        return "NETWORK_ERROR", "infra"
    if rec.get("finish_reason") == "length":
        return "TRUNCATED_AT_CAP", "cap"
    if parsed is None:
        return "UNPARSEABLE_JSON", "model"
    return "OK", None


def outdir_for(arm, output_dir=None):
    d = output_dir or os.path.join(HERE, f"json_arm{arm}")
    os.makedirs(d, exist_ok=True)
    return d


def run_one(arm, sid, filename, path, prompt, prompt_sha, force=False, output_dir=None):
    dest = os.path.join(outdir_for(arm, output_dir), f"{sid}.json")
    if os.path.exists(dest) and not force:
        try:
            prev = json.loads(open(dest).read())
            # Resume ONLY past TERMINAL records. An infra failure is NOT terminal --
            # inheriting one would bake a throttle into the measurement.
            if prev.get("outcome") and prev.get("failure_class") in (None, "cap", "model"):
                return prev, True
        except Exception:
            pass

    rec = {
        "sid": sid, "arm": arm, "model": MODEL,
        "pdf_real_filename": filename, "filename_sent_to_model": SAFE_FILENAME,
        "schema": "GEMINI_SCHEMA.json", "schema_leaf_count": 26,
        "prompt_path": PROMPTS[arm], "prompt_sha256": prompt_sha,
        "prompt_chars": len(prompt),
        "max_tokens": MAX_TOKENS, "reasoning_effort": EFFORT,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    resp, meta = L.invoke(build_payload(L.pdf_b64(path), prompt), model=MODEL,
                          max_attempts=8, timeout=1800)
    rec["meta"] = meta

    raw_text, parsed = "", None
    if resp is not None:
        choice = (resp.get("choices") or [{}])[0]
        rec["finish_reason"] = choice.get("finish_reason")
        rec["usage_raw"] = resp.get("usage")          # VERBATIM
        ok, detail = L.assert_token_identity(rec["usage_raw"])
        rec["token_identity_ok"] = ok
        rec["token_identity_detail"] = detail
        try:
            raw_text = L.extract_text(resp)
        except Exception as e:
            rec["extract_error"] = f"{type(e).__name__}: {e}"
        rec["raw_response_text"] = raw_text
        try:
            parsed = L.parse_json_strict(raw_text)
        except Exception:
            parsed = None

    rec["outcome"], rec["failure_class"] = classify(rec, resp, meta, raw_text, parsed)
    rec["parsed_json"] = parsed
    if isinstance(parsed, dict) and isinstance(parsed.get("transactions"), list):
        rec["n_transactions"] = len(parsed["transactions"])
        rec["n_cards"] = len(parsed.get("cards") or [])

    rec["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    L.atomic_write_json(dest, rec, indent=1, sort_keys=True)
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

    with open(PROMPTS[a.arm], encoding="utf-8") as fh:
        prompt = fh.read()
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
        emit(f"{tag:20s} {sid:<12} txn={rec.get('n_transactions')} cards={rec.get('n_cards')} "
             f"fr={rec.get('finish_reason')} pt={u.get('prompt_tokens')} "
             f"ct={u.get('completion_tokens')} tt={u.get('total_tokens')} "
             f"tokid={rec.get('token_identity_ok')} "
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
    bad = [r["sid"] for r in recs if r and r.get("token_identity_ok") is False]
    emit(f"DONE arm={a.arm} ok={ok}/{len(items)} calls_that_saw_429={rl} "
         f"token_identity_violations={bad}")


if __name__ == "__main__":
    main()
