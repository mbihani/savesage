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

from contracts.models import ExtractionResult, TraceEvent, bank_name
from contracts.ports import (
    ExtractionAdapter,
    JudgeAdapter,
    TraceSink,
)
from graph.routing import effective_bank, get_prompt_version, resolve_prompt
from graph.state import GraphState, Outcome, Stage
from graph.validation import load_schema_for_bank, validate_payload

if TYPE_CHECKING:  # pragma: no cover
    pass


class NodeDeps:
    """Injected port carrier passed to every node.

    Only the extraction adapter is required (it is the core of this workstream).
    ``trace_sink`` and ``judge`` are optional so the graph degrades gracefully:
    a missing judge means the judge stage is skipped, a missing trace sink means
    no trace events are recorded. The in-memory test fakes provide all three;
    production wiring provides the real ones.

    The database persistence layer has been removed — the agent returns
    parsed JSON only and the client persists. The PDF + extraction are still
    logged as MLflow artifacts (by ``finalize_node``) so the post-hoc judge
    can re-read them when scoring the trace.
    """

    def __init__(
        self,
        extraction: ExtractionAdapter,
        trace_sink: TraceSink | None = None,
        judge: JudgeAdapter | None = None,
    ) -> None:
        self.extraction = extraction
        self.trace_sink = trace_sink
        self.judge = judge


def _trace(
    deps: NodeDeps,
    state: GraphState,
    name: str,
    *,
    error: str | None = None,
    extra_attrs: dict[str, Any] | None = None,
    inputs: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
) -> None:
    """Record a trace event if a sink is wired (best-effort, never raises).

    Every child event gets a deterministic ``span_id`` (``{request_id}:{name}``)
    and a ``parent_span_id`` linking it to the parse root (``{request_id}:parse``).
    This allows :class:`harness.tracing.SpanTreeBuilder` to construct the span
    tree correctly — the root ``"parse"`` event is emitted by :func:`graph.graph.
    run_graph` after the pipeline completes.

    ``extra_attrs`` are merged into the summary attributes for this event only
    (e.g. the extract span carries ``model_id``/``token_usage`` so MLflow can
    attribute model + cost on the LLM span — ``state.as_summary()`` is a
    payload-free snapshot that omits them).

    ``inputs`` / ``outputs`` carry the actual payload data for this span.  The
    MLflow sink passes them to ``span.set_inputs()`` / ``span.set_outputs()``
    (after PII redaction) so the trace view shows the real extraction data —
    bank, model, the GT_SCHEMA payload, validation result — not just metadata
    counts.  Without these the spans are created but look empty.
    """
    if deps.trace_sink is None:
        return
    now = datetime.now(UTC)
    attrs = state.as_summary()
    if extra_attrs:
        attrs = {**attrs, **extra_attrs}
    try:
        deps.trace_sink.record(TraceEvent(
            request_id=state.request_id,
            name=name,
            started_at=now,
            ended_at=now,
            attributes=attrs,
            error=error,
            span_id=f"{state.request_id}:{name}",
            parent_span_id=f"{state.request_id}:parse",
            inputs=inputs,
            outputs=outputs,
        ))
    except Exception as exc:  # pragma: no cover - trace failures must not kill the graph
        # Trace failures are telemetry, not data: route them to trace_errors so a
        # broken sink never turns a clean run PARTIAL.
        state.trace_errors.append(f"trace:{name}:{type(exc).__name__}")


def _extract_telemetry(state: GraphState) -> dict[str, Any]:
    """Model/usage attrs for the extract span, sourced from the ExtractionResult.

    ``as_summary()`` is a payload-free snapshot (no model_id/usage), so without
    these extras the LLM span would carry no model or token-count attributes and
    MLflow could not attribute cost. The endpoint (AI-Gateway serving endpoint
    name) gates the ``mlflow.model.provider`` attribute.
    """
    if state.extraction is None:
        return {}
    attrs: dict[str, Any] = {
        "model_id": state.extraction.model_id,
        "latency_ms": state.extraction.latency_ms,
    }
    tu = state.extraction.token_usage
    if tu is not None:
        # Plain dict (not the dataclass) so redact_telemetry_attributes recurses
        # into it and MLflow can serialise it; usage_attributes/cost_attributes
        # both accept a Mapping.
        attrs["token_usage"] = {
            "input_tokens": tu.input_tokens,
            "output_tokens": tu.output_tokens,
            "total_tokens": tu.total_tokens,
        }
    try:
        from config import get_settings  # function-local; nodes.py stays stdlib-importable

        attrs["endpoint"] = get_settings().extraction_endpoint
    except Exception:  # noqa: BLE001 - endpoint is optional (gates provider attr only)
        pass
    return attrs


def route_node(state: GraphState, deps: NodeDeps) -> GraphState:
    """Resolve the bank to its prompt. Never raises; a routing failure is terminal.

    When ``state.prompt`` is already set (e.g. a custom prompt override passed
    by the ``/api/parse-custom`` endpoint), the resolution is skipped — the
    pre-set prompt is used directly and version-tagged. This lets the custom
    parse path trace the ACTUAL prompt sent, not the bank default.
    """
    try:
        if state.prompt is None:
            state.prompt = resolve_prompt(state.request.bank)
        # If the bank fell back to the GENERIC prompt (it is neither a known
        # Bank enum nor a registered dynamic bank), normalise the effective bank
        # to GENERIC so downstream nodes, traces, and the API response all
        # report the bank that was actually used -- not the unknown name the
        # caller passed. ``resolve_prompt`` already served the generic prompt;
        # this just makes the bank identity explicit. Known built-ins and
        # registered dynamic banks are left untouched.
        effective = effective_bank(state.request.bank)
        if bank_name(effective) != bank_name(state.request.bank):
            state.request = dc_replace(state.request, bank=effective)
        # Stable version id for the resolved prompt; stored on state so the
        # extract span and the MLflow run can be tagged without recomputing.
        # Pass the ALREADY-RESOLVED ``state.prompt`` (not the bank) so the
        # version hashes exactly the text that was traced/sent -- resolving the
        # prompt twice (once for the trace text, once for the version) would
        # re-read the file and could disagree if it was edited between reads.
        state.prompt_version = get_prompt_version(state.prompt, state.request.bank)
        state.stage = Stage.ROUTED
        _trace(deps, state, "route",
               # ``prompt_version`` is a span attribute so the route span records
               # WHICH prompt was selected (the run param/tag below also uses it).
               extra_attrs={"prompt_version": state.prompt_version},
               inputs={"bank": bank_name(state.request.bank)},
               outputs={"bank": bank_name(state.request.bank),
                        "prompt_resolved": True,
                        "prompt_version": state.prompt_version})
    except Exception as exc:
        state.mark_failure(Stage.ROUTED, f"route: {exc}")
        state.outcome = Outcome.EXTRACTION_FAILED
        _trace(deps, state, "route",
               inputs={"bank": bank_name(state.request.bank)},
               error=str(exc))
    return state


def extract_node(state: GraphState, deps: NodeDeps) -> GraphState:
    """Call the extraction adapter. A failure here is terminal for this run."""
    if state.outcome is not None:
        return state  # short-circuit: an earlier stage already failed terminally
    try:
        state.extraction = deps.extraction.extract(state.request)
        state.stage = Stage.EXTRACTED
        # ``prompt`` is the actual resolved prompt text sent to Luna. It is
        # template text (not customer data), so it is safe to trace; the PII
        # scrubber applies a LARGER truncation cap to the "prompt" key than to
        # ordinary strings so the prompt is actually VISIBLE in the trace view
        # (the default 200-char cap would show only the prompt's title line).
        extract_attrs = _extract_telemetry(state)
        if state.prompt_version is not None:
            extract_attrs["prompt_version"] = state.prompt_version
        _trace(deps, state, "extract",
               extra_attrs=extract_attrs,
               inputs={"bank": bank_name(state.request.bank),
                       "model_id": state.extraction.model_id,
                       "prompt": state.prompt},
               outputs={"extraction": state.extraction.payload,
                        "model_id": state.extraction.model_id,
                        "schema_valid": state.extraction.schema_valid,
                        "latency_ms": state.extraction.latency_ms,
                        "raw_response_id": state.extraction.raw_response_id})
    except Exception as exc:
        state.mark_failure(Stage.EXTRACTED, f"extract: {exc}")
        state.outcome = Outcome.EXTRACTION_FAILED
        _trace(deps, state, "extract",
               inputs={"bank": bank_name(state.request.bank)},
               error=str(exc))
    return state


def validate_node(state: GraphState, deps: NodeDeps) -> GraphState:
    """Validate the payload; never raises (failures are collected, not thrown).

    CRITICAL: the validated ``schema_valid`` is propagated into the frozen
    ``ExtractionResult`` via ``dataclasses.replace`` so the object handed to
    ``finalize_node`` (and logged as the ``extraction.json`` artifact) carries
    the validated value, not the adapter's initial ``False``. Without this,
    every real Luna extraction would be logged as schema-invalid.
    """
    if state.outcome is Outcome.EXTRACTION_FAILED or state.extraction is None:
        return state
    # A custom re-run sends schema_override to the model, so validation must use
    # that exact schema too. Normal parses retain the existing per-bank lookup.
    schema = (
        state.schema_override
        if state.schema_override is not None
        else load_schema_for_bank(state.request.bank)
    )
    report = validate_payload(state.extraction.payload, schema)
    state.schema_valid = report.schema_valid
    state.validation_errors = report.all_errors
    # An internal validation error (validator bug, not a bad payload) must
    # influence the terminal outcome. all_errors includes it (so it flows into
    # validation_errors), but also record it as a stage error so finalize_node
    # produces PARTIAL via has_stage_errors as a belt-and-suspenders backstop --
    # a validator crash must NEVER be silently swallowed into SUCCESS.
    if report.internal_error is not None:
        state.mark_failure(Stage.VALIDATED, f"validate: {report.internal_error}")
    # Rebuild the frozen dataclass so the persisted object reflects validation.
    state.extraction = dc_replace(state.extraction, schema_valid=report.schema_valid)
    state.stage = Stage.VALIDATED
    _trace(deps, state, "validate",
           error=None if report.ok else "; ".join(report.all_errors),
           inputs={"extraction": state.extraction.payload},
           outputs={"schema_valid": report.schema_valid,
                    "validation_errors": report.all_errors})
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
        _trace(deps, state, "judge",
               inputs={"request_id": state.request_id},
               outputs={"judge_model_id": state.verdict.judge_model_id,
                        "n_comparisons": len(state.verdict.comparisons),
                        "verdict_summary": state.verdict.summary})
    except Exception as exc:
        state.mark_failure(Stage.JUDGED, f"judge: {exc}")
        state.outcome = Outcome.JUDGE_FAILED
        _trace(deps, state, "judge",
               inputs={"request_id": state.request_id},
               error=str(exc))
    return state


def finalize_node(state: GraphState, deps: NodeDeps) -> GraphState:
    """Set the terminal outcome if no terminal failure was recorded earlier.

    A run with validation errors (schema/rule violations) OR an internal
    validation error is PARTIAL at best -- a user must never be told SUCCESS
    when the validator itself misbehaved. Trace failures do NOT count (they
    are telemetry).

    Also logs the source PDF + extraction as MLflow artifacts (best-effort) so
    the post-hoc judge can re-read them when scoring this trace. This was
    previously done in the now-removed persist stage; the pipeline no longer
    has a persist step, but the scorer still depends on these artifacts.
    """
    # Log the source PDF + extraction as MLflow artifacts so the post-hoc
    # judge can re-read them when scoring this trace. Best-effort: never raises.
    if state.extraction is not None and deps.trace_sink is not None:
        try:
            import json
            from pathlib import Path
            pdf = state.request.pdf.read_bytes() if isinstance(state.request.pdf, Path) else state.request.pdf
            deps.trace_sink.log_artifact(pdf, "statement.pdf")
            # Log the extraction payload + bank so the scorer can reconstruct
            # a ParseRequest and ExtractionResult without a live store.
            extraction_meta = {
                "request_id": state.request_id,
                "bank": bank_name(state.request.bank),
                "payload": state.extraction.payload,
                "model_id": state.extraction.model_id,
                "schema_valid": state.extraction.schema_valid,
            }
            deps.trace_sink.log_artifact(
                json.dumps(extraction_meta).encode("utf-8"), "extraction.json",
            )
        except Exception:  # pragma: no cover - artifact logging must never break the parse
            pass

    if state.outcome is not None:
        return state
    if state.validation_errors or state.has_stage_errors:
        state.outcome = Outcome.PARTIAL
    else:
        state.outcome = Outcome.SUCCESS
    _trace(deps, state, "finalize",
           outputs={"outcome": state.outcome.value,
                    "extraction": state.extraction.payload if state.extraction else None,
                    "schema_valid": state.schema_valid})
    return state
