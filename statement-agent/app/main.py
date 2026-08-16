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
_trace_sink: Any = None
_LOGGER = logging.getLogger("statement-agent.app")

# Maximum number of traces the post-hoc judge will score in one evaluation.
# Guards against a caller requesting an expensive sweep via the API.
MAX_SAMPLE_SIZE = 50

# Max seconds to wait for a best-effort persistence/telemetry call (Lakebase
# or MLflow) inside an ``async`` route handler before giving up and falling
# back to in-memory storage.  Bounds the blocking call so a hung connection
# can never freeze the single uvicorn event loop — which is what made the
# Apps proxy return 502 on feedback submit.
_PERSIST_TIMEOUT = 5.0

# Cache for the most recent judge evaluation result (populated by
# ``POST /api/run-judge`` and returned by ``GET /api/judge-results``).
# Process-scoped: a restart clears it, which is fine for a demo.
_judge_result_cache: Optional[dict[str, Any]] = None

# Guards concurrent judge evaluations — only one background evaluation at a time.
_judge_running = False
_judge_lock = threading.Lock()


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
    """
    from databricks.sdk import WorkspaceClient
    from db.connection import OAuthConnectionFactory
    from db.stores import LakebaseFeedbackStore, LakebaseResultStore, init_tables

    required = ("ENDPOINT_NAME", "PGHOST", "PGUSER", "PGDATABASE")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "Lakebase database resource did not inject required environment "
            f"variables: {', '.join(missing)}"
        )
    client = WorkspaceClient()
    connect = OAuthConnectionFactory(
        client,
        os.environ["ENDPOINT_NAME"],
        os.environ["PGHOST"],
        os.environ["PGDATABASE"],
        os.environ["PGUSER"],
        port=int(os.environ.get("PGPORT", "5432")),
        sslmode=os.environ.get("PGSSLMODE", "require"),
    )
    init_tables(connect)
    return LakebaseResultStore(connect), LakebaseFeedbackStore(connect)


def _get_stores() -> tuple[Any, Any]:
    """Return cached Lakebase stores ``(result_store, feedback_store)``.

    Lazily initialised on first call. Failures are logged and are not cached,
    allowing a transient credential, endpoint, or database outage to recover.
    """
    global _stores
    if _stores is not None:
        return _stores
    try:
        _stores = _build_lakebase_stores()
    except Exception:
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


def _build_deps(ctx: RequestContext, state: Any = None) -> Any:
    """Build production :class:`NodeDeps` with real ports wired.

    Degrades gracefully: if a port cannot be constructed (Lakebase down,
    MLflow unavailable), the graph skips that stage rather than failing the
    entire parse. The judge no longer runs inline — it is a post-hoc
    evaluation over MLflow traces (see ``judge/scorer.py``), so no judge
    adapter is constructed here.

    ``state`` is the live :class:`GraphState` — passed to
    :class:`_ProgressTraceSink` so it can push per-item SSE events
    when the extract node completes.
    """
    from graph.nodes import NodeDeps
    from harness.extraction_adapter import LunaExtractionAdapter

    extraction = LunaExtractionAdapter()

    result_store, feedback_store = _get_stores()
    trace_sink = _ProgressTraceSink(_get_trace_sink(), ctx, state)

    return NodeDeps(
        extraction=extraction,
        result_store=result_store,
        trace_sink=trace_sink,
        feedback_store=feedback_store,
    )


def _run_parse(ctx: RequestContext, pdf_bytes: bytes, filename: str, bank: str) -> None:
    """Background-thread entry point: run the LangGraph parse pipeline.

    Pushes SSE events into ``ctx.events`` as each node completes (via the
    :class:`_ProgressTraceSink` that wraps the real MLflow sink).  The trace
    sink pushes individual ``extraction_item`` events when the extract node
    completes — *during* the graph run, not after — so the frontend renders
    per-item results live as they arrive. The judge no longer runs inline.

    After the graph returns, this function stores the extraction snapshot on
    ``ctx`` for the ``GET /api/results`` fallback and pushes the terminal
    ``complete`` (or ``error``) event.
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

        ctx.push("start", {
            "request_id": ctx.request_id,
            "bank": bank,
            "filename": filename,
            "stages": list(PIPELINE_STAGES),
        })

        deps = _build_deps(ctx, state)

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
        result = run_judge_evaluation(sample_size=sample_size)
        _judge_result_cache = result
    except Exception as exc:
        _judge_result_cache = {
            "count_judged": 0,
            "count_errors": 1,
            "errors": [{"error": f"{type(exc).__name__}: {exc}"}],
            "overall_strict": None,
            "overall_narration_forgiven": None,
            "per_field": {},
            "per_bank": {},
            "_status": "error",
        }
        _LOGGER = __import__("logging").getLogger("statement-agent.app")
        _LOGGER.warning("judge evaluation failed: %s", exc)
    finally:
        with _judge_lock:
            _judge_running = False


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
    def health() -> dict[str, str]:
        return {"status": "ok"}

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

        # Only one evaluation at a time.
        with _judge_lock:
            if _judge_running:
                raise HTTPException(status_code=409, detail="evaluation already running")
            _judge_running = True

        thread = threading.Thread(
            target=_run_judge_evaluation_bg,
            args=(sample_size,),
            daemon=True,
        )
        thread.start()

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
