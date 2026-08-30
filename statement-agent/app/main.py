"""FastAPI app for SaveSage Statement Agent (workstream 6).

Wires the real ports into the LangGraph parse pipeline:

* ``ExtractionAdapter`` → :class:`harness.extraction_adapter.LunaExtractionAdapter`
* ``JudgeAdapter`` → :class:`harness.judge_adapter.OpusJudgeAdapter`
* ``ResultStore`` + ``FeedbackStore`` → :mod:`db.stores` (RDS/psycopg)
* ``TraceSink`` → :class:`harness.tracing.MLflowTraceSink`, wrapped by
  :class:`_ProgressTraceSink` which mirrors per-node trace events into the SSE
  stream.

**Import discipline.**  FastAPI/uvicorn are imported function-local inside
:func:`create_app` so the module is importable in a stdlib-only environment
(the contract-test gate).  All other module-level imports are stdlib-only;
the modules they pull in (``graph.*``, ``harness.*``, ``db.*``) defer their
own third-party imports (langgraph, psycopg, mlflow, databricks-sdk) to
function-local scopes — importing them never requires those packages.
"""

import json
import logging
import os
import queue
import threading
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from contracts.models import (
    FieldComparison,
    FieldFeedback,
    TraceEvent,
)
from contracts.paths import canonical_feedback_path, is_valid_feedback_path
from contracts.ports import TraceSink

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Trace event names emitted by graph nodes → frontend stage labels.
# ``persist_extraction`` is an internal trace name that maps to the
# user-facing ``persist`` stage. The judge no longer runs inline (it is a
# post-hoc evaluation over MLflow traces), so there is no ``judge`` stage.
_STAGE_MAP: dict[str, str] = {
    "route": "route",
    "extract": "extract",
    "validate": "validate",
    "persist_extraction": "persist",
    "finalize": "finalize",
}

# The five pipeline stages the frontend renders, in order. The judge no
# longer runs inline — it is a post-hoc evaluation triggered separately.
PIPELINE_STAGES: tuple[str, ...] = (
    "route", "extract", "validate", "persist", "finalize",
)

# In-memory request contexts, keyed by request_id.  Populated by
# ``POST /api/parse`` and consumed by the SSE + results endpoints.  Entries
# persist until evicted by the FIFO cap below.
_REQUESTS: dict[str, Any] = {}
_MAX_REQUESTS = 50  # FIFO cap — each entry holds ~1-10 MB of PDF bytes

# Module-level caches so we don't create a WorkspaceClient or MLflow sink
# per request.  ``None`` means "not yet attempted"; a tuple/store means
# "attempted" (including the failure sentinel).
_stores: Optional[tuple[Any, Any]] = None
# Last error from an RDS store-init attempt, surfaced via the /health
# endpoint for diagnostics.  ``None`` means "no attempt has failed".
_last_store_error: Optional[str] = None
# Connection parameters most recently resolved from the ``RDS_*`` env vars
# by ``_build_rds_stores``.  Surfaced via the /health endpoint for
# diagnostics; ``None`` until a derivation attempt has produced them (so a
# partial failure still shows whatever was resolved before the error).
_derived_host: Optional[str] = None
_derived_user: Optional[str] = None
_trace_sink: Any = None
_LOGGER = logging.getLogger("statement-agent.app")

# Maximum number of traces the post-hoc judge will score in one evaluation.
# Guards against a caller requesting an expensive sweep via the API.
MAX_SAMPLE_SIZE = 50

# Canonical request_id format — ``req-`` prefix + 12 hex chars (see
# ``_new_request_id``). Used to validate the single-trace judge path param
# BEFORE building an MLflow filter_string from it, so a crafted/quoted value
# cannot alter the filter or select the wrong run.
import re as _re
_REQUEST_ID_RE = _re.compile(r"^req-[0-9a-f]{12}$")


def _is_valid_request_id(request_id: str) -> bool:
    """Return True iff ``request_id`` matches the canonical ``req-<12hex>`` form."""
    return bool(_REQUEST_ID_RE.match(request_id or ""))

# Max seconds to wait for a best-effort persistence/telemetry call (RDS
# or MLflow) inside an ``async`` route handler before giving up and falling
# back to in-memory storage.  Bounds the blocking call so a hung connection
# can never freeze the single uvicorn event loop — which is what made the
# Apps proxy return 502 on feedback submit.  The initial RDS cold-start
# requires psycopg.connect with SSL + DDL (advisory lock + CREATE TABLE IF
# NOT EXISTS x2 + CREATE INDEX + ALTER TABLE x2); 5 s was too tight and
# silently timed out, so all results fell back to in-memory storage and
# never persisted.  15 s gives the cold-start room while still bounding a
# genuinely hung call.
_PERSIST_TIMEOUT = 15.0

# Cache for the most recent judge evaluation result (populated by
# ``POST /api/run-judge`` and returned by ``GET /api/judge-results``).
# Process-scoped: a restart clears it, which is fine for a demo.
_judge_result_cache: Optional[dict[str, Any]] = None

# Guards concurrent judge evaluations — only one background evaluation at a time.
# Shared between the batch sampler (POST /api/run-judge) and the on-demand
# single-trace judge (POST /api/results/{request_id}/judge) so the two never
# run concurrently — a single-trace judge blocks a batch sweep and vice versa.
_judge_running = False
_judge_lock = threading.Lock()


def _acquire_judge_slot() -> bool:
    """Try to acquire the judge concurrency slot (shared batch + single).

    Returns ``True`` if acquired (caller MUST release via
    :func:`_release_judge_slot` when done), ``False`` if a judge run (batch OR
    single) is already in progress.  Atomic under ``_judge_lock``.
    """
    global _judge_running
    with _judge_lock:
        if _judge_running:
            return False
        _judge_running = True
        return True


def _release_judge_slot() -> None:
    """Release the judge concurrency slot."""
    global _judge_running
    with _judge_lock:
        _judge_running = False

# Per-request status for the on-demand single-trace judge, keyed by request_id.
# Populated by POST /api/results/{request_id}/judge and read by the GET status
# endpoint. Entries persist until evicted by the FIFO cap below.
_single_judge_status: dict[str, dict[str, Any]] = {}
_MAX_SINGLE_JUDGE_STATUS = 50


# ---------------------------------------------------------------------------
# RequestContext — thread-safe progress + result holder for one parse request
# ---------------------------------------------------------------------------

class RequestContext:
    """Thread-safe context bridging the background graph thread and the SSE endpoint.

    The background graph thread pushes events into ``events`` (a stdlib
    ``queue.Queue``, thread-safe); the async SSE endpoint reads from it via
    ``run_in_executor``.  After the graph completes, ``extraction_data`` is
    available for the ``GET /api/results`` fallback when RDS is
    unreachable. The judge no longer runs inline, so there is no
    ``verdict_data`` — judge results come from the post-hoc evaluation API.
    """

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        self.events: queue.Queue[Optional[dict[str, Any]]] = queue.Queue()
        self.done = threading.Event()
        self.outcome: Optional[str] = None
        self.error: Optional[str] = None
        self.started_at = datetime.now(UTC)
        # Result snapshots for /api/results (populated after the graph completes).
        self.extraction_data: Optional[dict[str, Any]] = None
        self.complete_data: Optional[dict[str, Any]] = None
        # Original upload retained so the results view can display it alongside
        # the extracted fields for the lifetime of this process.
        self.pdf_bytes: Optional[bytes] = None
        self.pdf_filename: str = "statement.pdf"
        # In-memory feedback list (fallback when RDS feedback store is down).
        self.feedback: list[dict[str, Any]] = []

    def push(self, event_type: str, data: dict[str, Any]) -> None:
        """Push an SSE event (thread-safe; called from the background thread)."""
        self.events.put_nowait({"event": event_type, "data": data})

    def push_sentinel(self) -> None:
        """Signal that no more events will arrive (sentinel = ``None``)."""
        self.events.put_nowait(None)
        self.done.set()


# ---------------------------------------------------------------------------
# Pure, stdlib-only helpers (unit-tested by tests/test_app_ws6.py)
# ---------------------------------------------------------------------------

def _new_request_id() -> str:
    """Generate a unique request ID (``req-`` prefix + 12 hex chars)."""
    return f"req-{uuid.uuid4().hex[:12]}"


def _sse_event(event_type: str, data: Any) -> str:
    """Format one Server-Sent Event frame.

    SSE wire format: ``event: <type>\\ndata: <json>\\n\\n``.
    ``data`` is JSON-serialised with ``default=str`` so datetimes and other
    non-JSON objects degrade gracefully rather than crashing the stream.
    """
    payload = json.dumps(data, default=str)
    return f"event: {event_type}\ndata: {payload}\n\n"


def _comparison_to_dict(c: FieldComparison) -> dict[str, Any]:
    """Serialise a :class:`FieldComparison` to a JSON-safe dict for the API.

    Enum values are flattened to their ``.value`` strings so the frontend
    receives plain JSON (no enum-serialisation knowledge required).

    ``feedback_path`` is the canonical concrete dot-path the frontend
    should use when submitting Accept/Correct feedback for this field.
    For transaction rows, ``actual_row_index`` is preferred, falling
    back to ``expected_row_index`` so unmatched rows (where
    ``actual_row_index`` is ``None``) still get a valid path.
    """
    row_index = c.actual_row_index
    if row_index is None:
        row_index = c.expected_row_index
    feedback_path = None
    try:
        feedback_path = canonical_feedback_path(
            c.field_path,
            row_index=row_index,
            card_index=c.card_index,
        )
    except (ValueError, TypeError):
        pass  # feedback_path stays None — frontend hides Accept/Correct

    return {
        "field_path": c.field_path,
        "feedback_path": feedback_path,
        "expected": c.expected,
        "actual": c.actual,
        "outcome": c.outcome.value,
        "scope": c.scope.value,
        "match_method": c.match_method.value,
        "card_index": c.card_index,
        "expected_row_index": c.expected_row_index,
        "actual_row_index": c.actual_row_index,
        "similarity": c.similarity,
        "rationale": c.rationale,
    }


def _validate_feedback_body(body: dict[str, Any]) -> dict[str, Any]:
    """Validate a feedback request body and return normalised fields.

    Raises :class:`ValueError` with a human-readable message on invalid input.
    Validation includes the canonical-path check via :func:`contracts.paths.
    is_valid_feedback_path` — a path that is not a concrete, indexed canonical
    path (e.g. a template like ``cards[].cardMeta.cardDisplayName`` or a
    JSON-Pointer-style ``/cards/0/...``) is rejected.
    """
    field_path = str(body.get("field_path", ""))
    disposition = str(body.get("disposition", "")).upper()

    if not is_valid_feedback_path(field_path):
        raise ValueError(f"invalid field_path: {field_path!r}")

    if disposition == "ACCEPT":
        accepted = True
    elif disposition == "CORRECT":
        accepted = False
    else:
        raise ValueError("disposition must be ACCEPT or CORRECT")

    original_value = body.get("original_value")
    corrected_value = body.get("corrected_value")

    if not accepted and corrected_value is None:
        raise ValueError("corrected_value is required when disposition is CORRECT")

    return {
        "field_path": field_path,
        "disposition": disposition,
        "accepted": accepted,
        "original_value": original_value,
        "corrected_value": corrected_value,
        "actor": str(body.get("actor", "web-ui")),
    }


def _queue_get(q: queue.Queue, timeout: float) -> Any:
    """Blocking ``Queue.get`` with timeout — helper for ``run_in_executor``."""
    return q.get(block=True, timeout=timeout)


async def _run_blocking(func: Any, *args: Any, timeout: float = _PERSIST_TIMEOUT) -> Any:
    """Run a blocking callable in a worker thread with a bounded wait.

    Returns the callable's result, or ``None`` on exception/timeout.  Used for
    best-effort persistence/telemetry (RDS, MLflow) inside ``async`` route
    handlers so a hung network call can never block the single uvicorn event
    loop — the mechanism behind the proxy 502 on feedback submit.  The
    orphaned thread is not cancelled on timeout (Python cannot kill threads),
    but the handler returns immediately with an in-memory fallback.
    """
    import asyncio

    loop = asyncio.get_running_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(None, func, *args), timeout=timeout,
        )
    except Exception:
        _LOGGER.exception("Blocking persistence/telemetry call failed: %s",
                          getattr(func, "__qualname__", repr(func)))
        return None


def _coerce_schema(schema: Any) -> dict:
    """Normalise a schema body value to a dict, raising :class:`HTTPException`.

    Accepts a JSON object (dict) or a JSON string; anything else is a 400.
    ``HTTPException`` is imported function-local so this module-level helper
    stays importable in a stdlib-only environment (the contract-test gate).
    """
    from fastapi import HTTPException

    if isinstance(schema, str):
        try:
            schema = json.loads(schema)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=400, detail=f"schema is not valid JSON: {exc}"
            ) from exc
    if not isinstance(schema, dict):
        raise HTTPException(
            status_code=400,
            detail=f"schema must be a JSON object, got {type(schema).__name__}",
        )
    return schema


def _validate_v1_pdf(pdf_bytes: bytes) -> None:
    """Validate a PDF upload for the synchronous /api/v1/parse endpoint.

    Pure helper (no FastAPI import) — raises :class:`ValueError` with a
    human-readable message on an invalid upload so the route handler can
    translate it to a 400. A valid PDF is non-empty and starts with the
    ``%PDF`` magic bytes.
    """
    if not pdf_bytes:
        raise ValueError("empty file")
    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("file is not a valid PDF (missing %PDF magic bytes)")


class _UploadTooLarge(Exception):
    """Signal that an upload exceeds the configured max size.

    Carries the limit (MB) and the observed size (MB) so the route handler can
    build a 413 body. The read is abandoned mid-stream, so only ``max_bytes``
    of the body are buffered — not the full (potentially huge) upload.
    """

    def __init__(self, limit_mb: int, observed_mb: float) -> None:
        self.limit_mb = limit_mb
        self.observed_mb = observed_mb
        super().__init__(f"upload exceeds {limit_mb} MB")


async def _read_bounded(file: Any, max_bytes: int) -> bytes:
    """Read an ``UploadFile`` with a hard byte cap (no FastAPI import here).

    Streams in chunks so an oversized upload is detected after reading at most
    ``max_bytes + chunk`` bytes (the chunk that crosses the cap), not the whole
    body. Raises :class:`_UploadTooLarge` when the cap is exceeded; the caller
    translates that to a 413.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)  # 1 MB chunks
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise _UploadTooLarge(
                max_bytes // (1024 * 1024), total / (1024 * 1024),
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _build_v1_response(ctx: RequestContext, request_id: str,
                       bank_name: str) -> tuple[int, dict[str, Any]]:
    """Build the /api/v1/parse JSON response from a completed parse context.

    Pure helper (no FastAPI import) — returns ``(status_code, body)`` so the
    route handler can return the body directly on 200 or wrap it in a
    JSONResponse for non-200. A run is a failure ONLY when the graph
    produced no extraction (``ctx.extraction_data`` is ``None``) or
    short-circuited with the ``EXTRACTION_FAILED`` outcome. A ``PARTIAL``
    outcome (extraction OK, validation flagged) is still a 200 — the payload
    is usable, ``validation_errors`` carries the caveats — even when
    ``ctx.error`` is set (e.g. a validation message), because the extraction
    itself succeeded and the payload is returned. ``ctx.error`` alone never
    flips a usable extraction to a 422.
    """
    failed = (
        ctx.extraction_data is None
        or ctx.outcome == "EXTRACTION_FAILED"
    )
    if failed:
        return 422, {
            "request_id": request_id,
            "bank": bank_name,
            "status": "EXTRACTION_FAILED",
            "extraction": None,
            "error": ctx.error or "extraction produced no result",
            "verdict": None,
        }
    validation_errors = (
        ctx.complete_data.get("validation_errors", [])
        if ctx.complete_data else []
    )
    return 200, {
        "request_id": request_id,
        "bank": bank_name,
        "status": ctx.outcome or "SUCCESS",
        "extraction": {
            "payload": ctx.extraction_data["payload"],
            "model_id": ctx.extraction_data["model_id"],
            "schema_valid": ctx.extraction_data["schema_valid"],
            "validation_errors": validation_errors,
        },
        "verdict": None,
    }


# ---------------------------------------------------------------------------
# Production port wiring (third-party imports deferred to call-time)
# ---------------------------------------------------------------------------

def _build_rds_stores() -> tuple[Any, Any]:
    """Build RDS-backed ``ResultStore`` + ``FeedbackStore``.

    Returns ``(result_store, feedback_store)``.  Raises on failure; callers
    catch and degrade to in-memory.  ``psycopg`` is imported function-local
    inside the dependency modules, so importing this function does not require
    it — only *calling* it does.

    Connection parameters come from the ``RDS_*`` environment variables in
    ``app.yaml`` — a plain direct Postgres connection with username/password,
    no ``WorkspaceClient`` or endpoint API call.
    """
    global _derived_host, _derived_user
    from db.connection import RDSConnectionFactory
    from db.stores import LakebaseFeedbackStore, LakebaseResultStore, init_tables

    if not os.environ.get("RDS_HOST"):
        raise RuntimeError(
            "RDS_HOST environment variable is required for the RDS "
            "connection (set in app.yaml)"
        )
    # Surface the connection parameters for /health diagnostics before the
    # connection attempt that may still fail (and reset them on each call so
    # a later re-derivation overwrites a stale value).
    _derived_host = os.environ.get("RDS_HOST", "")
    _derived_user = os.environ.get("RDS_USER", "")
    _LOGGER.info(
        "RDS connection: host=%s, user=%s, db=%s",
        _derived_host, _derived_user, os.environ.get("RDS_DATABASE", "postgres"))
    connect = RDSConnectionFactory.from_env()
    init_tables(connect)
    return LakebaseResultStore(connect), LakebaseFeedbackStore(connect)


def _get_stores() -> tuple[Any, Any]:
    """Return cached RDS stores ``(result_store, feedback_store)``.

    Lazily initialised on first call. Failures are logged and are not cached,
    allowing a transient credential or database outage to recover.
    """
    global _last_store_error, _stores
    if _stores is not None:
        return _stores
    try:
        _stores = _build_rds_stores()
        _last_store_error = None
    except Exception as exc:
        _last_store_error = f"{type(exc).__name__}: {exc}"
        _LOGGER.exception("RDS store initialization failed")
        return (None, None)
    return _stores


def _get_trace_sink() -> Any:
    """Return a cached MLflow trace sink, or ``None`` if MLflow is unavailable."""
    global _trace_sink
    if _trace_sink is not None:
        return _trace_sink
    try:
        from harness.tracing import build_trace_sink
        _trace_sink = build_trace_sink()
    except Exception:
        _trace_sink = None
    return _trace_sink


class _ProgressTraceSink(TraceSink):
    """Wraps the real TraceSink and mirrors per-node events into the SSE stream.

    The graph's ``_trace`` helper calls :meth:`record` after each node, so
    this sink gives us per-node progress events for the SSE stream while the
    real (wrapped) sink receives the same event for MLflow telemetry.

    When the **extract** node completes, individual extraction items
    (cards, transactions, rewards) are pushed as separate
    ``extraction_item`` SSE events — the frontend renders each as it
    arrives rather than waiting for a batch at the end.

    The judge no longer runs inline (it is a post-hoc evaluation), so there
    are no ``field_verdict`` or ``verdict`` SSE events during a live parse.
    """

    def __init__(
        self,
        wrapped: Optional[TraceSink],
        ctx: RequestContext,
        state: Any = None,
    ) -> None:
        self._wrapped = wrapped
        self._ctx = ctx
        self._state = state

    def record(self, event: TraceEvent) -> None:
        stage = _STAGE_MAP.get(event.name, event.name)
        self._ctx.push("progress", {
            "stage": stage,
            "trace_name": event.name,
            "error": event.error,
        })

        # Stream individual extraction items when the extract node finishes.
        if event.name == "extract" and not event.error:
            self._push_extraction_items()

        if self._wrapped is not None:
            self._wrapped.record(event)

    def log_artifact(self, data: bytes, path: str) -> None:
        """Delegate artifact logging to the wrapped sink (best-effort)."""
        if self._wrapped is not None:
            try:
                self._wrapped.log_artifact(data, path)
            except Exception:
                pass  # artifact logging must never break the SSE stream

    def _push_extraction_items(self) -> None:
        """Push one ``extraction_item`` SSE event per card / transaction / reward."""
        state = self._state
        if state is None or getattr(state, "extraction", None) is None:
            return
        payload = state.extraction.payload
        if not isinstance(payload, dict):
            return
        # Cards
        cards = payload.get("cards", [])
        if isinstance(cards, list):
            for i, card in enumerate(cards):
                self._ctx.push("extraction_item", {
                    "type": "card",
                    "index": i,
                    "data": card,
                })
        # Transactions
        txns = payload.get("transactions", [])
        if isinstance(txns, list):
            for i, txn in enumerate(txns):
                self._ctx.push("extraction_item", {
                    "type": "transaction",
                    "index": i,
                    "data": txn,
                })
        # Rewards (scalar dict, single event)
        rewards = payload.get("rewards")
        if rewards is not None:
            self._ctx.push("extraction_item", {
                "type": "rewards",
                "data": rewards,
            })
        # Summary event — includes the full payload so ResultsView can
        # render extracted fields with per-field Accept/Correct feedback.
        self._ctx.push("extraction", {
            "model_id": state.extraction.model_id,
            "schema_valid": state.extraction.schema_valid,
            "payload": payload,
        })


def _build_deps(ctx: RequestContext, state: Any = None,
                prompt_override: str | None = None,
                schema_override: dict | None = None) -> Any:
    """Build production :class:`NodeDeps` with real ports wired.

    Degrades gracefully: if a port cannot be constructed (RDS down,
    MLflow unavailable), the graph skips that stage rather than failing the
    entire parse. The judge no longer runs inline — it is a post-hoc
    evaluation over MLflow traces (see ``judge/scorer.py``), so no judge
    adapter is constructed here.

    ``state`` is the live :class:`GraphState` — passed to
    :class:`_ProgressTraceSink` so it can push per-item SSE events
    when the extract node completes.

    ``prompt_override`` / ``schema_override`` are passed to the extraction
    adapter when the caller wants to use custom values instead of the bank
    defaults (the ``/api/parse-custom`` endpoint). ``None`` means use the
    bank default (the normal ``/api/parse`` path).
    """
    from graph.nodes import NodeDeps
    from harness.extraction_adapter import LunaExtractionAdapter

    extraction = LunaExtractionAdapter(
        prompt_override=prompt_override,
        schema_override=schema_override,
    )

    result_store, feedback_store = _get_stores()
    trace_sink = _ProgressTraceSink(_get_trace_sink(), ctx, state)

    return NodeDeps(
        extraction=extraction,
        result_store=result_store,
        trace_sink=trace_sink,
        feedback_store=feedback_store,
    )


def _run_parse(ctx: RequestContext, pdf_bytes: bytes, filename: str, bank: str,
               prompt_override: str | None = None,
               schema_override: dict | None = None) -> None:
    """Background-thread entry point: run the LangGraph parse pipeline.

    Pushes SSE events into ``ctx.events`` as each node completes (via the
    :class:`_ProgressTraceSink` that wraps the real MLflow sink).  The trace
    sink pushes individual ``extraction_item`` events when the extract node
    completes — *during* the graph run, not after — so the frontend renders
    per-item results live as they arrive. The judge no longer runs inline.

    After the graph returns, this function stores the extraction snapshot on
    ``ctx`` for the ``GET /api/results`` fallback and pushes the terminal
    ``complete`` (or ``error``) event.

    ``prompt_override`` / ``schema_override`` let the ``/api/parse-custom``
    endpoint run extraction with custom values instead of the bank defaults.
    When ``prompt_override`` is set, ``state.prompt`` is pre-set so
    ``route_node`` skips re-resolution and the trace carries the actual prompt.
    """
    try:
        from contracts.models import ParseRequest as _PR
        from graph.routing import coerce_request_bank
        from graph.state import GraphState

        request = _PR(
            pdf=pdf_bytes,
            filename=filename,
            bank=coerce_request_bank(bank),
            request_id=ctx.request_id,
        )
        # Keep the custom schema on the per-run state so validation uses the
        # exact same schema that the extraction adapter sends to Luna.
        state = GraphState(request=request, schema_override=schema_override)
        # Pre-set the prompt so route_node does not overwrite it with the
        # bank default when a custom prompt is provided.
        if prompt_override is not None:
            state.prompt = prompt_override

        ctx.push("start", {
            "request_id": ctx.request_id,
            "bank": bank,
            "filename": filename,
            "stages": list(PIPELINE_STAGES),
        })

        deps = _build_deps(ctx, state,
                           prompt_override=prompt_override,
                           schema_override=schema_override)

        from graph.graph import run_graph
        final_state = run_graph(deps, state)

        # Store extraction snapshot for /api/results fallback.
        # The extraction_item + extraction SSE events were already pushed
        # by _ProgressTraceSink when the extract node completed.
        if final_state.extraction is not None:
            ctx.extraction_data = {
                "payload": final_state.extraction.payload,
                "model_id": final_state.extraction.model_id,
                "schema_valid": final_state.extraction.schema_valid,
            }

        # Push terminal outcome.  The ``complete`` event is kept SMALL — it
        # carries only scalar status fields, never the extraction payload.
        # A large payload here would risk truncating the SSE frame before
        # the terminating blank line, causing the browser to silently drop
        # the event entirely (no dispatch, no fallback).  The frontend fetches
        # the full extraction via ``GET /api/results/{request_id}`` on the
        # ``complete`` event; the ``extraction`` SSE event is a fast-path
        # optimisation only.
        ctx.outcome = final_state.outcome.value if final_state.outcome else None
        ctx.complete_data = {
            "request_id": ctx.request_id,
            "outcome": ctx.outcome,
            "stage": final_state.stage.value,
            "schema_valid": final_state.schema_valid,
            "validation_errors": list(final_state.validation_errors),
        }
        ctx.push("complete", ctx.complete_data)

    except Exception as exc:
        ctx.error = str(exc)
        ctx.push("error", {"message": str(exc), "request_id": ctx.request_id})
    finally:
        ctx.push_sentinel()


def _run_judge_evaluation_bg(sample_size: int) -> None:
    """Background-thread entry point for the post-hoc judge evaluation.

    Runs ``run_judge_evaluation`` in a daemon thread (the HTTP handler returns
    immediately with 202). The result is stored in ``_judge_result_cache``;
    errors are captured there too so ``GET /api/judge-results`` can surface them
    without leaking internal tracebacks to the client.
    """
    global _judge_result_cache, _judge_running
    try:
        from judge.scorer import run_judge_evaluation
        # Thread the app's cached RDS result store into the scorer so each
        # OK verdict is persisted inline (best-effort) and surfaces on the
        # per-parse Results view. _get_stores()[0] is the result store or None
        # when RDS is unavailable; None lets the scorer build its own.
        result_store = _get_stores()[0]
        result = run_judge_evaluation(sample_size=sample_size, result_store=result_store)
        _judge_result_cache = result
    except Exception as exc:
        _LOGGER = __import__("logging").getLogger("statement-agent.app")
        _LOGGER.warning("judge evaluation failed: %s", exc, exc_info=True)
        _judge_result_cache = {
            "count_judged": 0,
            "count_errors": 1,
            "errors": [{"error": "evaluation failed"}],
            "overall_strict": None,
            "overall_narration_forgiven": None,
            "per_field": {},
            "per_bank": {},
            "eval_run_id": None,
            "_status": "error",
        }
    finally:
        _release_judge_slot()


def _run_single_judge_bg(request_id: str, run_id: str) -> None:
    """Background-thread body for the on-demand single-trace judge.

    Judges JUST the one MLflow run for ``request_id`` (already resolved to
    ``run_id`` by the caller) by delegating to :func:`judge.scorer.score_trace`
    — which reuses the existing scoring logic AND persists the verdict to
    RDS (best-effort) so ``GET /api/results`` surfaces it inline.

    The ``result_store`` is the app's cached RDS store (same one the
    batch path threads through ``_run_judge_evaluation_bg``); ``None`` lets
    the scorer build its own.  Force-rejudge is inherent: ``score_trace``
    does NOT check the ``judged`` tag (only the batch sampler skips already-
    judged runs), so calling it on a ``judged=true`` run re-judges it.

    Updates ``_single_judge_status[request_id]`` so the GET status endpoint
    can report progress.  Never raises — all failures land in the status.
    """
    try:
        from judge.scorer import score_trace
        result_store = _get_stores()[0]
        result = score_trace(run_id, result_store=result_store)
        status = result.get("status", "ERROR")
        if status == "OK":
            _single_judge_status[request_id] = {"status": "done", "request_id": request_id}
        elif status == "JUDGE_ERROR":
            _single_judge_status[request_id] = {
                "status": "error", "request_id": request_id,
                "error": "judge returned an unusable response (JUDGE_ERROR)",
            }
        else:
            _single_judge_status[request_id] = {
                "status": "error", "request_id": request_id,
                "error": result.get("error", "judge failed"),
            }
    except Exception as exc:
        _LOGGER.warning("single-trace judge failed for %s: %s", request_id, exc, exc_info=True)
        _single_judge_status[request_id] = {
            "status": "error", "request_id": request_id,
            "error": "judge failed",
        }
    finally:
        _release_judge_slot()


# ---------------------------------------------------------------------------
# Background judge scheduler (customer-deployable: runs the post-hoc judge
# on a fixed interval so verdicts populate without a manual trigger)
# ---------------------------------------------------------------------------

# How long the synchronous /api/v1/parse endpoint waits for the full pipeline
# before returning a 504. Luna extraction typically takes 15-30s; 300s gives a
# wide margin for a slow first-call cold start without hanging forever.
_SYNC_PARSE_TIMEOUT = 300.0

# Default cadence (hours) and sample size for the background judge scheduler.
_JUDGE_INTERVAL_DEFAULT = 6
_JUDGE_SAMPLE_DEFAULT = 10
# Minimum enabled cadence — a positive value below this is clamped up so the
# scheduler can't spin a tight loop that hammers the (expensive) Opus judge.
_JUDGE_INTERVAL_MIN = 0.1

# Maximum accepted PDF upload size (megabytes). A multipart upload larger than
# this is rejected with 413 BEFORE the body is read into memory. Env-overridable
# so a workspace with larger statements can raise it. 50 MB is generous for a
# credit-card statement PDF (typically 0.1-2 MB).
_MAX_PDF_SIZE_MB_DEFAULT = 50


def _max_pdf_size_mb() -> int:
    """Return the configured max PDF upload size in MB (env-overridable, >= 1).

    ``MAX_PDF_SIZE_MB`` lets a workspace tune the upload cap. A malformed value
    falls back to the default (never raises — a config typo must not break app
    startup).
    """
    raw = os.getenv("MAX_PDF_SIZE_MB", str(_MAX_PDF_SIZE_MB_DEFAULT))
    try:
        size = int(raw)
    except (TypeError, ValueError):
        return _MAX_PDF_SIZE_MB_DEFAULT
    return max(1, size)


def _judge_interval_hours() -> float:
    """Return the configured judge interval in hours (env-overridable).

    ``JUDGE_INTERVAL_HOURS <= 0`` disables the scheduler (returned as-is so the
    caller can distinguish "disabled" from a real cadence). A positive value
    is clamped to a minimum of ``_JUDGE_INTERVAL_MIN`` (0.1 h) so the scheduler
    can't spin a tight loop that hammers the Opus judge. ``NaN`` / ``Infinity``
    fall back to the default — both break the daemon wait loop. A malformed
    value falls back to the default (never raises — a config typo must not
    break app startup).
    """
    import math

    raw = os.getenv("JUDGE_INTERVAL_HOURS", str(_JUDGE_INTERVAL_DEFAULT))
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return float(_JUDGE_INTERVAL_DEFAULT)
    if not math.isfinite(val):
        return float(_JUDGE_INTERVAL_DEFAULT)
    if val <= 0:
        return val  # disables — pass through unchanged
    return max(val, _JUDGE_INTERVAL_MIN)


def _judge_sample_size_env() -> int:
    """Return the configured judge sample size (env-overridable, >= 1).

    Capped at ``MAX_SAMPLE_SIZE`` so the scheduler cannot launch an expensive
    sweep. A malformed value falls back to the default.
    """
    raw = os.getenv("JUDGE_SAMPLE_SIZE", str(_JUDGE_SAMPLE_DEFAULT))
    try:
        size = int(raw)
    except (TypeError, ValueError):
        return _JUDGE_SAMPLE_DEFAULT
    return max(1, min(size, MAX_SAMPLE_SIZE))


def _summarize_judge_result(result: Any) -> dict[str, Any]:
    """Reduce a full judge evaluation result to a compact status summary.

    The full result (per-field/per-bank breakdowns, error traces) is large;
    the scheduler status endpoint only needs the high-level counts so an
    operator can confirm the scheduler is producing verdicts.
    """
    if not isinstance(result, dict):
        return {"status": "unknown"}
    return {
        "count_judged": result.get("count_judged", 0),
        "count_errors": result.get("count_errors", 0),
        "overall_strict": result.get("overall_strict"),
        "overall_narration_forgiven": result.get("overall_narration_forgiven"),
        "eval_run_id": result.get("eval_run_id"),
        "_status": result.get("_status", "done"),
    }


class _JudgeScheduler:
    """Daemon thread that runs the batch judge on a fixed interval.

    Reuses :func:`_run_judge_evaluation_bg` — the SAME runner
    ``/api/run-judge`` uses — so there is one judge code path; only the
    trigger differs (a timer vs an HTTP request). The scheduler acquires the
    shared judge concurrency slot before each run, so it never races a
    manual/on-demand judge: if the slot is busy it skips the interval and
    retries on the next tick.
    """

    def __init__(self, interval_hours: float, sample_size: int) -> None:
        self.interval_hours = interval_hours
        self.sample_size = sample_size
        self.active = False
        self.last_run_at: Optional[str] = None
        self.next_run_at: Optional[str] = None
        self.last_summary: Optional[dict[str, Any]] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the daemon thread. A no-op (and inactive) when disabled."""
        if self.interval_hours <= 0:
            self.active = False
            _LOGGER.info(
                "judge scheduler disabled (JUDGE_INTERVAL_HOURS=%s)",
                self.interval_hours,
            )
            return
        self.active = True
        self._schedule_next()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="savesage-judge-scheduler",
        )
        self._thread.start()
        _LOGGER.info(
            "judge scheduler started: every %s hours, sample_size=%d",
            self.interval_hours, self.sample_size,
        )

    def _schedule_next(self) -> None:
        self.next_run_at = (
            datetime.now(UTC) + timedelta(hours=self.interval_hours)
        ).isoformat()

    def _loop(self) -> None:
        interval_s = max(0.0, self.interval_hours) * 3600.0
        while not self._stop.wait(interval_s):
            if self._stop.is_set():
                break
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001 — scheduler must never die
                _LOGGER.warning(
                    "scheduled judge run raised unexpectedly: %s",
                    exc, exc_info=True,
                )
                self.last_summary = {"status": "error", "error": str(exc)}
            self.last_run_at = datetime.now(UTC).isoformat()
            self._schedule_next()

    def _tick(self) -> None:
        """Run one scheduled judge evaluation (best-effort, never raises)."""
        if not _acquire_judge_slot():
            self.last_summary = {
                "status": "skipped", "reason": "judge already running",
            }
            _LOGGER.info("scheduled judge run skipped: judge slot busy")
            return
        # _run_judge_evaluation_bg releases the slot in its finally block, so
        # there is no double release (we acquire once here, it releases once).
        _run_judge_evaluation_bg(self.sample_size)
        self.last_summary = _summarize_judge_result(_judge_result_cache)
        _LOGGER.info("scheduled judge run complete: %s", self.last_summary)

    def stop(self) -> None:
        """Signal the daemon loop to exit after the current wait."""
        self._stop.set()
        self.active = False

    def status(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "interval_hours": self.interval_hours,
            "sample_size": self.sample_size,
            "last_run_at": self.last_run_at,
            "next_run_at": self.next_run_at,
            "last_summary": self.last_summary,
        }


# Process-level scheduler singleton, started once from create_app().
_judge_scheduler: Optional[_JudgeScheduler] = None


def _start_judge_scheduler() -> None:
    """Build and start the background judge scheduler (best-effort).

    Reads ``JUDGE_INTERVAL_HOURS`` / ``JUDGE_SAMPLE_SIZE`` from the
    environment. Disabled (and inactive) when the interval is <= 0. Never
    raises — a scheduler failure must not block app startup.

    Re-entrancy: if a scheduler is already running (e.g. ``create_app`` is
    called twice in a test harness), the existing one is stopped first so its
    daemon thread exits cleanly — no orphaned loop keeps firing the judge.
    """
    global _judge_scheduler
    # Stop any pre-existing scheduler so its daemon thread exits before we
    # replace the global. idempotent when the scheduler is None or inactive.
    if _judge_scheduler is not None and _judge_scheduler.active:
        _judge_scheduler.stop()
    try:
        interval = _judge_interval_hours()
        sample = _judge_sample_size_env()
        _judge_scheduler = _JudgeScheduler(interval, sample)
        _judge_scheduler.start()
    except Exception as exc:  # noqa: BLE001 — never break startup
        _LOGGER.warning("judge scheduler failed to start: %s", exc, exc_info=True)
        _judge_scheduler = None


# ---------------------------------------------------------------------------
# FastAPI app — all third-party imports are function-local inside create_app
# ---------------------------------------------------------------------------

def create_app():
    """Build and return the FastAPI application.

    ``fastapi`` is imported function-local so the module is importable in a
    stdlib-only environment (the contract-test gate imports helper functions
    from this module without fastapi installed).
    """
    from pathlib import Path as _Path

    from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
    from fastapi.responses import JSONResponse, Response, StreamingResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="SaveSage Statement Agent")
    static_dir = _Path(__file__).resolve().parent / "static"

    # -- GET /health -----------------------------------------------------
    @app.get("/health")
    def health() -> dict[str, Any]:
        import os
        # RDS_HOST/RDS_USER/RDS_PASSWORD are required for the direct RDS
        # Postgres connection (set in app.yaml).
        env_status = {
            name: "set" if os.environ.get(name) else "MISSING"
            for name in ("RDS_HOST", "RDS_USER", "RDS_PASSWORD", "RDS_SSLMODE")
        }
        psycopg_ok = True
        try:
            import psycopg  # noqa: F401
        except ImportError:
            psycopg_ok = False
        return {
            "status": "ok",
            "stores_initialized": _stores is not None,
            "last_store_error": _last_store_error,
            "env_vars": env_status,
            "psycopg_available": psycopg_ok,
            "rds_host": _derived_host,
            "rds_user": _derived_user,
        }

    # -- POST /api/parse -------------------------------------------------
    @app.post("/api/parse")
    async def parse(file: UploadFile = File(...), bank: str = Form(...)):
        pdf_bytes = await file.read()
        if not pdf_bytes:
            raise HTTPException(status_code=400, detail="empty file")

        request_id = _new_request_id()
        ctx = RequestContext(request_id)
        ctx.pdf_bytes = pdf_bytes
        ctx.pdf_filename = file.filename or "statement.pdf"
        _REQUESTS[request_id] = ctx
        # FIFO eviction: prevent unbounded memory growth from stored PDF bytes.
        while len(_REQUESTS) > _MAX_REQUESTS:
            oldest = next(iter(_REQUESTS))
            _REQUESTS.pop(oldest, None)

        thread = threading.Thread(
            target=_run_parse,
            args=(ctx, pdf_bytes, file.filename or "statement.pdf", bank),
            daemon=True,
        )
        thread.start()

        return {"request_id": request_id}

    # -- POST /api/v1/parse (synchronous customer API) -------------------
    @app.post("/api/v1/parse")
    async def parse_v1(file: UploadFile = File(...), bank: str = Form(...)):
        """Synchronous parse endpoint — the PRIMARY customer integration point.

        Accepts a multipart form (``file`` = PDF upload, ``bank`` = bank name),
        runs the FULL parse pipeline synchronously (route → extract →
        validate → persist → finalize), and returns the extracted JSON
        directly. This is NOT a background thread — the caller blocks until
        the extraction completes (or times out).

        MLflow tracing still works: the trace sink hooks into the same graph
        node events as the async path, so every v1 parse is traced end-to-end.
        The result is also stored in the in-memory ``_REQUESTS`` dict, so the
        existing ``GET /api/results/{request_id}`` endpoint serves follow-up
        queries (feedback, on-demand re-judge) for the same ``request_id``.

        Responses:
          * 200 — extraction succeeded; ``extraction`` holds the payload.
          * 400 — invalid PDF (empty / not a PDF) or invalid bank name.
          * 413 — the upload exceeds the configured ``MAX_PDF_SIZE_MB``.
          * 422 — extraction failed; ``status`` is ``EXTRACTION_FAILED``.
          * 504 — the pipeline did not complete within the sync timeout.
        """
        import asyncio

        max_bytes = _max_pdf_size_mb() * 1024 * 1024
        try:
            pdf_bytes = await _read_bounded(file, max_bytes)
        except _UploadTooLarge as exc:
            return JSONResponse(
                status_code=413,
                content={
                    "status": "EXTRACTION_FAILED",
                    "error": (
                        f"upload too large: {exc.observed_mb:.1f} MB exceeds "
                        f"the {exc.limit_mb} MB limit (MAX_PDF_SIZE_MB)"
                    ),
                },
            )

        # PDF validation: non-empty + %PDF magic bytes (pure helper → 400).
        try:
            _validate_v1_pdf(pdf_bytes)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        # Bank-name validation (format only; unknown banks fall back to GENERIC).
        from harness.dbfs import validate_bank_name
        try:
            bank_name = validate_bank_name(bank)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        request_id = _new_request_id()
        ctx = RequestContext(request_id)
        ctx.pdf_bytes = pdf_bytes
        ctx.pdf_filename = file.filename or "statement.pdf"
        _REQUESTS[request_id] = ctx
        # FIFO eviction (same as /api/parse).
        while len(_REQUESTS) > _MAX_REQUESTS:
            _REQUESTS.pop(next(iter(_REQUESTS)), None)

        # Run the full pipeline synchronously in a worker thread with a bounded
        # wait so a hung extraction returns a 504 rather than blocking the single
        # uvicorn event loop. _run_parse never raises (it stores errors on ctx),
        # so a timeout is the only failure mode that reaches the except below;
        # the orphaned worker thread is left to complete (Python can't kill it).
        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(
                    None, _run_parse, ctx, pdf_bytes,
                    file.filename or "statement.pdf", bank_name,
                ),
                timeout=_SYNC_PARSE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            return JSONResponse(
                status_code=504,
                content={
                    "request_id": request_id,
                    "bank": bank_name,
                    "status": "EXTRACTION_FAILED",
                    "extraction": None,
                    "error": (
                        f"parse did not complete within "
                        f"{_SYNC_PARSE_TIMEOUT:.0f}s"
                    ),
                    "verdict": None,
                },
            )

        # Build the response from the context the graph populated (pure helper).
        status_code, body = _build_v1_response(ctx, request_id, bank_name)
        if status_code == 200:
            return body
        return JSONResponse(status_code=status_code, content=body)

    # -- GET /api/pdf/{request_id} ---------------------------------------
    @app.get("/api/pdf/{request_id}")
    async def get_pdf(request_id: str):
        ctx = _REQUESTS.get(request_id)
        if ctx is None or ctx.pdf_bytes is None:
            return Response(
                content=b'<html><body style="font-family:sans-serif;padding:2rem;color:#666">PDF not available. The session may have expired.</body></html>',
                media_type="text/html",
                status_code=404,
            )
        return Response(
            content=ctx.pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="{ctx.pdf_filename}"'},
        )

    # -- GET /api/parse/{request_id}/stream (SSE) -------------------------
    @app.get("/api/parse/{request_id}/stream")
    async def stream(request_id: str):
        import asyncio

        ctx = _REQUESTS.get(request_id)
        if ctx is None:
            return JSONResponse(
                status_code=404,
                content={"detail": f"unknown request_id: {request_id}"},
            )

        async def generate():
            loop = asyncio.get_event_loop()
            while True:
                try:
                    event = await loop.run_in_executor(
                        None, _queue_get, ctx.events, 0.5,
                    )
                except queue.Empty:
                    if ctx.done.is_set():
                        break
                    yield ": \n\n"  # SSE comment — keep-alive heartbeat
                    continue
                if event is None:  # sentinel — stream is done
                    break
                yield _sse_event(event["event"], event["data"])

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    # -- POST /api/feedback/{request_id} ---------------------------------
    @app.post("/api/feedback/{request_id}")
    async def submit_feedback(request_id: str, body: dict = Body(...)):
        try:
            v = _validate_feedback_body(body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        # Build the feedback dataclass.  Guarded so a constructor or
        # __post_init__ validation failure degrades to in-memory storage
        # rather than surfacing as an unhandled 500/502.  The path is
        # pre-validated above, but this is defence-in-depth.
        fb = None
        try:
            from contracts.models import FieldFeedback

            fb = FieldFeedback(
                request_id=request_id,
                field_path=v["field_path"],
                original_value=v["original_value"],
                corrected_value=v["corrected_value"] if not v["accepted"] else None,
                accepted=v["accepted"],
                actor=v["actor"],
                timestamp=datetime.now(UTC),
            )
        except Exception:
            fb = None

        # Persist to RDS + log to MLflow — both best-effort.  Each blocking
        # call runs in a worker thread with a bounded wait (``_run_blocking``)
        # so a hung RDS connection or MLflow request can never freeze the
        # single uvicorn event loop — the mechanism behind the proxy 502 on
        # feedback submit.  Even if both fail, we fall back to in-memory below.
        try:
            stores = await _run_blocking(_get_stores)
            if stores is not None:
                _result_store, feedback_store = stores
            else:
                _result_store, feedback_store = None, None
        except Exception:
            _result_store, feedback_store = None, None
        if feedback_store is not None and fb is not None:
            await _run_blocking(feedback_store.append_feedback, fb)

        try:
            sink = await _run_blocking(_get_trace_sink)
        except Exception:
            sink = None
        if sink is not None and fb is not None:
            await _run_blocking(sink.log_field_feedback, fb)

        # Store in-memory for /api/results fallback.
        ctx = _REQUESTS.get(request_id)
        if ctx is not None:
            ctx.feedback.append({
                "field_path": v["field_path"],
                "disposition": v["disposition"],
                "original_value": v["original_value"],
                "corrected_value": v["corrected_value"],
                "actor": v["actor"],
                "timestamp": datetime.now(UTC).isoformat(),
            })

        return {
            "status": "ok",
            "request_id": request_id,
            "field_path": v["field_path"],
        }

    # -- GET /api/results/{request_id} -----------------------------------
    @app.get("/api/results/{request_id}")
    async def results(request_id: str):
        ctx = _REQUESTS.get(request_id)

        extraction: Optional[dict[str, Any]] = None
        verdict: Optional[dict[str, Any]] = None
        feedback_list: list[dict[str, Any]] = []

        # Try RDS first (durable persistence).  Blocking calls run in a
        # worker thread with a bounded wait (``_run_blocking``) so a hung
        # connection can never freeze the single uvicorn event loop (the same
        # 502 mechanism as feedback submit).  ``_run_blocking`` returns None
        # on exception/timeout, which simply falls back to in-memory below.
        try:
            stores = await _run_blocking(_get_stores)
            if stores is not None:
                result_store, feedback_store = stores
            else:
                result_store, feedback_store = None, None
        except Exception:
            result_store, feedback_store = None, None

        if result_store is not None:
            try:
                stored = await _run_blocking(result_store.get_extraction, request_id)
                if stored is not None:
                    extraction = {
                        "payload": stored.payload,
                        "model_id": stored.model_id,
                        "schema_valid": stored.schema_valid,
                    }
                stored_v = await _run_blocking(result_store.get_verdict, request_id)
                if stored_v is not None:
                    verdict = {
                        "comparisons": [
                            _comparison_to_dict(c) for c in stored_v.comparisons
                        ],
                        "judge_model_id": stored_v.judge_model_id,
                        "summary": stored_v.summary,
                    }
            except Exception:
                pass

        if feedback_store is not None:
            try:
                rows = await _run_blocking(feedback_store.list_feedback, request_id)
                if rows:
                    for f in rows:
                        ts = f.timestamp
                        feedback_list.append({
                            "field_path": f.field_path,
                            "accepted": f.accepted,
                            "original_value": f.original_value,
                            "corrected_value": f.corrected_value,
                            "actor": f.actor,
                            "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                        })
            except Exception:
                pass

        # Fall back to in-memory context (when RDS is unavailable).
        if extraction is None and ctx is not None:
            extraction = ctx.extraction_data
        if not feedback_list and ctx is not None:
            feedback_list = list(ctx.feedback)

        return {
            "request_id": request_id,
            "extraction": extraction,
            "verdict": verdict,
            "feedback": feedback_list,
        }

    # -- POST /api/run-judge ---------------------------------------------
    @app.post("/api/run-judge")
    async def run_judge(body: dict = Body(default={})):
        """Trigger a post-hoc judge evaluation over sampled MLflow traces.

        Accepts ``{"sample_size": N}`` (default 10, max ``MAX_SAMPLE_SIZE``).
        Runs in a background thread so the HTTP response returns immediately
        with 202; the client polls ``GET /api/judge-results`` for the result.
        Returns 409 if an evaluation is already running.
        """
        global _judge_running

        # Parse and validate sample_size — wrap in try/except so a non-int
        # value (e.g. "abc") returns 400, not a 500 Internal Server Error.
        raw = body.get("sample_size", 10)
        try:
            sample_size = int(raw)
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=400,
                detail=f"sample_size must be an integer, got {type(raw).__name__}",
            )
        if sample_size < 1:
            raise HTTPException(status_code=400, detail="sample_size must be >= 1")
        if sample_size > MAX_SAMPLE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"sample_size must be <= {MAX_SAMPLE_SIZE}",
            )

        # Only one evaluation at a time (shared with the single-trace judge).
        if not _acquire_judge_slot():
            raise HTTPException(status_code=409, detail="evaluation already running")

        thread = threading.Thread(
            target=_run_judge_evaluation_bg,
            args=(sample_size,),
            daemon=True,
        )
        # Hand slot ownership to the runner ONLY after a successful start().
        # If start() raises (RuntimeError / resource exhaustion / thread
        # limit), release the slot ourselves so a subsequent judge request
        # (batch OR single-trace) is NOT 409'd permanently until app restart
        # — the runner's finally never runs when the thread never starts,
        # so there is NO double-release (a runner that never started does
        # not release). Mirrors the single-trace path below.
        try:
            thread.start()
        except Exception:
            _release_judge_slot()
            _LOGGER.warning(
                "failed to start batch judge thread", exc_info=True,
            )
            raise HTTPException(
                status_code=503,
                detail="failed to start judge; please retry",
            )

        return JSONResponse(
            status_code=202,
            content={"status": "started", "sample_size": sample_size},
        )

    # -- GET /api/judge-results ------------------------------------------
    @app.get("/api/judge-results")
    async def judge_results():
        """Return the most recent judge evaluation results.

        Serves from the process-level cache populated by ``POST /api/run-judge``.
        Returns ``{"status": "running", "results": null}`` if an evaluation is
        in progress, ``{"status": "idle", "results": null}`` if none has been
        run yet, or ``{"status": "done", "results": {...}}`` with the cached result.
        """
        if _judge_running:
            return {"status": "running", "results": None}
        if _judge_result_cache is not None:
            return {"status": "done", "results": _judge_result_cache}
        return {"status": "idle", "results": None}

    # -- GET /api/v1/judge/status (scheduler status) ---------------------
    @app.get("/api/v1/judge/status")
    async def judge_scheduler_status():
        """Return the background judge scheduler status.

        Reports whether the scheduler is active, the configured interval and
        sample size, the last/next run timestamps (ISO-8601 UTC), and a compact
        summary of the last scheduled batch. The scheduler is disabled (and
        reported inactive) when ``JUDGE_INTERVAL_HOURS`` is set to 0 or less.
        """
        if _judge_scheduler is None:
            # Scheduler not built (disabled at startup or build failed).
            return {
                "active": False,
                "interval_hours": _judge_interval_hours(),
                "sample_size": _judge_sample_size_env(),
                "last_run_at": None,
                "next_run_at": None,
                "last_summary": None,
            }
        return _judge_scheduler.status()

    # -- POST /api/results/{request_id}/judge ----------------------------
    @app.post("/api/results/{request_id}/judge")
    async def judge_single(request_id: str):
        """Judge a SINGLE trace on demand so inline per-field verdicts render
        immediately without waiting for the 6-hour scheduled sampler.

        Resolves ``request_id`` → MLflow ``run_id`` (via the ``request_id`` tag
        the tracing sink sets on each parse run), then runs the existing
        single-trace scorer in a background thread (the Opus call takes
        ~15-20s — MUST NOT block the uvicorn event loop).  Returns 202
        immediately; the frontend polls ``GET /api/results/{request_id}/judge``
        for status, then re-fetches ``GET /api/results`` to render the
        inline verdict.

        Force-rejudge: ``score_trace`` does NOT check the ``judged`` tag (only
        the batch sampler skips already-judged runs), so this always re-judges
        — even if the run is already tagged ``judged=true``.

        Concurrency: shares the ``_judge_running`` guard with the batch
        ``POST /api/run-judge`` — returns 409 if ANY judge run is in progress.

        Returns:
          * 202 ``{"status": "started", "request_id": ...}`` — judging in bg.
          * 404 — no MLflow trace found for this ``request_id``.
          * 409 — a judge run (batch or single) is already in progress.
          * 500 — never (errors land in the status endpoint).
        """
        # Validate the request_id format BEFORE building an MLflow filter_string
        # from it — a crafted/quoted value could alter the filter (tags.request_id
        # = '<value>') or select the wrong run. Reject malformed ids with 400
        # before touching the concurrency lock.
        if not _is_valid_request_id(request_id):
            raise HTTPException(
                status_code=400,
                detail="request_id must be the canonical 'req-<12hex>' form",
            )
        # Concurrency guard — shared slot with the batch sampler. Reserve
        # BEFORE resolving so a concurrent batch request sees the busy flag.
        # Released in _run_single_judge_bg's finally (or here on 404 failure).
        if not _acquire_judge_slot():
            raise HTTPException(
                status_code=409,
                detail="a judge run is already in progress",
            )

        # Resolve request_id → run_id. This is a ~1s MLflow tag-filter search
        # (not the 15-20s Opus call), so it's safe to run via _run_blocking
        # (bounded 5s) on the event loop. If no run is found, release the guard
        # and return 404 — never 500.
        try:
            from judge.scorer import resolve_run_id
            run_id = await _run_blocking(resolve_run_id, request_id)
        except Exception:
            run_id = None

        if run_id is None:
            _release_judge_slot()
            raise HTTPException(
                status_code=404,
                detail=(
                    "no MLflow trace found for this request; it may predate "
                    "the request_id tag, has aged out of the experiment, or "
                    "MLflow is unavailable"
                ),
            )

        # Evict the oldest status entry if at capacity (FIFO).
        while len(_single_judge_status) >= _MAX_SINGLE_JUDGE_STATUS:
            _single_judge_status.pop(next(iter(_single_judge_status)), None)
        _single_judge_status[request_id] = {"status": "running", "request_id": request_id}

        thread = threading.Thread(
            target=_run_single_judge_bg,
            args=(request_id, run_id),
            daemon=True,
        )
        # Hand slot ownership to the runner ONLY after a successful start().
        # If start() raises (RuntimeError / resource exhaustion / thread limit),
        # release the slot ourselves so a subsequent judge request is NOT 409'd
        # permanently until app restart — the runner's finally never runs when
        # the thread never starts.
        try:
            thread.start()
        except Exception:
            _release_judge_slot()
            _single_judge_status[request_id] = {
                "status": "error", "request_id": request_id,
                "error": "failed to start judge thread",
            }
            _LOGGER.warning(
                "failed to start single-judge thread for %s",
                request_id, exc_info=True,
            )
            raise HTTPException(
                status_code=503,
                detail="failed to start judge; please retry",
            )

        return JSONResponse(
            status_code=202,
            content={"status": "started", "request_id": request_id},
        )

    # -- GET /api/results/{request_id}/judge ----------------------------
    @app.get("/api/results/{request_id}/judge")
    async def judge_single_status(request_id: str):
        """Return the on-demand single-trace judge status for a request.

        Polled by the frontend after ``POST /api/results/{request_id}/judge``
        returns 202.  Returns ``{"status": "running"|"done"|"error", ...}``
        or ``{"status": "idle"}`` if no judge has been triggered for this
        request in this process.
        """
        return _single_judge_status.get(
            request_id, {"status": "idle", "request_id": request_id},
        )

    # -- GET /api/prompt/{bank} ------------------------------------------
    @app.get("/api/prompt/{bank}")
    async def get_prompt_schema(bank: str):
        """Return the prompt text and schema JSON for a bank.

        Loads from the shared bank config if it exists, else from the bundled file
        (PROMPT_BY_BANK / SCHEMA_BY_BANK).  Unknown bank names fall back to
        the GENERIC prompt/schema (handled by resolve_prompt /
        load_schema_for_bank).
        """
        from graph.routing import resolve_prompt
        from rules.routing import load_schema_for_bank
        prompt = resolve_prompt(bank)
        schema = load_schema_for_bank(bank)
        return {"prompt": prompt, "schema": schema}

    # -- POST /api/prompt/{bank} ----------------------------------------
    @app.post("/api/prompt/{bank}")
    async def save_prompt_schema(bank: str, body: dict = Body(...)):
        """Save the prompt text and schema JSON to DBFS for a bank.

        Both ``prompt`` and ``schema`` must be present in the body. Writes to
        the shared bank config layout
        ``/Workspace/savesage-statement-agent/banks/<BANK>/prompt.txt`` and
        ``schema.json`` — the routing layer reads this path first, so the
        override takes effect without a restart. For a bank not in the
        built-in :class:`Bank` enum, the bank is also added to the DBFS
        registry so ``GET /api/banks`` lists it. The bank name is upper-cased.
        """
        from contracts.models import Bank
        from harness.dbfs import (
            bank_dbfs_dir,
            bank_prompt_dbfs_path,
            bank_schema_dbfs_path,
            mkdirs_dbfs,
            read_dbfs_registry,
            validate_bank_name,
            write_dbfs_registry,
            write_dbfs_text,
        )

        try:
            name = validate_bank_name(bank)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        prompt = body.get("prompt")
        schema = body.get("schema")
        if prompt is None or schema is None:
            raise HTTPException(
                status_code=400,
                detail="both 'prompt' and 'schema' are required",
            )
        schema = _coerce_schema(schema)
        if not mkdirs_dbfs(bank_dbfs_dir(name)):
            raise HTTPException(
                status_code=502,
                detail="Failed to create bank config directory. Contact an admin to ensure /Workspace/savesage-statement-agent/banks/ exists and the app service principal has CAN_MANAGE permission.",
            )
        prompt_ok = write_dbfs_text(bank_prompt_dbfs_path(name), str(prompt))
        schema_ok = write_dbfs_text(
            bank_schema_dbfs_path(name),
            json.dumps(schema, indent=2, ensure_ascii=False),
        )
        if not prompt_ok or not schema_ok:
            raise HTTPException(
                status_code=502,
                detail="Failed to save bank configuration. Check app logs for details.",
            )
        if name not in {item.value for item in Bank}:
            registry = read_dbfs_registry()
            if name not in registry:
                # Best-effort demo registry: concurrent read-modify-write calls
                # can race, which is acceptable for this single-user app.
                registry.append(name)
                if not write_dbfs_registry(registry):
                    raise HTTPException(
                        status_code=502,
                        detail="Bank files saved but registry update failed.",
                    )
        return {"status": "ok", "bank": name}

    # -- GET /api/banks -------------------------------------------------
    @app.get("/api/banks")
    async def list_banks():
        """Return every bank: built-in (from the :class:`Bank` enum) plus
        dynamically added banks from the DBFS registry. Each entry is
        ``{"name": "HDFC", "dynamic": false}`` (built-in) or
        ``{"name": "KOTAK", "dynamic": true}`` (runtime-added).
        """
        from contracts.models import Bank
        from harness.dbfs import read_dbfs_registry

        builtin_names = [b.value for b in Bank]
        builtin = [{"name": n, "dynamic": False} for n in builtin_names]
        registry = read_dbfs_registry()
        dynamic = [
            {"name": n, "dynamic": True}
            for n in registry
            if n and n not in builtin_names
        ]
        return builtin + dynamic

    # -- POST /api/banks ------------------------------------------------
    @app.post("/api/banks")
    async def create_bank(body: dict = Body(...)):
        """Create a new (dynamic) bank with its prompt and schema.

        Body: ``{"name": "KOTAK", "prompt": "...", "schema": {...}}`` (the
        schema may also be a JSON string). Persists the prompt/schema to DBFS
        and adds the bank to the registry. Rejects names that collide with a
        built-in bank or an already-registered dynamic bank. Returns the
        created bank entry.
        """
        from contracts.models import Bank
        from harness.dbfs import (
            bank_prompt_dbfs_path,
            bank_schema_dbfs_path,
            bank_dbfs_dir,
            mkdirs_dbfs,
            read_dbfs_registry,
            validate_bank_name,
            write_dbfs_registry,
            write_dbfs_text,
        )

        try:
            name = validate_bank_name(body.get("name"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        prompt = body.get("prompt")
        schema = body.get("schema")
        if prompt is None or schema is None:
            raise HTTPException(
                status_code=400,
                detail="both 'prompt' and 'schema' are required",
            )
        if not isinstance(prompt, str) or not prompt.strip():
            raise HTTPException(status_code=400, detail="'prompt' must not be empty")
        schema = _coerce_schema(schema)
        builtin_names = {b.value for b in Bank}
        if name in builtin_names:
            raise HTTPException(
                status_code=409,
                detail=f"bank {name!r} already exists as a built-in bank",
            )
        registry = read_dbfs_registry()
        if name in registry:
            raise HTTPException(
                status_code=409,
                detail=f"bank {name!r} already exists as a dynamic bank",
            )
        if not mkdirs_dbfs(bank_dbfs_dir(name)):
            raise HTTPException(
                status_code=502,
                detail="Failed to create bank config directory. Contact an admin to ensure /Workspace/savesage-statement-agent/banks/ exists and the app service principal has CAN_MANAGE permission.",
            )
        prompt_ok = write_dbfs_text(bank_prompt_dbfs_path(name), str(prompt))
        schema_ok = write_dbfs_text(
            bank_schema_dbfs_path(name),
            json.dumps(schema, indent=2, ensure_ascii=False),
        )
        if not prompt_ok or not schema_ok:
            raise HTTPException(
                status_code=502,
                detail="Failed to save bank configuration. Check app logs for details.",
            )
        registry.append(name)
        # Best-effort demo registry: concurrent read-modify-write calls can
        # race, which is acceptable for this single-user app.
        if not write_dbfs_registry(registry):
            raise HTTPException(
                status_code=502,
                detail="Bank files saved but registry update failed.",
            )
        return {"name": name, "dynamic": True}

    # -- GET /api/schema/{bank} -----------------------------------------
    @app.get("/api/schema/{bank}")
    async def get_schema(bank: str):
        """Return the schema JSON for a bank (built-in or dynamic).

        Resolves via :func:`rules.routing.load_schema_for_bank`, which checks
        the DBFS override first and falls back to the bundled schema.
        """
        from rules.routing import load_schema_for_bank
        return load_schema_for_bank(bank)

    # -- POST /api/schema/{bank} ----------------------------------------
    @app.post("/api/schema/{bank}")
    async def save_schema(bank: str, body: dict = Body(...)):
        """Save a schema to DBFS for the given bank (built-in or dynamic).

        Body: ``{"schema": {...}}`` (a JSON object) or ``{"schema": "{...}"}``
        (a JSON string). The bank name is upper-cased; a non-built-in bank is
        added to the registry so it is discoverable by ``GET /api/banks``.
        """
        from contracts.models import Bank
        from harness.dbfs import (
            bank_dbfs_dir,
            bank_schema_dbfs_path,
            mkdirs_dbfs,
            read_dbfs_registry,
            validate_bank_name,
            write_dbfs_registry,
            write_dbfs_text,
        )

        try:
            name = validate_bank_name(bank)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        schema = _coerce_schema(body.get("schema"))
        if not mkdirs_dbfs(bank_dbfs_dir(name)):
            raise HTTPException(
                status_code=502,
                detail="Failed to create bank config directory. Contact an admin to ensure /Workspace/savesage-statement-agent/banks/ exists and the app service principal has CAN_MANAGE permission.",
            )
        if not write_dbfs_text(
            bank_schema_dbfs_path(name),
            json.dumps(schema, indent=2, ensure_ascii=False),
        ):
            raise HTTPException(
                status_code=502,
                detail="Failed to save bank configuration. Check app logs for details.",
            )
        if name not in {item.value for item in Bank}:
            registry = read_dbfs_registry()
            if name not in registry:
                # Best-effort demo registry: concurrent read-modify-write calls
                # can race, which is acceptable for this single-user app.
                registry.append(name)
                if not write_dbfs_registry(registry):
                    raise HTTPException(
                        status_code=502,
                        detail="Bank files saved but registry update failed.",
                    )
        return {"status": "ok", "bank": name}

    # -- POST /api/parse-custom -----------------------------------------
    @app.post("/api/parse-custom")
    async def parse_custom(
        file: UploadFile = File(...),
        bank: str = Form(...),
        prompt_override: str = Form(None),
        schema_override: str = Form(None),
    ):
        """Run extraction with a custom prompt/schema instead of bank defaults.

        Accepts the same multipart form as ``/api/parse`` plus optional
        ``prompt_override`` (text) and ``schema_override`` (JSON string).
        If an override is absent, the bank default is used. Returns the
        same ``{"request_id": ...}`` shape; the frontend consumes SSE
        from the same ``/api/parse/{request_id}/stream`` endpoint.
        Unknown bank names are accepted (GENERIC fallback).
        """
        pdf_bytes = await file.read()
        if not pdf_bytes:
            raise HTTPException(status_code=400, detail="empty file")

        custom_schema: dict | None = None
        if schema_override:
            try:
                custom_schema = json.loads(schema_override)
            except json.JSONDecodeError:
                raise HTTPException(
                    status_code=400,
                    detail="schema_override is not valid JSON",
                )
            if not isinstance(custom_schema, dict):
                raise HTTPException(
                    status_code=400,
                    detail=f"schema_override must be a JSON object, got {type(custom_schema).__name__}",
                )

        custom_prompt: str | None = prompt_override if prompt_override else None

        request_id = _new_request_id()
        ctx = RequestContext(request_id)
        ctx.pdf_bytes = pdf_bytes
        ctx.pdf_filename = file.filename or "statement.pdf"
        _REQUESTS[request_id] = ctx
        while len(_REQUESTS) > _MAX_REQUESTS:
            oldest = next(iter(_REQUESTS))
            _REQUESTS.pop(oldest, None)

        thread = threading.Thread(
            target=_run_parse,
            args=(ctx, pdf_bytes, file.filename or "statement.pdf", bank,
                  custom_prompt, custom_schema),
            daemon=True,
        )
        thread.start()

        return {"request_id": request_id}

    # -- Static files (catch-all, mounted AFTER API routes) --------------
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    try:
        from harness.dbfs import seed_builtin_configs

        if not seed_builtin_configs():
            _LOGGER.warning(
                "Built-in bank configs were not seeded; using bundled fallbacks"
            )
    except Exception as exc:  # noqa: BLE001 -- seeding must never block startup
        _LOGGER.warning("Built-in bank config seeding failed: %s", exc)

    # Start the background judge scheduler (best-effort; never blocks startup).
    # Disabled (and reported inactive) when JUDGE_INTERVAL_HOURS <= 0.
    _start_judge_scheduler()

    return app


# Module-level app instance.  Guarded so a stdlib-only environment can still
# import the helper functions above without FastAPI (or its optional
# ``python-multipart`` form-data dependency) installed.
#
# When ``create_app()`` fails we install a minimal ASGI diagnostic app that
# returns the exception traceback as JSON, so the deployed app's 500 response
# contains the *reason* rather than a bare "Internal Server Error" from a
# ``None`` ASGI object.  In a stdlib-only test environment the diagnostic
# app is never used (the tests import the pure helpers directly).
try:
    app = create_app()
except Exception as _create_app_exc:  # noqa: BLE001 — diagnostic
    import traceback as _tb

    _create_app_traceback = _tb.format_exc()

    async def _diagnostic_app(scope, receive, send):  # type: ignore[no-untyped-def]
        """Minimal ASGI app that returns the create_app() exception as JSON.

        Handles ``lifespan`` events so uvicorn doesn't hang during startup.
        Returns HTTP 200 (not 500) so the Databricks proxy passes the body
        through instead of replacing it with its own error page.
        """
        if scope["type"] == "lifespan":
            # Respond to lifespan startup/shutdown so uvicorn proceeds.
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
            return
        if scope["type"] != "http":
            return

        body = json.dumps(
            {
                "error": "create_app() failed",
                "exception": str(_create_app_exc),
                "type": type(_create_app_exc).__name__,
                "traceback": _create_app_traceback,
            },
            default=str,
        ).encode()
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [[b"content-type", b"application/json"]],
        })
        await send({"type": "http.response.body", "body": body})

    app = _diagnostic_app
