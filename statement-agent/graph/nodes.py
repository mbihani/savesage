"""LangGraph node functions for the parse pipeline.

Each node takes and returns the :class:`graph.state.GraphState` (LangGraph's
"state modifier" pattern with a reducer-free typed object is used by mutating
and returning the same instance). Nodes depend ONLY on the ABCs from
``contracts/ports.py`` injected through the :class:`NodeDeps` carrier -- never
on psycopg, mlflow, or a concrete judge. This is what lets four workstreams
integrate later without a big-bang: WS3/WS4/WS5 hand in their concrete ports and
the graph keeps its shape.

langgraph is NOT imported here; node functions are plain callables the graph
builder wires up. That keeps this module on the stdlib test path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from contracts.models import TraceEvent
from contracts.ports import (
    ExtractionAdapter,
    FeedbackStore,
    JudgeAdapter,
    ResultStore,
    TraceSink,
)
from graph.routing import resolve_prompt
from graph.state import GraphState, Outcome, Stage
from graph.validation import validate_payload

if TYPE_CHECKING:  # pragma: no cover
    pass


class NodeDeps:
    """Injected port carrier passed to every node.

    Only the extraction adapter is required (it is the core of this workstream).
    ``result_store``, ``trace_sink``, and ``judge`` are optional so the graph
    degrades gracefully: a missing store means persistence is skipped, a missing
    judge means the judge stage is skipped, a missing trace sink means no trace
    events are recorded. The in-memory test fakes provide all four; production
    wiring provides the real ones.
    """

    def __init__(
        self,
        extraction: ExtractionAdapter,
        result_store: ResultStore | None = None,
        trace_sink: TraceSink | None = None,
        judge: JudgeAdapter | None = None,
        feedback_store: FeedbackStore | None = None,
    ) -> None:
        self.extraction = extraction
        self.result_store = result_store
        self.trace_sink = trace_sink
        self.judge = judge
        self.feedback_store = feedback_store


def _trace(deps: NodeDeps, state: GraphState, name: str, *, error: str | None = None) -> None:
    """Record a trace event if a sink is wired (best-effort, never raises)."""
    if deps.trace_sink is None:
        return
    now = datetime.now(UTC)
    try:
        deps.trace_sink.record(TraceEvent(
            request_id=state.request_id,
            name=name,
            started_at=now,
            ended_at=now,
            attributes=state.as_summary(),
            error=error,
        ))
    except Exception as exc:  # pragma: no cover - trace failures must not kill the graph
        state.errors.append(f"trace:{name}:{type(exc).__name__}")


def route_node(state: GraphState, deps: NodeDeps) -> GraphState:
    """Resolve the bank to its prompt. Never raises; a routing failure is terminal."""
    try:
        state.prompt = resolve_prompt(state.request.bank)
        state.stage = Stage.ROUTED
        _trace(deps, state, "route")
    except Exception as exc:
        state.mark_failure(Stage.ROUTED, f"route: {exc}")
        state.outcome = Outcome.EXTRACTION_FAILED
    return state


def extract_node(state: GraphState, deps: NodeDeps) -> GraphState:
    """Call the extraction adapter. A failure here is terminal for this run."""
    if state.outcome is not None:
        return state  # short-circuit: an earlier stage already failed terminally
    try:
        state.extraction = deps.extraction.extract(state.request)
        state.stage = Stage.EXTRACTED
        _trace(deps, state, "extract")
    except Exception as exc:
        state.mark_failure(Stage.EXTRACTED, f"extract: {exc}")
        state.outcome = Outcome.EXTRACTION_FAILED
        _trace(deps, state, "extract", error=str(exc))
    return state


def validate_node(state: GraphState, deps: NodeDeps) -> GraphState:
    """Validate the payload; never raises (failures are collected, not thrown)."""
    if state.outcome is Outcome.EXTRACTION_FAILED or state.extraction is None:
        return state
    report = validate_payload(state.extraction.payload)
    state.schema_valid = report.schema_valid
    state.validation_errors = report.all_errors
    state.stage = Stage.VALIDATED
    _trace(deps, state, "validate", error=None if report.ok else "; ".join(report.all_errors))
    return state


def persist_node(state: GraphState, deps: NodeDeps) -> GraphState:
    """Persist the extraction (and later the verdict) via the injected store."""
    if state.outcome is Outcome.EXTRACTION_FAILED:
        return state
    if state.extraction is not None and deps.result_store is not None:
        try:
            deps.result_store.save_extraction(state.extraction)
            state.stage = Stage.PERSISTED
            _trace(deps, state, "persist_extraction")
        except Exception as exc:
            state.mark_failure(Stage.PERSISTED, f"persist: {exc}")
            _trace(deps, state, "persist_extraction", error=str(exc))
    return state


def judge_node(state: GraphState, deps: NodeDeps) -> GraphState:
    """Run the judge if one is wired and the extraction produced a payload.

    Decision (documented): a validation failure does NOT short-circuit the judge.
    The judge compares extraction fields against PDF ground truth independently of
    schema/rule conformance, and a partial-but-schema-invalid extraction is
    exactly the kind of output that benefits most from judging -- you want to
    know whether the model read the PDF correctly even when it shaped the answer
    wrong. Only a hard EXTRACTION_FAILED outcome skips the judge (there is
    nothing to judge).
    """
    if state.outcome is Outcome.EXTRACTION_FAILED:
        return state
    if deps.judge is None:
        return state  # no judge wired -> stage skipped, not a failure
    if state.extraction is None:
        return state
    try:
        state.verdict = deps.judge.judge(state.request, state.extraction)
        state.stage = Stage.JUDGED
        if deps.result_store is not None:
            deps.result_store.save_verdict(state.verdict)
        _trace(deps, state, "judge")
    except Exception as exc:
        state.mark_failure(Stage.JUDGED, f"judge: {exc}")
        state.outcome = Outcome.JUDGE_FAILED
        _trace(deps, state, "judge", error=str(exc))
    return state


def finalize_node(state: GraphState, deps: NodeDeps) -> GraphState:
    """Set the terminal outcome if no terminal failure was recorded earlier."""
    if state.outcome is not None:
        return state
    # A clean extraction with validation errors is PARTIAL (still persisted + judged);
    # a clean extraction with no errors is SUCCESS.
    state.outcome = Outcome.PARTIAL if state.validation_errors else Outcome.SUCCESS
    _trace(deps, state, "finalize")
    return state
