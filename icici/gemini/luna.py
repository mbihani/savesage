"""Self-contained Luna 5.6 transport for the ICICI arms.

Deliberately self-contained: sbi/ owns its own vendored copy of this plumbing and this
task must not edit hdfc/ or sbi/, nor depend on an out-of-repo path.

Operational facts baked in here, all learned the hard way on this project:
  * The binding workspace limit is OUTPUT TOKENS PER MINUTE, WORKSPACE-WIDE -- not QPS.
    A prior run hit 429s at concurrency 14. Hence whole-MINUTE backoff, not seconds.
  * 429 and IP-ACL 403 are INFRASTRUCTURE, never a model failure. A throttled call
    scored as "the model could not extract" would defame the model.
  * usage_raw is persisted VERBATIM. Luna reports reasoning tokens INSIDE
    completion_tokens (OpenAI convention; Gemini is the opposite, and getting that
    backwards was a ~2x error earlier on this project), so the identity
    prompt + completion == total is ASSERTED rather than assumed.
  * The CLI at /usr/local/bin/databricks is used by FULL PATH: a v0.18 CLI earlier on
    $PATH prints a banner that corrupts stdout parsing.
"""

import base64
import json
import os
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

CLI = "/usr/local/bin/databricks"          # full path on purpose -- see docstring
PROFILE = os.environ.get("DATABRICKS_CONFIG_PROFILE", "fevm-stable")
MODEL = "databricks-gpt-5-6-luna"

_tok_lock = threading.Lock()
_tok = {"value": None, "exp": 0.0}
_host = {"value": None}


def _run(args):
    p = subprocess.run(args, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed rc={p.returncode}: {p.stderr[:400]}")
    return p.stdout


def host():
    if _host["value"]:
        return _host["value"]
    env = json.loads(_run([CLI, "auth", "env", "--profile", PROFILE]))["env"]
    _host["value"] = env["DATABRICKS_HOST"].rstrip("/")
    return _host["value"]


def token():
    """Fresh bearer token, refreshed a minute before expiry (tokens are ~1h)."""
    with _tok_lock:
        if _tok["value"] and time.time() < _tok["exp"] - 60:
            return _tok["value"]
        d = json.loads(_run([CLI, "auth", "token", "--profile", PROFILE]))
        _tok["value"] = d["access_token"]
        _tok["exp"] = time.time() + float(d.get("expires_in") or 3600)
        return _tok["value"]


def pdf_b64(path):
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode()


def is_ip_acl(status, body):
    if status != 403:
        return False
    b = (body or "").lower()
    return any(k in b for k in ("ip access list", "ip acl", "not allowed to access",
                                "denied by ip"))


def invoke(payload, model=MODEL, max_attempts=8, timeout=1800):
    """POST to the serving endpoint. Returns (response_json_or_None, meta).

    meta records infrastructure truth: http_status, attempts, rate_limited, ip_acl,
    latency_ms, error. Never conflates infrastructure with model behaviour.
    """
    url = f"{host()}/serving-endpoints/{model}/invocations"
    body = json.dumps(payload).encode()
    meta = {"attempts": 0, "rate_limited": False, "ip_acl": False,
            "http_status": None, "latency_ms": None, "error": None}
    t0 = time.time()
    for attempt in range(1, max_attempts + 1):
        meta["attempts"] = attempt
        req = urllib.request.Request(
            url, data=body,
            headers={"Authorization": f"Bearer {token()}",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                meta["http_status"] = r.status
                meta["latency_ms"] = int((time.time() - t0) * 1000)
                return json.loads(r.read().decode()), meta
        except urllib.error.HTTPError as e:
            raw = e.read().decode(errors="replace")
            meta["http_status"] = e.code
            meta["error"] = raw[:600]
            if e.code == 429:
                meta["rate_limited"] = True
                # WHOLE-MINUTE backoff: the quota is per-minute output tokens.
                time.sleep(60 * attempt + 5)
                continue
            if is_ip_acl(e.code, raw):
                meta["ip_acl"] = True
                break
            if e.code in (500, 502, 503, 504):
                time.sleep(min(30, 5 * attempt))
                continue
            break
        except Exception as e:                                  # network/timeout
            meta["error"] = f"{type(e).__name__}: {e}"
            time.sleep(min(30, 5 * attempt))
            continue
    meta["latency_ms"] = int((time.time() - t0) * 1000)
    return None, meta


def extract_text(resp):
    ch = (resp.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):                                    # content-block form
        return "".join(b.get("text", "") for b in c if isinstance(b, dict))
    return ""


def parse_json_strict(text):
    """Parse the model's JSON. Tolerates a ```json fence but never repairs content."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return json.loads(t)


def assert_token_identity(usage):
    """prompt + completion == total. Luna counts reasoning INSIDE completion.

    Returns (ok, detail). Never raises: a usage anomaly must be RECORDED, not crash a
    sweep that has already paid for the call.
    """
    if not isinstance(usage, dict):
        return False, "usage missing"
    p, c, t = (usage.get("prompt_tokens"), usage.get("completion_tokens"),
               usage.get("total_tokens"))
    if None in (p, c, t):
        return False, f"incomplete usage p={p} c={c} t={t}"
    if p + c != t:
        return False, f"IDENTITY VIOLATED: {p} + {c} = {p+c} != total {t}"
    reason = ((usage.get("completion_tokens_details") or {}).get("reasoning_tokens"))
    if reason is not None and reason > c:
        return False, f"reasoning_tokens {reason} > completion_tokens {c}"
    return True, f"ok p={p} c={c} t={t} reasoning={reason}"


def atomic_write_json(path, obj, indent=1, sort_keys=True):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(obj, fh, indent=indent, sort_keys=sort_keys, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
