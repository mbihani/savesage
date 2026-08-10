"""HDFC native-PDF evaluation: corpus + join + Luna/Opus plumbing.

Reuses gt298_lib for everything that is bank-agnostic (auth, token refresh, 429
backoff in whole minutes, IP-ACL classification, atomic writes, content/JSON
extraction, GT_PROMPT + GT_SCHEMA, the Anthropic-shape Opus payload builder) and
adds only what is HDFC-specific: the corpus root, the URL-decoded join key, and
the Luna OpenAI-`file`-shape payload builder parameterised by prompt.

Two measured deviations from the brief, both verified (see HDFC_REPORT.md):
  * 281 PDFs, not 282 -- the 282nd directory entry is failed-download-links.txt.
  * The join reaches 281/300 CSV rows, not 271, once the basename is URL-DECODED.
    9 HDFC filenames are stored on disk with literal spaces ("HDFC Bank Pixel Play
    Credit Card Statement  - 03Mar2026 to 02Apr2026.pdf") while the CSV `link`
    percent-encodes them (%20), and 1 more differs only by that. The 19 CSV rows
    that still do not match are byte-identical to failed-download-links.txt, i.e.
    PDFs that were never downloaded -- a collection gap, not a join defect.
"""

import ast
import json
import os
import re
import sys
import time
import urllib.parse

sys.path.insert(0, "/Users/mayanck.bihani/Savesage/apev-wt-gt298/groundtruth298")
import gt298_lib as G  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PDF_ROOT = "/Users/mayanck.bihani/Downloads/hdfc-pdfs"
CSV_PATH = "/Users/mayanck.bihani/Downloads/remaining pdfs ground truth/hdfc.csv"

LUNA_MODEL = "databricks-gpt-5-6-luna"
GT_MODEL = "databricks-claude-opus-5"

LUNA_MAX_TOKENS = 96000
LUNA_EFFORT = "medium"
# RAISED 32000 -> 64000 mid-run, for the HDFC tail only. HDFC is the densest of the
# three corpora (16.5 txn/stmt mean, max 223) and the 32000 inherited from the Axis
# pass left too little margin: fitting the 69 completed GT calls gives
# completion_tokens ~= 96.3 * n_txn + 573, and the worst per-txn ratio observed on
# large statements (n>=30) is 123.3 tok/txn, which projects ~27.5k for the 223-txn
# statement -- inside 32000 by only 14%. A truncated GT record silently PENALISES the
# challenger, so the cheap fix is more headroom.
#
# This does NOT invalidate the 69 records already collected at 32000: max_tokens is a
# ceiling, not a sampling parameter, and all 69 finished with finish_reason='stop' at
# a maximum of 7,058 completion tokens -- none came near either cap, so none could
# have been shaped by it. Audited explicitly in HDFC_REPORT.md.
GT_MAX_TOKENS = 64000

# THE baseline is the CLIENT'S OWN production prompt. It is NOT luna_prompt/
# LUNA_PROMPT.txt -- that file is ground-truth-flavoured ("this output will be used
# to score other models") and is the wrong instrument for a production baseline.
# A prior session in this directory used LUNA_PROMPT.txt by mistake; that run is
# retained under prior_session_wrong_baseline/ as evidence, not reused.
BASELINE_PROMPT_PATH = "/Users/mayanck.bihani/Savesage/SYSTEM PROMPT.txt"
HDFC_PROMPT_PATH = os.path.join(HERE, "HDFC_PROMPT.txt")

RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "credit_card_statement", "strict": True, "schema": G.GT_SCHEMA},
}

# ---------------------------------------------------------------- corpus / join
# HDFC ids are more varied than Axis's: `decrypt_<num>_<hash>_...`,
# `decrypt_encrypt_<num>_...`, `decrypt_gmail:36703:<hash>_...`, and
# `decrypt_<num>_<yyyymmdd>_<hhmmss>_<n>_...`. The statement id is therefore NOT
# reliably parseable from the filename, so the FILENAME itself is the corpus key
# and the CSV `id` column is carried alongside it. Deriving an id by regex here
# would silently collapse the gmail:-prefixed files.
_SAFE = re.compile(r"[^A-Za-z0-9]+")


def sid_for(filename):
    """A filesystem-safe, collision-free record id derived from the PDF filename."""
    stem = filename[:-4] if filename.lower().endswith(".pdf") else filename
    return _SAFE.sub("_", stem).strip("_")


def discover_pdfs():
    """-> sorted list of (sid, filename, path). Non-PDF directory entries excluded."""
    out = []
    for f in sorted(os.listdir(PDF_ROOT)):
        if not f.lower().endswith(".pdf"):
            continue
        out.append((sid_for(f), f, os.path.join(PDF_ROOT, f)))
    sids = [t[0] for t in out]
    if len(set(sids)) != len(sids):
        from collections import Counter
        raise RuntimeError(f"sid collision: {[k for k, v in Counter(sids).items() if v > 1]}")
    return out


def csv_rows():
    import csv as _csv
    _csv.field_size_limit(10 ** 9)
    with open(CSV_PATH, newline="") as fh:
        return list(_csv.DictReader(fh))


def join_key(link):
    """CSV `link` -> PDF filename. URL-DECODING is load-bearing: 10 HDFC files are
    stored with literal spaces while the CSV percent-encodes them."""
    return urllib.parse.unquote(os.path.basename((link or "").split("?")[0]))


def build_join():
    """-> (matched, unmatched_csv, pdfs_without_csv).

    matched: list of dicts {sid, filename, path, csv_row}. 1:1 -- verified no PDF
    maps to two CSV rows and CSV `id` is unique.
    """
    pdfs = {f: (s, p) for s, f, p in discover_pdfs()}
    matched, unmatched = [], []
    seen = set()
    for r in csv_rows():
        fn = join_key(r.get("link"))
        if fn in pdfs:
            sid, path = pdfs[fn]
            if sid in seen:
                raise RuntimeError(f"two CSV rows map to one PDF: {fn}")
            seen.add(sid)
            matched.append({"sid": sid, "filename": fn, "path": path, "csv_row": r})
        else:
            unmatched.append(r)
    matched.sort(key=lambda m: m["sid"])
    return matched, unmatched, sorted(set(pdfs) - {m["filename"] for m in matched})


# ---------------------------------------------------------------- payloads

def baseline_prompt():
    """The INNER STRING of `SYSTEM_PROMPT = \"\"\"...\"\"\"` in the client's prompt file.

    That file is a Python SOURCE file, not raw prompt text, so the assignment
    wrapper must be stripped or the model would receive `SYSTEM_PROMPT = \"\"\"` as
    part of its instructions. It is stripped by AST parse rather than by regex or
    slicing: ast.literal_eval yields exactly the string Python itself would build,
    so an embedded quote sequence cannot silently corrupt the extraction.

    The prompt embeds NO JSON schema despite saying "strictly matching the provided
    schema" -- the schema is supplied out-of-band via response_format (LUNA_SCHEMA,
    verified byte-identical to gt298_lib.GT_SCHEMA and held constant across banks).
    """
    src = open(BASELINE_PROMPT_PATH, encoding="utf-8").read()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SYSTEM_PROMPT" for t in node.targets):
            val = ast.literal_eval(node.value)
            if not isinstance(val, str):
                raise RuntimeError("SYSTEM_PROMPT is not a string literal")
            return val
    raise RuntimeError(f"SYSTEM_PROMPT assignment not found in {BASELINE_PROMPT_PATH}")


def load_prompt(which):
    """'generic' -> the client's production baseline; 'hdfc' -> the refined prompt."""
    if which == "generic":
        return baseline_prompt()
    if which == "hdfc":
        with open(HDFC_PROMPT_PATH, encoding="utf-8") as fh:
            return fh.read()
    raise ValueError(which)


def build_luna_payload(filename, pdf_b64, prompt):
    """The proven Luna native-PDF shape. Luna rejects the Anthropic `document`
    block with a hard 400; the OpenAI `file` block + data URL is the only path.
    `reasoning_effort` is Luna's effort param (a hard 400 on Claude endpoints)."""
    return {
        "messages": [{
            "role": "user",
            "content": [
                {"type": "file", "file": {
                    "filename": filename,
                    "file_data": "data:application/pdf;base64," + pdf_b64,
                }},
                {"type": "text", "text": prompt},
            ],
        }],
        "max_tokens": LUNA_MAX_TOKENS,
        "response_format": RESPONSE_FORMAT,
        "reasoning_effort": LUNA_EFFORT,
    }


def build_gt_payload(pdf_b64):
    """Opus 5: Anthropic `document` block + thinking/adaptive + output_config.effort.
    `reasoning_effort` is a hard 400 here. GT_PROMPT/GT_SCHEMA are UNCHANGED -- they
    are the shared reference instrument across all three banks."""
    return G.build_payload(pdf_b64, effort="medium", prompt=G.GT_PROMPT,
                           response_format=RESPONSE_FORMAT, max_tokens=GT_MAX_TOKENS)


# ---------------------------------------------------------------- outcome classes

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


def _coerce_escaped_transactions(parsed):
    """Opus emitted `transactions` as an escaped JSON STRING 7x on the Axis corpus.
    Recorded as its own outcome class AND repaired for scoring, so the defect stays
    visible in the tally without discarding the extraction."""
    if isinstance(parsed, dict) and isinstance(parsed.get("transactions"), str):
        try:
            tx = json.loads(parsed["transactions"])
            if isinstance(tx, list):
                out = dict(parsed)
                out["transactions"] = tx
                return out
        except Exception:
            pass
    return parsed


def run_one(which_model, sid, filename, pdf_path, outdir, prompt=None, force=False):
    """One statement -> one atomically-persisted record. Idempotent resume: an
    existing terminal record is returned untouched so a crash costs zero work."""
    dest = os.path.join(outdir, "json", f"{sid}.json")
    if os.path.exists(dest) and not force:
        try:
            prev = json.loads(open(dest).read())
            if prev.get("outcome") and prev.get("failure_class") in (None, "cap", "model"):
                return prev
        except Exception:
            pass

    b64 = G.pdf_b64(pdf_path)
    if which_model == "luna":
        model, payload = LUNA_MODEL, build_luna_payload(filename, b64, prompt)
        extra = {"max_tokens": LUNA_MAX_TOKENS, "reasoning_effort": LUNA_EFFORT}
    else:
        model, payload = GT_MODEL, build_gt_payload(b64)
        extra = {"max_tokens": GT_MAX_TOKENS, "output_config_effort": "medium",
                 "thinking": "adaptive"}

    rec = {"sid": sid, "model": model, "pdf": filename,
           "input_path": "native_pdf", "call_type": "single_full_schema",
           "prompt_sha256": G_sha(prompt) if prompt else G_sha(G.GT_PROMPT),
           "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), **extra}

    resp, meta = G.invoke(payload, model=model, max_attempts=10, timeout=1200)
    rec["meta"] = meta

    raw_text, parsed = "", None
    if resp is not None:
        choice = (resp.get("choices") or [{}])[0]
        rec["finish_reason"] = choice.get("finish_reason")
        # PERSIST USAGE VERBATIM: provider usage semantics differ and a field
        # discarded at write time cannot be recovered without re-running.
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

    rec["outcome"], rec["failure_class"] = classify(rec, resp, meta, raw_text, parsed)
    rec["parsed_json"] = _coerce_escaped_transactions(parsed)
    pj = rec["parsed_json"]
    if isinstance(pj, dict) and isinstance(pj.get("transactions"), list):
        rec["n_transactions"] = len(pj["transactions"])
        rec["n_cards"] = len(pj.get("cards") or [])

    G.atomic_write_json(dest, rec, indent=1, sort_keys=True)
    return rec


def G_sha(text):
    import hashlib
    return hashlib.sha256((text or "").encode()).hexdigest()
