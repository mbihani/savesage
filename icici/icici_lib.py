"""Shared plumbing for the ICICI native-PDF evaluation (Luna 5.6 vs Opus-5 GT vs incumbent CSV).

Reuses the validated Axis-298 machinery rather than rebuilding it:

  * auth / OAuth re-mint / 429 output-TPM backoff / IP-ACL classification /
    extract_text / parse_json_strict / GT_PROMPT / GT_SCHEMA  -> gt298_lib.py
  * Luna request SHAPE (OpenAI `file` content block) + outcome classifier -> luna_lib.py

TWO DELIBERATE DIVERGENCES FROM THE AXIS HARNESS, both forced by the ICICI corpus:

  1. CORPUS DISCOVERY. gt298_lib._ID_RE is `^decrypt_(?:encrypt_)?(\\d+)_`, which
     silently DROPS 4 of the 304 ICICI PDFs whose token is not numeric
     (`decrypt_gmail:1030239:19fe3dce17aa0588_...`). Dropping 4 statements to keep a
     regex is a measurement error, so the id scheme here is widened and the PDF
     FILENAME -- not the id -- is the authoritative join key against the CSV `link`
     column (verified: 304/304 PDFs have exactly one CSV row).

  2. PROMPT is a parameter, not a constant. Phase 1 runs the UNMODIFIED generic
     LUNA_PROMPT.txt; Phase 3 runs the ICICI-refined prompt. Both share
     GT_SCHEMA byte-for-byte so cross-bank comparison stays valid.

stdlib only -- pypi is blackholed on this machine.
"""

import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse

GT_DIR = "/Users/mayanck.bihani/Savesage/apev-wt-gt298/groundtruth298"
sys.path.insert(0, GT_DIR)
import gt298_lib as G  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PDF_DIR = "/Users/mayanck.bihani/Downloads/remaining pdfs ground truth/icici-pdfs"
CSV_PATH = "/Users/mayanck.bihani/Downloads/remaining pdfs ground truth/icici.csv"
GENERIC_PROMPT_PATH = "/Users/mayanck.bihani/Savesage/luna_prompt/LUNA_PROMPT.txt"
CLIENT_PROMPT_PATH = "/Users/mayanck.bihani/Savesage/SYSTEM PROMPT.txt"
REFINED_PROMPT_PATH = os.path.join(HERE, "ICICI_PROMPT.txt")

LUNA_MODEL = "databricks-gpt-5-6-luna"
OPUS_MODEL = "databricks-claude-opus-5"

LUNA_MAX_TOKENS = 96000
LUNA_EFFORT = "medium"
OPUS_MAX_TOKENS = 32000
OPUS_EFFORT = "medium"

# Opus 5 published list price per 1M tokens (gt298_lib.PRICE_*). Luna's price is
# UNPUBLISHED -- token counts only, never a dollar figure, never interpolated.
OPUS_PRICE_IN_PER_M = G.PRICE_IN_PER_M
OPUS_PRICE_OUT_PER_M = G.PRICE_OUT_PER_M

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "credit_card_statement", "strict": True, "schema": G.GT_SCHEMA},
}

# ---------------------------------------------------------------- prompts


# MEASURED, not assumed. Two facts about the "generic" prompt that the provenance
# README gets wrong, both verified byte-for-byte on 2026-08-10:
#
#  (a) LUNA_PROMPT.txt is 8,230 chars / sha e8e90c6c..., NOT the 8,243 chars /
#      sha a14219f1... the README pins. The README's sha is the sha of
#      gt298_lib.GT_PROMPT. The single difference is that LUNA_PROMPT.txt deletes
#      the words "GROUND-TRUTH " from line 1. Nothing else differs (unified diff:
#      1 line changed of 59). So the "generic challenger prompt" and the "Opus GT
#      prompt" are the SAME instrument bar that phrase -- a confound already
#      flagged in the README's own "Known caveat", and it is real.
#
#  (b) THE GENERIC PROMPT IS NOT BANK-NEUTRAL. It names Axis Bank on 5 of its 59
#      lines, and rule 1 states outright: 'issuerName -- ... For this corpus that
#      is "Axis Bank"'. Run unmodified against ICICI PDFs this is an instruction to
#      emit the WRONG ISSUER. It is carried into Phase 1 deliberately and unmodified
#      (that is the stated baseline), and the resulting damage is measured rather
#      than pre-empted. It also contaminates the Opus GT on issuerName specifically,
#      so issuerName is adjudicated against the PDF and never scored on GT alone.
GENERIC_PROMPT_SHA256 = "e8e90c6cf0fa68e7ddae91a4aa008ca32d65546811c79c10cc9025e4fd47cd9f"
GT_PROMPT_SHA256 = "a14219f16d3485893b497131218cea7a69b49631285103bf5adb9f6e065190ff"

# THE BASELINE INSTRUMENT IS THE CLIENT'S OWN PRODUCTION PROMPT, not LUNA_PROMPT.txt.
# LUNA_PROMPT.txt is ground-truth-flavoured ("this output will be used to score other
# models") AND hard-codes 'For this corpus that is "Axis Bank"', so it is the wrong
# instrument for an ICICI production baseline. `SYSTEM PROMPT.txt` is the right one.
#
# It is a PYTHON SOURCE FILE, not raw prompt text: it opens `SYSTEM_PROMPT = """` and
# closes `"""`. Only the inner string literal is sent. Measured on 2026-08-10:
#   file      10,111 bytes  sha256 c618380769746a4abe988cb12cb947d979bac81424deb371286183a3454ca362
#   inner     10,041 chars / 10,088 bytes (>chars: the file contains non-ASCII -- the
#             rupee sign and the '→' arrows used in its mapping rules)
#             sha256 9dc59e63b6957bf24ca3fdb9f7dee9389f5650dc030c7abfae7d3277ae025bac
CLIENT_PROMPT_FILE_SHA256 = "c618380769746a4abe988cb12cb947d979bac81424deb371286183a3454ca362"
CLIENT_PROMPT_INNER_SHA256 = "9dc59e63b6957bf24ca3fdb9f7dee9389f5650dc030c7abfae7d3277ae025bac"
_WRAP_PRE = 'SYSTEM_PROMPT = """'


def load_client_prompt():
    """The client's production prompt: the inner string literal ONLY, wrapper stripped.

    This is the Phase-1 baseline and the parent of the Phase-2 refinement.
    """
    raw = open(CLIENT_PROMPT_PATH, "rb").read()
    fsha = hashlib.sha256(raw).hexdigest()
    assert fsha == CLIENT_PROMPT_FILE_SHA256, f"client prompt FILE sha drifted: {fsha}"
    s = raw.decode("utf-8")
    assert s.startswith(_WRAP_PRE), "client prompt no longer opens with the SYSTEM_PROMPT wrapper"
    inner = s[len(_WRAP_PRE):].rstrip("\n")
    assert inner.endswith('"""'), "client prompt no longer closes with the wrapper"
    inner = inner[:-3]
    isha = hashlib.sha256(inner.encode()).hexdigest()
    assert isha == CLIENT_PROMPT_INNER_SHA256, f"client prompt INNER sha drifted: {isha}"
    return inner


def load_generic_prompt():
    """LUNA_PROMPT.txt -- kept ONLY so the Axis-legacy instrument stays reproducible.
    NOT the ICICI baseline; use load_client_prompt()."""
    with open(GENERIC_PROMPT_PATH, encoding="utf-8") as fh:
        p = fh.read()
    sha = hashlib.sha256(p.encode()).hexdigest()
    assert sha == GENERIC_PROMPT_SHA256, f"generic prompt sha drifted: {sha}"
    return p


def load_gt_prompt():
    """The shared Opus-5 reference instrument -- gt298_lib.GT_PROMPT, unchanged, so the
    GT is byte-identical across the icici / hdfc / sbi workers."""
    sha = hashlib.sha256(G.GT_PROMPT.encode()).hexdigest()
    assert sha == GT_PROMPT_SHA256, f"GT prompt sha drifted: {sha}"
    return G.GT_PROMPT


def load_refined_prompt():
    with open(REFINED_PROMPT_PATH, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------- corpus
# ICICI filenames come in three shapes:
#   decrypt_<digits>_<hash>_<card>_<n>_<product>.pdf                 (298)
#   decrypt_<digits>_<hash>_<card with spaces>-<n>.pdf               (2)
#   decrypt_gmail:<n>:<hash>_<hash>_<card>_<n>_<product>.pdf         (4)
# The token after `decrypt_` is the statement id. It is sanitised for use as a
# filename but the mapping is recorded so it stays reversible.
_TOK_RE = re.compile(r"^decrypt_(?:encrypt_)?(.+?)_[0-9a-f]{12,}_", re.I)
_NUM_RE = re.compile(r"^decrypt_(?:encrypt_)?(\d+)_")


def _sid_from_name(fname):
    m = _TOK_RE.match(fname)
    if not m:
        m = _NUM_RE.match(fname)
    if not m:
        return None
    return re.sub(r"[^0-9A-Za-z]+", "-", m.group(1))


def _product_from_name(fname):
    """The card product encoded in the filename tail. Used only to make the Phase-1
    sample structurally diverse; never used as a label."""
    stem = fname[:-4] if fname.lower().endswith(".pdf") else fname
    parts = stem.split("_")
    idx = [i for i, t in enumerate(parts) if "XXXX" in t]
    if not idx:
        return "UNKNOWN"
    tail = "_".join(parts[idx[0] + 2:])
    return tail or "UNLABELLED"


def discover_pdfs():
    """-> [(sid, filename, path)] sorted by filename. Raises on an id collision or an
    unparseable name rather than dropping the statement."""
    out, odd = [], []
    for f in sorted(os.listdir(PDF_DIR)):
        if not f.lower().endswith(".pdf"):
            continue
        sid = _sid_from_name(f)
        if not sid:
            odd.append(f)
            continue
        out.append((sid, f, os.path.join(PDF_DIR, f)))
    if odd:
        raise RuntimeError(f"{len(odd)} PDF(s) unparseable: {odd}")
    ids = [t[0] for t in out]
    if len(set(ids)) != len(ids):
        from collections import Counter
        raise RuntimeError(f"duplicate sids: {[k for k, v in Counter(ids).items() if v > 1]}")
    return out


# ---------------------------------------------------------------- incumbent CSV


def load_csv_incumbent():
    """filename -> {"blob": nested `data` JSON, "cols": top-level columns}.

    JOIN KEY, verified: basename(unquote(urlparse(link).path)) == PDF filename, for
    304/304 PDFs, with no PDF matched by two CSV rows. The nested `data` blob is the
    transaction-level and card-level source; top-level columns are a lossy
    (comma-grouped, integer-truncated) projection kept for cross-checking only.
    """
    import csv as _csv
    _csv.field_size_limit(10 ** 9)
    by_name, dups, unmatched = {}, [], []
    pdf_names = {f for _, f, _ in discover_pdfs()}
    rows = 0
    with open(CSV_PATH, encoding="utf-8") as fh:
        for r in _csv.DictReader(fh):
            rows += 1
            name = os.path.basename(urllib.parse.unquote(
                urllib.parse.urlparse(r.get("link") or "").path))
            if name not in pdf_names:
                unmatched.append(name)
                continue
            if name in by_name:
                dups.append(name)
                continue
            try:
                blob = json.loads(r["data"]) if (r.get("data") or "").strip() else None
            except Exception:
                blob = None
            by_name[name] = {"blob": blob, "cols": dict(r), "data_parse_ok": blob is not None}
    return by_name, {"csv_rows": rows, "matched": len(by_name),
                     "unmatched_csv_rows": unmatched, "duplicate_joins": dups}


# ---------------------------------------------------------------- payloads


def build_luna_payload(pdf_path, pdf_b64, prompt):
    """The proven Luna native-PDF shape. The Anthropic `document` block is a hard 400
    on this endpoint; `reasoning_effort` is Luna's effort param."""
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


def build_opus_payload(pdf_b64, prompt):
    """Anthropic document block + thinking/adaptive + output_config.effort.
    `reasoning_effort` is a hard 400 here. Opus 5 returned ZERO reasoning tokens at
    every effort level in prior probes -- this is NOT a high-reasoning pass."""
    return {
        "messages": [{
            "role": "user",
            "content": [
                {"type": "document", "source": {
                    "type": "base64", "media_type": "application/pdf", "data": pdf_b64}},
                {"type": "text", "text": prompt},
            ],
        }],
        "max_tokens": OPUS_MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": OPUS_EFFORT},
        "response_format": RESPONSE_FORMAT,
    }


def pdf_b64(path):
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode()


# ---------------------------------------------------------------- outcome classes


def classify(finish_reason, resp, meta, raw_text, parsed):
    """Infrastructure and model defects kept strictly apart, so a 429 can never be
    reported as 'the model failed to extract'."""
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

    fr = finish_reason
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


# ---------------------------------------------------------------- runner


def run_one(sid, fname, path, outdir, arm, prompt, force=False):
    """One statement -> one atomically-persisted record. Idempotent: an existing
    terminal record is returned untouched so a crash costs zero completed work.

    `usage` is persisted VERBATIM (`usage_raw`) -- provider usage semantics differ and
    a field discarded at write time cannot be recovered without re-running.
    """
    dest = os.path.join(outdir, "json", f"{sid}.json")
    if os.path.exists(dest) and not force:
        try:
            prev = json.loads(open(dest).read())
            if prev.get("outcome") and prev.get("failure_class") in (None, "cap", "model"):
                return prev
        except Exception:
            pass

    is_luna = arm.startswith("luna")
    model = LUNA_MODEL if is_luna else OPUS_MODEL
    b64 = pdf_b64(path)
    payload = (build_luna_payload(path, b64, prompt) if is_luna
               else build_opus_payload(b64, prompt))

    rec = {
        "statement_id": sid, "pdf": fname, "arm": arm, "model": model,
        "input_path": "native_pdf_file_block" if is_luna else "native_pdf_document_block",
        "call_type": "single_full_schema",
        "max_tokens": LUNA_MAX_TOKENS if is_luna else OPUS_MAX_TOKENS,
        "effort_param": ("reasoning_effort=" + LUNA_EFFORT) if is_luna
                        else ("output_config.effort=" + OPUS_EFFORT),
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "prompt_chars": len(prompt),
        "pdf_bytes": os.path.getsize(path),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    resp, meta = G.invoke(payload, model=model, max_attempts=10, timeout=1800)
    rec["meta"] = meta

    raw_text, parsed, fr = "", None, None
    if resp is not None:
        choice = (resp.get("choices") or [{}])[0]
        fr = choice.get("finish_reason")
        rec["finish_reason"] = fr
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

    rec["parsed_json"] = parsed
    rec["outcome"], rec["failure_class"] = classify(fr, resp, meta, raw_text, parsed)
    if isinstance(parsed, dict) and isinstance(parsed.get("transactions"), list):
        rec["n_transactions"] = len(parsed["transactions"])
        rec["n_cards"] = len(parsed.get("cards") or [])
    rec["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")

    G.atomic_write_json(dest, rec, indent=1, sort_keys=True)
    return rec
