"""MLflow-backed TraceSink + field-wise feedback + judge logging (workstream 4).

Implements ``contracts.ports.TraceSink.record`` and the wider workstream-4
telemetry surface (feedback, judge verdicts, cost) against MLflow. ``mlflow`` is
imported FUNCTION-LOCAL only, so importing this module never requires mlflow
(per CONTRACTS.md); the pure builders under ``harness/tracing_*.py`` are
stdlib-testable without it.

BEST-EFFORT BOUNDARY (review B1 — airtight):

The single most important property: telemetry must NEVER break a customer's
statement parse. Every PUBLIC method (``record``, ``log_field_feedback``,
``log_judge_verdict``) wraps its ENTIRE body — tree construction, configuration,
payload construction, AND mlflow interaction — in ``_guard``, the outer boundary.
``_guard`` catches ``BaseException``, RE-RAISES ``KeyboardInterrupt`` /
``SystemExit`` / ``GeneratorExit`` (genuine process control — never swallow Ctrl-C),
and on any other failure logs a warning and DISABLES telemetry so a recurring
payload bug fast-fails on subsequent requests rather than spamming warnings.
The inner ``best_effort`` helper guards individual mlflow calls against transient
failures (logging + continue, no disable). A payload-construction bug that raises
BEFORE mlflow is reached is caught by ``_guard`` — proven by tests where payload
construction itself raises (not just the mlflow client).
"""

from __future__ import annotations

import logging
import os
from collections import OrderedDict
from typing import Any, Callable

from contracts.models import FieldFeedback, JudgeVerdict, TraceEvent
from contracts.ports import TraceSink

from .config_ws4 import TracingConfig, get_tracing_config, resolve_experiment_path
from .tracing_cost import cost_attributes, model_attributes, usage_attributes
from .tracing_feedback import build_feedback_payload
from .tracing_judge import build_judge_feedback, verdict_to_metrics
from .tracing_keys import (
    ASSESSMENT_HUMAN,
    ASSESSMENT_LLM_JUDGE,
    SPAN_ATTR_CHAT_USAGE,
    SPAN_ATTR_LLM_COST,
    SPAN_ATTR_MODEL,
    SPAN_ATTR_MODEL_PROVIDER,
)
from .tracing_safe import best_effort
from .tracing_spans import SpanTreeBuilder, SpanOp, redact_telemetry_attributes, span_type_for, to_ns

_LOGGER = logging.getLogger("statement-agent.tracing")

# Exceptions that represent operator/process intent — always propagate.
_CONTROL_EXCEPTIONS = (KeyboardInterrupt, SystemExit, GeneratorExit)


def _import_mlflow() -> Any:
    """Function-local mlflow import (per CONTRACTS.md telemetry rule)."""
    import mlflow  # type: ignore[import-not-found]

    return mlflow


def configure_tracing(config: TracingConfig, mlflow_module: Any = None) -> None:
    """Set the tracking URI, experiment, and tracing/autolog for the agent.

    Best-effort: a failure here only disables telemetry, never the agent. For the
    ``databricks`` tracking URI locally, the Databricks SDK selects the profile
    from ``DATABRICKS_CONFIG_PROFILE``; we apply the configured profile when the
    operator has not already set one. In the Databricks Apps runtime the bound
    experiment resource supplies the tracking URI/auth, so the profile is moot.

    ``mlflow_module`` lets tests inject a fake; production leaves it None so the
    real mlflow is imported function-local here.
    """
    mlf = mlflow_module if mlflow_module is not None else _import_mlflow()
    if config.tracking_uri == "databricks" and config.databricks_profile:
        if "DATABRICKS_CONFIG_PROFILE" not in os.environ:
            os.environ["DATABRICKS_CONFIG_PROFILE"] = config.databricks_profile
    best_effort("mlflow.set_tracking_uri", mlf.set_tracking_uri, config.tracking_uri)
    experiment_path = resolve_experiment_path(config)
    if experiment_path:
        best_effort("mlflow.set_experiment", mlf.set_experiment, experiment_path)
    best_effort("mlflow.tracing.enable", mlf.tracing.enable)

    if config.autolog_langchain:

        def _enable_langchain_autolog() -> None:
            if mlflow_module is not None:
                mlflow_module.langchain.autolog(log_traces=True)
            else:
                from mlflow.langchain import autolog  # function-local

                autolog(log_traces=True)

        best_effort("mlflow.langchain.autolog", _enable_langchain_autolog)


class MLflowTraceSink(TraceSink):
    """Concrete MLflow-backed TraceSink.

    Records the parse pipeline as a TRACE: an outer parse span (CHAIN) containing
    child spans for extraction (LLM), validation (GUARDRAIL), persistence (TOOL),
    and judging (EVALUATOR), connected via ``span_id``/``parent_span_id`` (not
    flattened). Per-field client feedback and judge verdicts are logged as
    trace-bound assessments via ``mlflow.log_feedback``.

    All request-scoped state (pending trees, trace-id map, flushed set) is BOUNDED
    (review B2) so a long-lived Apps process cannot leak memory under sustained
    traffic.
    """

    def __init__(
        self,
        config: TracingConfig | None = None,
        *,
        mlflow_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._config = config or get_tracing_config()
        self._builder = SpanTreeBuilder(
            root_stage="parse",
            max_pending=self._config.max_pending_requests,
            max_flushed=self._config.max_flushed,
            max_events_per_request=self._config.max_events_per_request,
        )
        # Bounded LRU trace-id map (review B2). Prefer pop_trace_id() for explicit
        # handoff with the parse result; this map is the fallback.
        self._trace_ids: "OrderedDict[str, str]" = OrderedDict()
        # Bounded LRU run-id map — one MLflow run per parse request. The run is
        # started when the first trace event arrives (before the root span
        # flushes) so that log_artifact() called from persist_node has an active
        # run to log to. The run carries artifacts, metrics (when judged), and
        # the ``judged`` tag.
        self._run_ids: "OrderedDict[str, str]" = OrderedDict()
        self._mlflow_factory = mlflow_factory  # test seam: inject a fake/raising mlflow
        self._mlflow_client: Any = None
        self._configured = False
        self._disabled = False  # set by _guard on a hard non-control failure

    # --- airtight outer boundary (review B1) ---
    def _guard(self, action: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Outer telemetry boundary: never propagate except control exceptions.

        Catches ``BaseException`` (so payload/RecursionError/MemoryError bugs
        cannot break the parse), RE-RAISES control exceptions (KeyboardInterrupt /
        SystemExit / GeneratorExit — operator intent, never swallowed), and
        DISABLES telemetry after a hard non-control failure so a recurring bug
        fast-fails on subsequent requests.
        """
        if self._disabled:
            return None
        try:
            return fn(*args, **kwargs)
        except _CONTROL_EXCEPTIONS:
            raise
        except BaseException as exc:  # noqa: BLE001 - telemetry must never break parse
            self._disabled = True
            _LOGGER.warning("telemetry DISABLED after hard failure [%s]: %s", action, exc)
            return None

    # --- mlflow access (function-local import or injected factory) ---
    def _mlflow(self) -> Any:
        if self._mlflow_factory is not None:
            return self._mlflow_factory()
        if self._mlflow_client is None:
            self._mlflow_client = _import_mlflow()
        return self._mlflow_client

    def _ensure_configured(self) -> None:
        if self._configured or not self._config.enabled:
            return
        # Route through the factory when injected so tests stay hermetic; the
        # default path imports real mlflow function-local inside configure_tracing.
        if self._mlflow_factory is not None:
            best_effort("mlflow.configure", configure_tracing, self._config, self._mlflow())
        else:
            best_effort("mlflow.configure", configure_tracing, self._config)
        self._configured = True

    # --- run-id management (one MLflow run per parse request) ---
    def _ensure_run(self, request_id: str) -> None:
        """Start an MLflow run for this request if not already started.

        Called on the FIRST trace event for a request (before the root span
        flushes) so that ``log_artifact()`` from persist_node has an active run.
        Best-effort: a failure here means no run (artifacts/metrics are skipped).
        """
        if request_id in self._run_ids:
            return  # already started for this request
        self._ensure_configured()

        def _do() -> None:
            mlf = self._mlflow()
            run = mlf.start_run()
            # start_run returns an ActiveRun; extract the run_id.
            run_id = getattr(getattr(run, "info", None), "run_id", None)
            if run_id is None:
                run_id = str(getattr(run, "run_id", "")) or None
            if run_id is not None:
                self._set_run_id(request_id, run_id)

        best_effort("mlflow.start_run", _do)

    def _set_run_id(self, request_id: str, run_id: str) -> None:
        self._run_ids[request_id] = run_id
        while len(self._run_ids) > self._config.max_trace_ids:
            self._run_ids.popitem(last=False)

    def get_run_id(self, request_id: str) -> str | None:
        """Return the MLflow run_id for a request, or None if not started."""
        return best_effort("tracing.get_run_id", self._run_ids.get, request_id)

    def pop_run_id(self, request_id: str) -> str | None:
        """Return AND remove the run_id — preferred for explicit handoff."""
        return best_effort("tracing.pop_run_id", self._run_ids.pop, request_id, None)

    def _end_run(self, request_id: str) -> None:
        """Finalize the MLflow run for this request (RUNNING → ENDED).

        Called once after the root span flushes — all child spans are ended,
        and artifacts were logged during the graph run (before the root
        arrived). Best-effort: if ``end_run`` fails, MLflow auto-ends the run
        on the next ``start_run()`` or process exit. The run_id is popped from
        the bounded map regardless, so the slot is freed for reuse.
        """
        if request_id not in self._run_ids:
            return  # no run was started (e.g. _ensure_run failed)

        def _do() -> None:
            mlf = self._mlflow()
            mlf.end_run()

        best_effort("mlflow.end_run", _do)
        self.pop_run_id(request_id)

    # --- TraceSink ABC ---
    def record(self, event: TraceEvent) -> None:
        if not self._config.enabled:
            return
        # ENTIRE operation inside the boundary (review B1): feed + configure + flush.
        self._guard("tracing.record", self._record_impl, event)

    def _record_impl(self, event: TraceEvent) -> None:
        # Start an MLflow run for this request on the FIRST trace event (before
        # the root span flushes). This ensures log_artifact() called from
        # persist_node (during the graph, before the root arrives) has an
        # active run to log to. The run carries artifacts, judge metrics, and
        # the ``judged`` tag. Best-effort: a failure here only means no run.
        self._ensure_run(event.request_id)
        ops = self._builder.feed(event)
        if ops is None:
            return  # buffered (no root yet) or a late arrival after flush
        if not ops:
            return  # malformed tree (logged in _build)
        self._ensure_configured()
        self._flush(ops, event.request_id)
        # The root span has flushed — all child spans are ended, and artifacts
        # were logged during the graph run (before the root arrived). Finalize
        # the MLflow run (RUNNING → ENDED) and free the run_id slot.
        self._end_run(event.request_id)

    def _flush(self, ops: list[SpanOp], request_id: str) -> None:
        def _do() -> None:
            mlf = self._mlflow()
            live_by_span: dict[Any, Any] = {}
            root_trace_id: str | None = None
            # Pre-order: start each span after its parent (parent always earlier).
            for op in ops:
                # A child's parent is keyed by the child's parent_span_id (the
                # parent's span_id) — NOT the child's own span_id.
                parent_live = None if op.is_root else live_by_span.get(op.event.parent_span_id)
                live = mlf.start_span_no_context(
                    name=op.event.name,
                    span_type=span_type_for(op.event.name),
                    parent_span=parent_live,
                    start_time_ns=to_ns(op.event.started_at),
                )
                self._apply_attributes(live, op.event)
                if op.event.error:
                    best_effort(
                        "mlflow.span.record_exception",
                        lambda l=live, e=op.event.error: l.record_exception(e),
                    )
                live_by_span[op.event.span_id] = live
                if op.is_root:
                    root_trace_id = getattr(live, "trace_id", None)
            # Reverse pre-order: end children before the root, with explicit times.
            for op in reversed(ops):
                live = live_by_span.get(op.event.span_id)
                if live is None:
                    continue
                status = "ERROR" if op.event.error else "OK"
                best_effort(
                    "mlflow.span.end",
                    lambda l=live, op=op, s=status: l.end(
                        end_time_ns=to_ns(op.event.ended_at), status=s
                    ),
                )
            if root_trace_id:
                self._set_trace_id(request_id, root_trace_id)

        best_effort("mlflow.flush", _do)

    def _apply_attributes(self, live: Any, event: TraceEvent) -> None:
        # Redacted caller-provided attributes (counts/hashes/paths/bools, never PII).
        attrs = redact_telemetry_attributes(dict(event.attributes))
        if attrs:
            best_effort("mlflow.span.set_attributes", lambda: live.set_attributes(attrs))
        # Enrich with MLflow-specific model/usage/cost keys so cost is explicit.
        raw = event.attributes
        model_id = raw.get("model_id") or raw.get("model")
        endpoint = raw.get("endpoint")
        token_usage = raw.get("token_usage") or raw.get("usage")
        for key, value in model_attributes(model_id, endpoint).items():
            best_effort("mlflow.span.model_attr", lambda l=live, k=key, v=value: l.set_attribute(k, v))
        usage = usage_attributes(token_usage)
        if usage is not None:
            best_effort(
                "mlflow.span.usage",
                lambda l=live, u=usage: l.set_attribute(SPAN_ATTR_CHAT_USAGE, u),
            )
        cost = cost_attributes(token_usage, model_id or "", self._config.cost_rates_per_million)
        if cost is not None:
            best_effort(
                "mlflow.span.cost",
                lambda l=live, c=cost: l.set_attribute(SPAN_ATTR_LLM_COST, c),
            )

    # --- trace-id lookup (bounded LRU, review B2) ---
    def _set_trace_id(self, request_id: str, trace_id: str) -> None:
        self._trace_ids[request_id] = trace_id
        while len(self._trace_ids) > self._config.max_trace_ids:
            self._trace_ids.popitem(last=False)

    def get_trace_id(self, request_id: str) -> str | None:
        # Read-only lookup: best_effort (swallow + return None, no disable).
        return best_effort("tracing.get_trace_id", self._get_trace_id_impl, request_id)

    def _get_trace_id_impl(self, request_id: str) -> str | None:
        tid = self._trace_ids.get(request_id)
        if tid is not None:
            self._trace_ids.move_to_end(request_id)  # LRU refresh
        return tid

    def pop_trace_id(self, request_id: str) -> str | None:
        """Return AND remove the trace id — preferred for explicit handoff.

        WS6/WS3 should propagate the trace id WITH the parse result rather than
        rely on this process-global map (review B2). This method takes the id out
        of the bounded LRU so it cannot leak.
        """
        return best_effort("tracing.pop_trace_id", self._trace_ids.pop, request_id, None)

    def abandon(self, request_id: str) -> None:
        """Drop all telemetry state for a request whose parse crashed (review B2).

        This is a cleanup method called on error paths — it must never raise, so
        an additional exception cannot mask the original failure. It works even
        when telemetry is disabled (to clean up leftover state).
        """
        try:
            self._builder.abandon(request_id)
            self._trace_ids.pop(request_id, None)
            self._run_ids.pop(request_id, None)
        except _CONTROL_EXCEPTIONS:
            raise
        except BaseException as exc:  # noqa: BLE001 - cleanup must never raise
            _LOGGER.warning("telemetry abandon failed for %s: %s", request_id, exc)

    # --- field-wise client feedback (requirement 3) ---
    def log_field_feedback(self, feedback: FieldFeedback, trace_id: str | None = None) -> None:
        if not self._config.enabled:
            return
        # ENTIRE operation inside the boundary (review B1).
        self._guard("tracing.log_field_feedback", self._feedback_impl, feedback, trace_id)

    def _feedback_impl(self, feedback: FieldFeedback, trace_id: str | None) -> None:
        tid = trace_id or self.get_trace_id(feedback.request_id)
        if not tid:
            _LOGGER.warning(
                "telemetry feedback dropped (no trace_id for %s); persist trace_id with the result",
                feedback.request_id,
            )
            return
        payload = build_feedback_payload(
            feedback,
            redact_pii=self._config.redact_pii_values,
            log_nonpii=self._config.log_nonpii_values_raw,
            hmac_key=self._config.feedback_hmac_key,
        )
        self._ensure_configured()

        def _do() -> None:
            mlf = self._mlflow()
            from mlflow.entities import AssessmentSource  # function-local

            mlf.log_feedback(
                trace_id=tid,
                name=payload.name,
                value=payload.value,
                source=AssessmentSource(payload.source_type, payload.source_id),
                rationale=payload.rationale,
                metadata=payload.metadata,
            )

        best_effort("mlflow.log_feedback", _do)

    # --- judge verdict (requirement 4) ---
    def log_judge_verdict(self, verdict: JudgeVerdict, trace_id: str | None = None) -> None:
        if not self._config.enabled:
            return
        # ENTIRE operation inside the boundary (review B1).
        self._guard("tracing.log_judge_verdict", self._judge_impl, verdict, trace_id)

    def _judge_impl(self, verdict: JudgeVerdict, trace_id: str | None) -> None:
        tid = trace_id or self.get_trace_id(verdict.request_id)
        if not tid:
            _LOGGER.warning(
                "telemetry judge verdict dropped (no trace_id for %s)",
                verdict.request_id,
            )
            return
        payload = build_judge_feedback(verdict)
        self._ensure_configured()

        def _do() -> None:
            mlf = self._mlflow()
            from mlflow.entities import AssessmentSource  # function-local

            mlf.log_feedback(
                trace_id=tid,
                name=payload["name"],
                value=payload["value"],
                source=AssessmentSource(payload["source_type"], payload["source_id"]),
                rationale=payload["rationale"],
                metadata=payload["metadata"],
            )

        best_effort("mlflow.log_judge_verdict", _do)

    # Convenience for WS6/WS3: also return metrics for run-side logging if desired.
    def judge_metrics(self, verdict: JudgeVerdict) -> dict[str, float]:
        """Compute judge metrics for run-side logging if desired.

        This is a telemetry-only computation; a failure here returns an empty
        dict rather than propagating (review B1).
        """
        return self._guard("tracing.judge_metrics", verdict_to_metrics, verdict) or {}

    # --- artifact logging (PDF persistence on the trace) ---
    def log_artifact(self, data: bytes, path: str) -> None:
        """Log a binary artifact (e.g. the source PDF) on the current trace.

        Writes ``data`` to a temporary file and calls ``mlflow.log_artifact``
        so the post-hoc judge can download the PDF from the trace later.
        Best-effort (review B1): a failure here only disables telemetry.
        """
        if not self._config.enabled:
            return
        self._guard("tracing.log_artifact", self._log_artifact_impl, data, path)

    def _log_artifact_impl(self, data: bytes, path: str) -> None:
        import tempfile
        from pathlib import Path

        self._ensure_configured()

        def _do() -> None:
            mlf = self._mlflow()
            # log_artifact takes a local file path and uses the FILENAME as the
            # artifact name. Write bytes to a temp dir with the correct name so
            # the artifact is stored as "statement.pdf" (not a random temp name).
            filename = Path(path).name
            parent = str(Path(path).parent)
            artifact_dir = parent if parent != "." else None
            with tempfile.TemporaryDirectory() as tmpdir:
                filepath = Path(tmpdir) / filename
                filepath.write_bytes(data)
                mlf.log_artifact(str(filepath), artifact_path=artifact_dir)

        best_effort("mlflow.log_artifact", _do)


def build_trace_sink(config: TracingConfig | None = None) -> MLflowTraceSink:
    """Construct the concrete TraceSink used by the agent.

    Fail-safe: if construction raises (e.g. invalid env-var config), returns a
    disabled no-op sink rather than propagating — app startup must not break
    (review B1).
    """
    try:
        return MLflowTraceSink(config)
    except _CONTROL_EXCEPTIONS:
        raise
    except BaseException as exc:  # noqa: BLE001 - construction must never break startup
        _LOGGER.warning("telemetry sink construction failed; returning disabled no-op: %s", exc)
        return MLflowTraceSink(TracingConfig(enabled=False))
