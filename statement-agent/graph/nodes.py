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

from dataclasses import replace as dc_replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from contracts.models import ExtractionResult, TraceEvent
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
        # Trace failures are telemetry, not data: route them to trace_errors so a
        # broken sink never turns a clean run PARTIAL.
        state.trace_errors.append(f"trace:{name}:{type(exc).__name__}")


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
    """Validate the payload; never raises (failures are collected, not thrown).

    CRITICAL: the validated ``schema_valid`` is propagated into the frozen
    ``ExtractionResult`` via ``dataclasses.replace`` so the object handed to
    ``persist_node`` carries the validated value, not the adapter's initial
    ``False``. Without this, every real Luna extraction would be persisted as
    schema-invalid.
    """
    if state.outcome is Outcome.EXTRACTION_FAILED or state.extraction is None:
        return state
    report = validate_payload(state.extraction.payload)
    state.schema_valid = report.schema_valid
    state.validation_errors = report.all_errors
    # Rebuild the frozen dataclass so the persisted object reflects validation.
    state.extraction = dc_replace(state.extraction, schema_valid=report.schema_valid)
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


# Sections the judge grades, keyed by the payload path they live under. The
# judge (WS5) grades each section INDEPENDENTLY and returns ABSENT_IN_PDF for
# null truth rather than erroring, so a payload that malforms ONE section can
# still usefully grade the others. We gate per-section rather than suppressing
# the whole verdict on a partially-broken parse.
_JUDGE_SECTIONS = ("cards", "transactions", "rewards")


def _judgeable_sections(extraction: ExtractionResult) -> tuple[str, ...]:
    """Return the section names that are structurally present and judgeable.

    A section is judgeable when its payload value has the type the judge adapter
    can serialise: ``cards``/``transactions`` must be lists (the judge iterates
    rows); ``rewards`` must be a dict (scalar fields). A payload that is not a
    dict has no judgeable sections. The judge is invoked if AT LEAST ONE section
    is judgeable, so a partially-broken parse (e.g. cards missing but
    transactions present) still gets the surviving sections graded instead of
    throwing away the whole verdict.
    """
    payload = extraction.payload
    if not isinstance(payload, dict):
        return ()
    sections: list[str] = []
    if isinstance(payload.get("cards"), list):
        sections.append("cards")
    if isinstance(payload.get("transactions"), list):
        sections.append("transactions")
    if isinstance(payload.get("rewards"), dict):
        sections.append("rewards")
    return tuple(sections)


def _meets_judge_minimum_shape(extraction: ExtractionResult) -> bool:
    """True if at least one judged section is structurally present."""
    return bool(_judgeable_sections(extraction))


def judge_node(state: GraphState, deps: NodeDeps) -> GraphState:
    """Run the judge if one is wired and at least one section is judgeable.

    Decision (documented in docs/agent-ws2.md): a validation failure does NOT
    short-circuit the judge -- a schema-invalid-but-structurally-usable payload
    is exactly the kind of output that benefits most from judging. The judge
    grades sections INDEPENDENTLY (cards/transactions/rewards), so a payload
    that malforms ONE section is still judged on the surviving sections rather
    than suppressing the whole verdict. The judge is skipped only when NO
    section is structurally judgeable (or on EXTRACTION_FAILED / no judge wired).
    """
    if state.outcome is Outcome.EXTRACTION_FAILED:
        return state
    if deps.judge is None:
        return state  # no judge wired -> stage skipped, not a failure
    if state.extraction is None:
        return state
    sections = _judgeable_sections(state.extraction)
    if not sections:
        state.judge_skipped_reason = (
            "payload has no structurally judgeable sections "
            "(cards/transactions must be lists, rewards a dict)"
        )
        _trace(deps, state, "judge_skipped", error=state.judge_skipped_reason)
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
    """Set the terminal outcome if no terminal failure was recorded earlier.

    A run with ANY real stage error (e.g. persistence failure) or validation
    errors is PARTIAL at best -- a user must never be told SUCCESS when their
    statement was not saved. Trace failures do NOT count (they are telemetry).
    """
    if state.outcome is not None:
        return state
    if state.validation_errors or state.has_stage_errors:
        state.outcome = Outcome.PARTIAL
    else:
        state.outcome = Outcome.SUCCESS
    _trace(deps, state, "finalize")
    return state
