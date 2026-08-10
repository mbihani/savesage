"""SBI native-PDF evaluation plumbing.

Reuses the validated Axis-298 pieces rather than rebuilding them:
  * auth / 20-min proactive token refresh / 429 whole-minute backoff /
    IP-ACL-vs-auth 403 discrimination / atomic writes / extract_text /
    parse_json_strict / usage_row / GT_PROMPT / GT_SCHEMA
        -> gt298_lib.py   (the module that built the Opus-5 Axis ground truth)
  * the Luna native-PDF request SHAPE (OpenAI `file` block)
        -> luna_lib.py    (Luna rejects the Anthropic `document` block, hard 400)

Deliberate differences from the Axis harness, each forced by a MEASURED fact
about this corpus rather than assumed:

  1. CORPUS ID CONVENTION. gt298_lib._ID_RE is `^decrypt_(?:encrypt_)?(\\d+)_`.
     Two of the 300 SBI PDFs are named `decrypt_gmail:<digits>:<hash>_...`, which
     that regex REJECTS (gt298_lib.discover_pdfs would raise). Those two are real
     statements with real CSV rows, so the id convention is widened here rather
     than dropping them.

  2. CONCURRENCY 1. Three workers share one workspace output-TPM budget and this
     is the highest-output-volume of the three.

  3. OPUS max_tokens RAISED 32000 -> 64000. SBI is the transaction-dense bank; a
     truncated GT record would silently penalise the challenger, so the cap is
     lifted and every record's finish_reason is recorded.
"""

import hashlib
import json
import os
import re
import sys
import time

# GT prompt/schema source. THIS directory's vendored copy wins.
#
# The original pin was a git worktree under another directory; that worktree was
# deleted mid-evaluation and every `gt` sweep began dying instantly with
# ModuleNotFoundError -- which looked exactly like a mysterious background-process
# kill and cost several restarts to diagnose. The vendored copy is byte-identical
# (26,435 B, sha256 131de4fb02dfe15c) and restored from
# `git show feat/groundtruth-298:groundtruth298/gt298_lib.py`, so GT_PROMPT stays
# 8,243 chars / sha256 a14219f16d348589 -- identical to the instrument that produced
# every GT record already on disk. The old path is kept as a fallback.
sys.path.insert(0, "/Users/mayanck.bihani/Savesage/apev-wt-gt298/groundtruth298")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gt298_lib as G  # noqa: E402

_GT_PROMPT_SHA = "a14219f16d348589"
if hashlib.sha256(G.GT_PROMPT.encode()).hexdigest()[:16] != _GT_PROMPT_SHA:
    raise SystemExit(
        f"GT_PROMPT CHANGED: expected {_GT_PROMPT_SHA}, got "
        f"{hashlib.sha256(G.GT_PROMPT.encode()).hexdigest()[:16]}. Every GT record on "
        f"disk was produced with the former; refusing to mix two instruments.")

ROOT = os.path.dirname(os.path.abspath(__file__))
PDF_ROOT = "/Users/mayanck.bihani/Downloads/remaining pdfs ground truth/sbi-pdfs"
CSV_PATH = "/Users/mayanck.bihani/Downloads/remaining pdfs ground truth/sbi.csv"

LUNA_MODEL = "databricks-gpt-5-6-luna"
GT_MODEL = "databricks-claude-opus-5"

LUNA_MAX_TOKENS = 96000
LUNA_EFFORT = "medium"
# Raised from the Axis 32000. SBI is transaction-dense; a truncated GT silently
# penalises the challenger, so the cap is lifted well clear of the worst case.
GT_MAX_TOKENS = 64000
GT_EFFORT = "medium"

# THE BASELINE IS THE CLIENT'S OWN PRODUCTION PROMPT, not luna_prompt/LUNA_PROMPT.txt.
# LUNA_PROMPT.txt is ground-truth-flavoured ("this output will be used to score other
# models") and is the wrong instrument for a production baseline. It is retained here
# only as a clearly-labelled third arm, never as "the baseline".
CLIENT_PROMPT_PATH = "/Users/mayanck.bihani/Savesage/SYSTEM PROMPT.txt"
GTFLAVOUR_PROMPT_PATH = "/Users/mayanck.bihani/Savesage/luna_prompt/LUNA_PROMPT.txt"
REFINED_PROMPT_PATH = os.path.join(ROOT, "SBI_PROMPT.txt")

CLIENT_PROMPT_SHA256_EXPECTED = \
    "c618380769746a4abe988cb12cb947d979bac81424deb371286183a3454ca362"

# SCHEMA IS UNCHANGED across banks and across arms -- cross-bank comparison
# depends on it. Only the prompt text is tuned.
SCHEMA = G.GT_SCHEMA
RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "credit_card_statement", "strict": True, "schema": SCHEMA},
}

# ---------------------------------------------------------------- corpus
# `decrypt_<id>_...` for 298 of 300; `decrypt_gmail:<id>:<hash>_...` for 2.
_ID_NUM = re.compile(r"^decrypt_(?:encrypt_)?(\d+)_")
_ID_GMAIL = re.compile(r"^decrypt_(gmail:\d+):")


def statement_id(filename):
    m = _ID_NUM.match(filename)
    if m:
        return m.group(1)
    m = _ID_GMAIL.match(filename)
    if m:
        return m.group(1).replace(":", "_")   # 'gmail:1126962' -> 'gmail_1126962'
    return None


def discover_pdfs():
    """-> [(statement_id, filename, path)] sorted deterministically. 300 expected."""
    out, odd = [], []
    for f in sorted(os.listdir(PDF_ROOT)):
        if not f.lower().endswith(".pdf"):
            continue
        sid = statement_id(f)
        if sid is None:
            odd.append(f)
            continue
        out.append((sid, f, os.path.join(PDF_ROOT, f)))
    if odd:
        raise RuntimeError(f"{len(odd)} PDF(s) off-convention: {odd}")
    ids = [t[0] for t in out]
    if len(set(ids)) != len(ids):
        from collections import Counter
        raise RuntimeError(f"dup ids: {[k for k, v in Counter(ids).items() if v > 1]}")
    # sort by filename (already) -> stable; numeric ids sorted numerically after
    out.sort(key=lambda t: (0, int(t[0])) if t[0].isdigit() else (1, 0, t[0]))
    return out


# ---------------------------------------------------------------- prompts
_WRAPPER_RE = re.compile(r'^\s*SYSTEM_PROMPT\s*=\s*"""')


def strip_python_wrapper(src):
    """`SYSTEM PROMPT.txt` is a PYTHON SOURCE FILE, not raw prompt text: it opens
    `SYSTEM_PROMPT = \"\"\"You are an AI that...` and closes `...Use the Reward Points
    for this.\"\"\"`. Only the inner string literal is the prompt -- shipping the
    assignment statement to the model would be a prompt defect. Fails loud rather
    than silently sending the wrapper if the file shape ever changes."""
    m = _WRAPPER_RE.match(src)
    if not m:
        raise RuntimeError("SYSTEM PROMPT.txt: missing expected 'SYSTEM_PROMPT = \"\"\"' prefix")
    t = src[m.end():].rstrip()
    if not t.endswith('"""'):
        raise RuntimeError('SYSTEM PROMPT.txt: missing expected closing \"\"\"')
    return t[:-3]


def load_prompt(which):
    """'client'  -> the client's production prompt (THE Phase-1 baseline)
    'refined' -> the SBI-specific prompt this evaluation derives
    'gtflavour' -> luna_prompt/LUNA_PROMPT.txt, kept only as a labelled extra arm
    """
    if which == "client":
        raw = open(CLIENT_PROMPT_PATH, "rb").read()
        got = hashlib.sha256(raw).hexdigest()
        if got != CLIENT_PROMPT_SHA256_EXPECTED:
            raise RuntimeError(f"SYSTEM PROMPT.txt sha256 changed: {got}")
        return strip_python_wrapper(raw.decode("utf-8"))
    p = {"refined": REFINED_PROMPT_PATH, "gtflavour": GTFLAVOUR_PROMPT_PATH}[which]
    with open(p, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- payloads
def luna_payload(pdf_path, pdf_b64, prompt):
    """The proven Luna native-PDF shape. `reasoning_effort` is Luna's effort param;
    it is a hard 400 on Claude endpoints."""
    return {
        "messages": [{
            "role": "user",
            "content": [
                {"type": "file", "file": {
                    "filename": os.path.basename(pdf_path),
                    "file_data": "data:application/pdf;base64," + pdf_b64,
                }},
                {"type": "text", "text": prompt},
            ],
        }],
        "max_tokens": LUNA_MAX_TOKENS,
        "response_format": RESPONSE_FORMAT,
        "reasoning_effort": LUNA_EFFORT,
    }


def gt_payload(pdf_b64):
    """Anthropic `document` block + thinking/output_config. Shared reference
    instrument: GT_PROMPT and GT_SCHEMA unchanged across all three banks."""
    return {
        "messages": [{
            "role": "user",
            "content": [
                {"type": "document", "source": {
                    "type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
                {"type": "text", "text": G.GT_PROMPT},
            ],
        }],
        "max_tokens": GT_MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": GT_EFFORT},
        "response_format": RESPONSE_FORMAT,
    }


# ---------------------------------------------------------------- outcomes
def classify(rec, resp, meta, raw_text, parsed):
    """Infrastructure and model defects are kept strictly apart so a rate-limited
    call can never be reported as 'the model failed to extract'."""
    if meta.get("ip_acl"):
        return "BLOCKED_IP_ACL", "infrastructure"
    if resp is None:
        st = meta.get("http_status")
        if st == 429:
            return "RATE_LIMITED", "infrastructure"
        if st and st >= 500:
            return "HTTP_5XX", "infrastructure"
        if st is None:
            return "NETWORK_ERROR", "infrastructure"
        return "HTTP_4XX", "model"

    fr = rec.get("finish_reason")
    if not (raw_text or "").strip():
        return ("TRUNCATED_EMPTY" if fr in ("length", "max_tokens") else "ZERO_LENGTH_BODY"), "model"
    if parsed is None:
        if fr in ("length", "max_tokens"):
            return "TRUNCATED_OUTPUT_CAP", "cap"
        return "JSON_PARSE_FAIL", "model"

    tx = parsed.get("transactions") if isinstance(parsed, dict) else None
    if isinstance(tx, str):
        return "ESCAPED_TRANSACTIONS_STRING", "model"
    if not isinstance(tx, list):
        return "SCHEMA_VIOLATION", "model"
    if fr in ("length", "max_tokens"):
        return "TRUNCATED_BUT_PARSED", "cap"
    return "OK", None


def run_one(arm, sid, fname, pdf_path, outdir, prompt=None, force=False):
    """One statement -> one atomically-persisted record. Idempotent resume: an
    existing terminal record is returned untouched so a crash costs zero work.

    arm: 'luna_generic' | 'luna_refined' | 'gt'
    """
    dest = os.path.join(outdir, "json", f"{sid}.json")
    if os.path.exists(dest) and not force:
        try:
            prev = json.loads(open(dest).read())
            if prev.get("outcome") and prev.get("failure_class") in (None, "cap", "model"):
                return prev
        except Exception:
            pass  # unreadable -> re-run

    b64 = G.pdf_b64(pdf_path)
    if arm == "gt":
        payload, model, mt, eff = gt_payload(b64), GT_MODEL, GT_MAX_TOKENS, GT_EFFORT
    else:
        payload, model, mt, eff = (luna_payload(pdf_path, b64, prompt), LUNA_MODEL,
                                   LUNA_MAX_TOKENS, LUNA_EFFORT)

    rec = {
        "statement_id": sid, "arm": arm, "model": model, "pdf": fname,
        "input_path": "native_pdf_document_block" if arm == "gt" else "native_pdf_file_block",
        "call_type": "single_full_schema", "max_tokens": mt, "effort": eff,
        "prompt_sha256": _sha(prompt) if prompt else _sha(G.GT_PROMPT),
        "prompt_chars": len(prompt) if prompt else len(G.GT_PROMPT),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    resp, meta = G.invoke(payload, model=model, max_attempts=10, timeout=1500)
    rec["meta"] = meta

    raw_text, parsed = "", None
    if resp is not None:
        choice = (resp.get("choices") or [{}])[0]
        rec["finish_reason"] = choice.get("finish_reason")
        # USAGE PERSISTED VERBATIM: provider usage semantics differ and a field
        # dropped at write time is unrecoverable without re-running the call.
        rec["usage_raw"] = resp.get("usage")
        try:
            raw_text = G.extract_text(resp)
        except Exception as e:
            rec["extract_error"] = f"{type(e).__name__}: {e}"
        rec["raw_response_text"] = raw_text
        try:
            parsed = G.parse_json_strict(raw_text)
        except Exception:
            parsed = None

    # `transactions` arriving as an escaped JSON string is its own outcome class
    # (Opus did this 7x on Axis). Recover it so the record is still scoreable,
    # but keep the class so the defect is reported.
    if isinstance(parsed, dict) and isinstance(parsed.get("transactions"), str):
        rec["escaped_transactions_recovered"] = False
        try:
            parsed = dict(parsed, transactions=json.loads(parsed["transactions"]))
            rec["escaped_transactions_recovered"] = True
        except Exception:
            pass

    rec["parsed_json"] = parsed
    rec["outcome"], rec["failure_class"] = classify(rec, resp, meta, raw_text, parsed)
    if rec.get("escaped_transactions_recovered"):
        rec["outcome"] = "ESCAPED_TRANSACTIONS_STRING"
        rec["failure_class"] = "model"
    if isinstance(parsed, dict) and isinstance(parsed.get("transactions"), list):
        rec["n_transactions"] = len(parsed["transactions"])
        rec["n_cards"] = len(parsed.get("cards") or [])

    G.atomic_write_json(dest, rec, indent=1, sort_keys=True)
    return rec


def _sha(s):
    import hashlib
    return hashlib.sha256((s or "").encode()).hexdigest()
