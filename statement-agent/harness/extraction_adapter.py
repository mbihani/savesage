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
import logging
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

_LOGGER = logging.getLogger("statement-agent.extraction")

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class ExtractionError(RuntimeError):
    """Raised when the endpoint cannot be reached after all retries."""


class ExtractionCancelled(ExtractionError):
    """Raised when a parse is cancelled (an endpoint gave up) mid-extraction.

    A subclass of :class:`ExtractionError` so existing callers that treat
    extraction failure as terminal keep working, while ``_run_parse`` can tell a
    cancellation apart from a real endpoint failure if it needs to.
    """


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
        cancel_event: Any = None,
        token_timeout_seconds: float = 30.0,
    ) -> None:
        # Store the caller-supplied policy (may be None). When None, the policy
        # is derived LAZILY from config.Settings in ``_policy_obj`` so
        # REQUEST_TIMEOUT_SECONDS / MAX_ATTEMPTS actually govern the retry
        # ladder. An explicitly-passed policy is used verbatim (tests, and any
        # caller that wants full control — it must NOT be overridden by config).
        self._retry_policy = retry_policy
        self._policy_cache: RetryPolicy | None = retry_policy
        self._settings = settings  # lazily fetched in extract() if None
        self._token_provider = token_provider
        self._urlopen = urlopen
        self._prompt_override = prompt_override
        self._schema_override = schema_override
        # Cooperative-cancellation signal (a ``threading.Event`` or None). Checked
        # before token acquisition, before each attempt, and during each backoff
        # sleep so a zombie worker stops promptly after an endpoint gives up.
        self._cancel_event = cancel_event
        # Own timeout for token acquisition (OAuth/SDK) so a hung credential call
        # cannot stall extraction unbounded, independent of the per-attempt HTTP
        # timeout on the Luna call itself.
        self._token_timeout_seconds = token_timeout_seconds

    def _check_cancelled(self, request_id: str) -> None:
        """Raise :class:`ExtractionCancelled` if this parse was cancelled."""
        if self._cancel_event is not None and self._cancel_event.is_set():
            raise ExtractionCancelled(f"extraction cancelled for {request_id}")

    def _cancellable_sleep(self, seconds: float, request_id: str) -> None:
        """Sleep, but wake early and raise if cancellation fires mid-backoff."""
        if self._cancel_event is not None:
            # Event.wait returns True only if the event is set within the window.
            if self._cancel_event.wait(seconds):
                self._check_cancelled(request_id)  # raises ExtractionCancelled
        else:
            time.sleep(seconds)

    def _acquire_token(self, request_id: str) -> str:
        """Acquire an auth token under a bounded timeout (never unbounded).

        Checks cancellation first, then runs the token provider under
        ``token_timeout_seconds`` so a hung OAuth/SDK credential call is
        abandoned rather than stalling the extraction worker forever.
        """
        self._check_cancelled(request_id)
        from harness.tracing_safe import call_bounded

        t_tok = time.perf_counter()
        # Wrap the result in a tuple so a provider returning a falsy value is
        # distinguishable from call_bounded's None-on-timeout/failure sentinel.
        boxed = call_bounded(
            "extract.token.acquire", self._token_timeout_seconds,
            lambda: ("ok", self._token_provider()),
        )
        if boxed is None:
            raise ExtractionError(
                f"token acquisition timed out or failed after "
                f"{self._token_timeout_seconds:.0f}s for {request_id}"
            )
        _LOGGER.info(
            "extract[%s]: token acquired in %.0fms", request_id,
            (time.perf_counter() - t_tok) * 1000.0,
        )
        return boxed[1]

    def _settings_obj(self):
        if self._settings is None:
            self._settings = get_settings()
        return self._settings

    def _policy_obj(self) -> RetryPolicy:
        """Return the effective retry policy, deriving it from config if unset.

        An explicit policy passed at construction wins. Otherwise the policy is
        built from ``config.Settings`` (``REQUEST_TIMEOUT_SECONDS`` /
        ``MAX_ATTEMPTS``) and cached — previously the adapter used a hardcoded
        ``RetryPolicy()`` default, so those config values were silently ignored.
        """
        if self._policy_cache is None:
            self._policy_cache = RetryPolicy.from_settings(self._settings_obj())
        return self._policy_cache

    def _build_request(self, request: ParseRequest, prompt: str, schema: dict[str, Any]) -> urllib.request.Request:
        settings = self._settings_obj()
        url = settings.endpoint_url(settings.extraction_endpoint)
        pdf = _read_pdf(request)
        body = json.dumps(extraction_payload(pdf, request.filename, prompt, schema)).encode()
        _LOGGER.info("extract[%s]: acquiring SP/token for %s", request.request_id, url)
        token = self._acquire_token(request.request_id)
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
        rid = request.request_id
        # Early-out before any network work if the parse was already cancelled.
        self._check_cancelled(rid)
        if self._prompt_override is not None:
            prompt = self._prompt_override
        else:
            from graph.routing import resolve_prompt  # function-local; see docstring
            prompt = resolve_prompt(request.bank)
        schema = self._schema_override if self._schema_override is not None else load_schema_for_bank(request.bank)
        req = self._build_request(request, prompt, schema)
        policy = self._policy_obj()
        timeout = policy.timeout_seconds

        last_error = ""
        t0 = time.perf_counter()
        for attempt in range(1, policy.max_attempts + 1):
            # Cooperative cancellation: stop before spending another attempt if
            # an endpoint has given up on this parse.
            self._check_cancelled(rid)
            _LOGGER.info(
                "extract[%s]: Luna attempt %d/%d (per-attempt timeout %.0fs)",
                rid, attempt, policy.max_attempts, timeout,
            )
            t_att = time.perf_counter()
            try:
                with self._urlopen(req, timeout=timeout) as r:
                    raw = r.read().decode()
                resp = json.loads(raw)
                latency_ms = (time.perf_counter() - t0) * 1000.0
                _LOGGER.info(
                    "extract[%s]: Luna response received on attempt %d in %.0fms",
                    rid, attempt, (time.perf_counter() - t_att) * 1000.0,
                )
                return map_response(resp, request, latency_ms)
            except urllib.error.HTTPError as exc:
                last_error = f"HTTP {exc.code}: {self._read_err(exc)[:500]}"
                if exc.code in policy.retry_statuses and attempt < policy.max_attempts:
                    backoff = policy.backoff_for_attempt(attempt)
                    _LOGGER.warning(
                        "extract[%s]: attempt %d failed (%s); retrying after %.1fs",
                        rid, attempt, last_error, backoff,
                    )
                    self._cancellable_sleep(backoff, rid)
                    # re-acquire token on auth failures; tokens expire ~1h
                    if exc.code in (401, 403):
                        token = self._acquire_token(rid)
                        req.add_header("Authorization", f"Bearer {token}")
                    continue
                _LOGGER.warning("extract[%s]: attempt %d failed (%s); no retry",
                                rid, attempt, last_error)
                break
            except ExtractionCancelled:
                # Cancellation raised from a cancellable backoff sleep — propagate
                # immediately; it is NOT a retryable transport error.
                raise
            except Exception as exc:  # timeout / socket reset
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < policy.max_attempts:
                    backoff = policy.backoff_for_attempt(attempt)
                    _LOGGER.warning(
                        "extract[%s]: attempt %d errored (%s); retrying after %.1fs",
                        rid, attempt, last_error, backoff,
                    )
                    self._cancellable_sleep(backoff, rid)
                    continue
                _LOGGER.warning("extract[%s]: attempt %d errored (%s); no retry",
                                rid, attempt, last_error)
                break
        raise ExtractionError(
            f"extraction failed for {request.request_id} after "
            f"{policy.max_attempts} attempts: {last_error}"
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
