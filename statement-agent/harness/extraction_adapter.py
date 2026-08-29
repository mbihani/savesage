"""Workstream-2 Luna extraction adapter.

Invokes ``databricks-gpt-5-6-luna`` at the workspace serving endpoint using the
OpenAI ``file`` content block (the Anthropic ``document`` block is a hard 400 on
this endpoint -- see ``harness/transports.py`` for the two separate builders).

Design notes
------------
* **Auth** is delegated to :func:`harness.auth.acquire_token`, which prefers
  ``DATABRICKS_TOKEN`` and falls back to the Databricks SDK. OAuth tokens expire
  (~1h), so a token is acquired *per request* rather than cached for the process
  lifetime -- a long-running graph that reuses a stale token would 401 mid-batch.
* **Transport** is stdlib ``urllib.request`` (pypi is blackholed on this
  machine, so ``requests``/``httpx`` cannot be installed locally). The retry
  policy is :class:`harness.policy.RetryPolicy`; it is the SINGLE source of both
  retry behaviour and the request timeout (``timeout_seconds``) -- no second
  timeout or retry mechanism exists.
* **Response mapping** (:func:`map_response`) is pure and stdlib-testable: it
  pulls the model's text out of the OpenAI chat-completions shape, parses the JSON
  (tolerating a ```json fenced block), and maps usage/id into an
  :class:`ExtractionResult`. Schema conformance is NOT checked here -- the
  validation node owns that.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

from config import get_settings
from contracts.models import ExtractionResult, ParseRequest, TokenUsage
from contracts.ports import ExtractionAdapter
from harness.auth import acquire_token
from harness.policy import RetryPolicy
from harness.transports import extraction_payload
from rules.routing import PROMPT_BY_BANK, load_schema_for_bank

# Re-exported so the wiring layer can build a default policy without importing
# harness.policy separately (keeps the adapter the single integration point).
DefaultRetryPolicy = RetryPolicy

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class ExtractionError(RuntimeError):
    """Raised when the endpoint cannot be reached after all retries."""


def _extract_text(resp: dict[str, Any]) -> str:
    """Pull the assistant text out of an OpenAI chat-completions response.

    ``content`` may be a plain string or a list of typed blocks; Luna returns a
    list whose first text block is the answer. Mirrors the proven pattern in the
    repo's ``gt298_lib.extract_text``.
    """
    choices = resp.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return "" if content is None else str(content)


def parse_json_strict(text: str) -> dict[str, Any]:
    """Parse the model's text as JSON, tolerating a ```json fence."""
    t = text.strip()
    if t.startswith("```"):
        t = _FENCE.sub("", t).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i >= 0 and j > i:
            return json.loads(t[i:j + 1])
        raise


def _check_completion(resp: dict[str, Any]) -> None:
    """Raise :class:`ExtractionError` on truncation, refusal, or a malformed response.

    A response whose content is still parseable JSON but whose first choice has
    ``finish_reason: "length"`` was clipped at ``max_tokens`` -- statements with
    long transaction lists are exactly where this happens, and the clipped JSON
    would silently drop transactions and then be judged as an extraction error.
    ``finish_reason: "content_filter"`` and a non-empty ``message.refusal`` are
    refusals. For a SYNCHRONOUS invocation of this endpoint there is no legitimate
    reason for ``finish_reason`` to be absent or None, so only an exact ``"stop"``
    is a clean completion -- anything else is treated as a malformed/incomplete
    response and raised as an :class:`ExtractionError`.
    """
    choices = resp.get("choices") or []
    if not choices:
        raise ExtractionError("response has no choices")
    choice = choices[0]
    finish_reason = choice.get("finish_reason")
    if finish_reason != "stop":
        raise ExtractionError(
            f"model did not finish cleanly: finish_reason={finish_reason!r} "
            f"(expected 'stop'; truncation, content filter, or malformed response -- "
            f"output may be incomplete)"
        )
    message = choice.get("message") or {}
    refusal = message.get("refusal")
    if refusal:
        raise ExtractionError(f"model refused to respond: {str(refusal)[:200]}")


def map_response(resp: dict[str, Any], request: ParseRequest, latency_ms: float) -> ExtractionResult:
    """Map a Luna chat-completions response to an :class:`ExtractionResult`.

    ``schema_valid`` is left ``False`` here; the validation node sets it once the
    declarative rules + JSON-Schema conformance have been checked. A response
    with no parseable JSON, a truncated completion (``finish_reason: "length"``),
    or a refusal raises :class:`ExtractionError` -- the graph records that as an
    extraction failure rather than persisting a clipped or empty payload.
    """
    _check_completion(resp)
    raw_text = _extract_text(resp)
    if not raw_text:
        raise ExtractionError("model returned empty content")
    try:
        payload = parse_json_strict(raw_text)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"model output is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        # A top-level array, string, number, bool, or null is valid JSON but not a
        # statement object; letting it through would crash later (e.g. _txn_count
        # calls .get on a list). Defence in depth: reject here AND type-check
        # defensively in the summary helpers.
        raise ExtractionError(
            f"model output is not a JSON object: got {type(payload).__name__}"
        )
    usage = _map_usage(resp.get("usage"))
    return ExtractionResult(
        request_id=request.request_id,
        payload=payload,
        model_id=str(resp.get("model") or ""),
        latency_ms=latency_ms,
        token_usage=usage,
        raw_response_id=str(resp.get("id")) if resp.get("id") is not None else None,
        schema_valid=False,
    )


def _map_usage(usage: dict[str, Any] | None) -> TokenUsage:
    if not usage:
        return TokenUsage()
    return TokenUsage(
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
        total_tokens=usage.get("total_tokens"),
    )


def _read_pdf(request: ParseRequest) -> bytes:
    source = request.pdf
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    # Path-like
    return source.read_bytes()  # type: ignore[union-attr]


class LunaExtractionAdapter(ExtractionAdapter):
    """Concrete Luna extraction adapter; stdlib transport, per-request auth.

    When ``prompt_override`` / ``schema_override`` are provided (the
    ``/api/parse-custom`` path), they are used INSTEAD of the bank defaults —
    ``resolve_prompt`` and ``load_schema_for_bank`` are not called. This lets
    the user experiment with custom prompts/schemas without persisting them.
    """

    def __init__(
        self,
        retry_policy: RetryPolicy | None = None,
        settings=None,
        token_provider=acquire_token,
        urlopen=urllib.request.urlopen,
        prompt_override: str | None = None,
        schema_override: dict[str, Any] | None = None,
    ) -> None:
        self._policy = retry_policy or RetryPolicy()
        self._settings = settings  # lazily fetched in extract() if None
        self._token_provider = token_provider
        self._urlopen = urlopen
        self._prompt_override = prompt_override
        self._schema_override = schema_override

    def _settings_obj(self):
        if self._settings is None:
            self._settings = get_settings()
        return self._settings

    def _build_request(self, request: ParseRequest, prompt: str, schema: dict[str, Any]) -> urllib.request.Request:
        settings = self._settings_obj()
        url = settings.endpoint_url(settings.extraction_endpoint)
        pdf = _read_pdf(request)
        body = json.dumps(extraction_payload(pdf, request.filename, prompt, schema)).encode()
        token = self._token_provider()
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        return req

    def extract(self, request: ParseRequest) -> ExtractionResult:
        """Invoke Luna with the bank's prompt and the bank's per-bank schema.

        Uses :func:`graph.routing.resolve_prompt` for the prompt and
        :func:`rules.routing.load_schema_for_bank` for the schema (both keyed on
        the request's detected bank, mirroring each other), unless
        ``prompt_override`` / ``schema_override`` were provided at construction
        (the ``/api/parse-custom`` path), in which case those are used directly.
        The function-local prompt import keeps this module importable in
        isolation tests that monkeypatch the prompt. The retry policy's
        ``max_attempts`` bounds the call; the ``retry_statuses`` set decides
        what is retried. The timeout is the policy's ``timeout_seconds``
        (single source -- no settings timeout).
        """
        if self._prompt_override is not None:
            prompt = self._prompt_override
        else:
            from graph.routing import resolve_prompt  # function-local; see docstring
            prompt = resolve_prompt(request.bank)
        schema = self._schema_override if self._schema_override is not None else load_schema_for_bank(request.bank)
        req = self._build_request(request, prompt, schema)
        timeout = self._policy.timeout_seconds

        last_error = ""
        t0 = time.perf_counter()
        for attempt in range(1, self._policy.max_attempts + 1):
            try:
                with self._urlopen(req, timeout=timeout) as r:
                    raw = r.read().decode()
                resp = json.loads(raw)
                latency_ms = (time.perf_counter() - t0) * 1000.0
                return map_response(resp, request, latency_ms)
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}: {self._read_err(exc)[:500]}"
                if exc.code in self._policy.retry_statuses and attempt < self._policy.max_attempts:
                    time.sleep(self._policy.backoff_for_attempt(attempt))
                    # re-acquire token on auth failures; tokens expire ~1h
                    if exc.code in (401, 403):
                        token = self._token_provider()
                        req.add_header("Authorization", f"Bearer {token}")
                    continue
                break
            except Exception as exc:  # timeout / socket reset
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self._policy.max_attempts:
                    time.sleep(self._policy.backoff_for_attempt(attempt))
                    continue
                break
        raise ExtractionError(
            f"extraction failed for {request.request_id} after "
            f"{self._policy.max_attempts} attempts: {last_error}"
        )

    @staticmethod
    def _read_err(exc: urllib.error.HTTPError) -> str:
        try:
            body = exc.read().decode()
        except Exception:
            return str(exc)
        finally:
            # Close the underlying response so test/mocked file handles are released.
            close = getattr(exc, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        return body


# Kept for parity with the wiring helper; the prompt + schema maps are the
# source of truth in rules.routing but re-exporting here avoids a second import
# in callers.
_ = PROMPT_BY_BANK
_ = load_schema_for_bank
