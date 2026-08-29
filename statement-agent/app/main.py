"""FastAPI app for SaveSage Statement Agent (workstream 6).

Wires the real ports into the LangGraph parse pipeline:

* ``ExtractionAdapter`` → :class:`harness.extraction_adapter.LunaExtractionAdapter`
* ``JudgeAdapter`` → :class:`harness.judge_adapter.OpusJudgeAdapter`
* ``ResultStore`` + ``FeedbackStore`` → :mod:`db.stores` (Lakebase/psycopg)
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
from datetime import UTC, datetime
from typing import Any, Optional

from contracts.models import (
    Bank,
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
# Last error from a Lakebase store-init attempt, surfaced via the /health
# endpoint for diagnostics.  ``None`` means "no attempt has failed".
_last_store_error: Optional[str] = None
# Connection parameters most recently resolved from the environment /
# ``WorkspaceClient`` identity by ``_build_lakebase_stores``.
# Surfaced via the /health endpoint for diagnostics; ``None`` until a
# derivation attempt has produced them (so a partial failure still shows
# whatever was resolved before the error).
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

# Max seconds to wait for a best-effort persistence/telemetry call (Lakebase
# or MLflow) inside an ``async`` route handler before giving up and falling
# back to in-memory storage.  Bounds the blocking call so a hung connection
# can never freeze the single uvicorn event loop — which is what made the
# Apps proxy return 502 on feedback submit.  The initial Lakebase cold-start
# requires WorkspaceClient init + generate_database_credential API call +
# psycopg.connect with SSL + DDL (advisory lock + CREATE TABLE IF NOT EXISTS
# x2 + CREATE INDEX + ALTER TABLE x2); 5 s was too tight and silently timed
# out, so all results fell back to in-memory storage and never persisted.
# 15 s gives the cold-start room while still bounding a genuinely hung call.
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
    available for the ``GET /api/results`` fallback when Lakebase is
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
        # In-memory feedback list (fallback when Lakebase feedback store is down).
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
    best-effort persistence/telemetry (Lakebase, MLflow) inside ``async`` route
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


# ---------------------------------------------------------------------------
# Production port wiring (third-party imports deferred to call-time)
# ---------------------------------------------------------------------------

def _build_lakebase_stores() -> tuple[Any, Any]:
    """Build Lakebase-backed ``ResultStore`` + ``FeedbackStore``.

    Returns ``(result_store, feedback_store)``.  Raises on failure; callers
    catch and degrade to in-memory.  ``databricks-sdk`` and ``psycopg`` are
    imported function-local inside the dependency modules, so importing this
    function does not require them — only *calling* it does.

    Connection parameters come from the explicit Lakebase fallbacks in
    ``app.yaml`` and the ``WorkspaceClient`` identity.  In particular, host
    lookup does not require an endpoint API call; ``WorkspaceClient.postgres``
    is used only for the fresh per-connection credential.
    """
    global _derived_host, _derived_user
    from databricks.sdk import WorkspaceClient
    from db.connection import OAuthConnectionFactory
    from db.stores import LakebaseFeedbackStore, LakebaseResultStore, init_tables

    if not os.environ.get("ENDPOINT_NAME"):
        raise RuntimeError(
            "ENDPOINT_NAME environment variable is required for the Lakebase "
            "connection (set in app.yaml)"
        )
    client = WorkspaceClient()
    # PGHOST is explicit because pg_version=17 Lakebase projects are not
    # registered in the Database Instances API and cannot be looked up there.
    host = (os.environ.get("PGHOST") or "").strip()
    if not host:
        raise RuntimeError(
            "PGHOST environment variable must contain a Lakebase host "
            "(set it in app.yaml)"
        )
    # Prefer the configured service-principal client id as the connecting
    # user (the working permissions-app pattern); fall back to the current
    # workspace user when no SP client id is configured (local dev).
    user = client.config.client_id or client.current_user.me().user_name
    # Surface the derived parameters for /health diagnostics before the
    # connection attempt that may still fail (and reset them on each call so
    # a later re-derivation overwrites a stale value).
    _derived_host = host
    _derived_user = user
    database = os.environ.get("PGDATABASE", "databricks_postgres")
    port = int(os.environ.get("PGPORT", "5432"))
    sslmode = os.environ.get("PGSSLMODE", "require")
    _LOGGER.info(
        "Lakebase connection: host=%s, user=%s, db=%s", host, user, database)
    connect = OAuthConnectionFactory(
        client, os.environ["ENDPOINT_NAME"], host, database, user,
        port=port, sslmode=sslmode,
    )
    init_tables(connect)
    return LakebaseResultStore(connect), LakebaseFeedbackStore(connect)


def _get_stores() -> tuple[Any, Any]:
    """Return cached Lakebase stores ``(result_store, feedback_store)``.

    Lazily initialised on first call. Failures are logged and are not cached,
    allowing a transient credential, endpoint, or database outage to recover.
    """
    global _last_store_error, _stores
    if _stores is not None:
        return _stores
    try:
        _stores = _build_lakebase_stores()
        _last_store_error = None
    except Exception as exc:
        _last_store_error = f"{type(exc).__name__}: {exc}"
        _LOGGER.exception("Lakebase store initialization failed")
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

    Degrades gracefully: if a port cannot be constructed (Lakebase down,
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
        from contracts.models import Bank as _Bank, ParseRequest as _PR
        from graph.state import GraphState

        request = _PR(
            pdf=pdf_bytes,
            filename=filename,
            bank=_Bank(bank),
            request_id=ctx.request_id,
        )
        state = GraphState(request=request)
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
        # Thread the app's cached Lakebase result store into the scorer so each
        # OK verdict is persisted inline (best-effort) and surfaces on the
        # per-parse Results view. _get_stores()[0] is the result store or None
        # when Lakebase is unavailable; None lets the scorer build its own.
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
    Lakebase (best-effort) so ``GET /api/results`` surfaces it inline.

    The ``result_store`` is the app's cached Lakebase store (same one the
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
        # PGHOST and ENDPOINT_NAME are required explicit Lakebase fallbacks;
        # the user is resolved from the WorkspaceClient identity.
        env_status = {
            name: "set" if os.environ.get(name) else "MISSING"
            for name in ("ENDPOINT_NAME", "PGHOST", "PGSSLMODE")
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
            "endpoint_derived_host": _derived_host,
            "derived_user": _derived_user,
        }

    # -- POST /api/parse -------------------------------------------------
    @app.post("/api/parse")
    async def parse(file: UploadFile = File(...), bank: str = Form(...)):
        try:
            Bank(bank)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"unsupported bank: {bank}")

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

        # Persist to Lakebase + log to MLflow — both best-effort.  Each blocking
        # call runs in a worker thread with a bounded wait (``_run_blocking``)
        # so a hung Lakebase credential or MLflow request can never freeze the
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

        # Try Lakebase first (durable persistence).  Blocking calls run in a
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

        # Fall back to in-memory context (when Lakebase is unavailable).
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

        Loads from DBFS override if it exists, else from the bundled file
        (PROMPT_BY_BANK / SCHEMA_BY_BANK).
        """
        try:
            bank_enum = Bank(bank)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"unsupported bank: {bank}")
        from graph.routing import resolve_prompt
        from rules.routing import load_schema_for_bank
        prompt = resolve_prompt(bank_enum)
        schema = load_schema_for_bank(bank_enum)
        return {"prompt": prompt, "schema": schema}

    # -- POST /api/prompt/{bank} ----------------------------------------
    @app.post("/api/prompt/{bank}")
    async def save_prompt_schema(bank: str, body: dict = Body(...)):
        """Save the prompt text and schema JSON to DBFS for a bank.

        Both ``prompt`` and ``schema`` must be present in the body. The
        prompt is written to ``/savesage/prompts/<bank>.txt`` and the schema
        to ``/savesage/schemas/<bank>.json``.
        """
        try:
            bank_enum = Bank(bank)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"unsupported bank: {bank}")
        prompt = body.get("prompt")
        schema = body.get("schema")
        if prompt is None or schema is None:
            raise HTTPException(
                status_code=400,
                detail="both 'prompt' and 'schema' are required",
            )
        if not isinstance(schema, dict):
            raise HTTPException(
                status_code=400,
                detail=f"schema must be a JSON object, got {type(schema).__name__}",
            )
        from harness.dbfs import write_dbfs_text, prompt_dbfs_path, schema_dbfs_path
        prompt_ok = write_dbfs_text(prompt_dbfs_path(bank_enum.value), str(prompt))
        schema_ok = write_dbfs_text(
            schema_dbfs_path(bank_enum.value),
            json.dumps(schema, indent=2, ensure_ascii=False),
        )
        if not prompt_ok or not schema_ok:
            raise HTTPException(
                status_code=502,
                detail="DBFS save failed (SDK unavailable or write error)",
            )
        return {"status": "ok", "bank": bank_enum.value}

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
        """
        try:
            Bank(bank)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"unsupported bank: {bank}")

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
