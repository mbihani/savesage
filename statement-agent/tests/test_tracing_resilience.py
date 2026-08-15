"""Stdlib resilience tests for the MLflow TraceSink (WS4, requirement 6 + acceptance B).

These prove telemetry failure NEVER breaks the parse path. They use an injected
``mlflow_factory`` (no real mlflow needed) that raises, returns a broken object,
or returns None — and asserts ``record`` / ``log_field_feedback`` /
``log_judge_verdict`` all complete without propagating. The gate at the bottom
runs the full suite including these.
"""

from datetime import UTC, datetime
import unittest

from contracts.models import (
    Bank,
    ComparisonOutcome,
    ExtractionResult,
    FieldComparison,
    FieldFeedback,
    FieldScope,
    JudgeVerdict,
    MatchMethod,
    ParseRequest,
    TraceEvent,
    TokenUsage,
)
from harness.config_ws4 import TracingConfig
from harness.tracing import MLflowTraceSink


def _evt(name, sid, parent=None, attrs=None, error=None, rid="req-1", offset=0):
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    started = datetime.fromtimestamp(base.timestamp() + offset, tz=UTC)
    ended = datetime.fromtimestamp(base.timestamp() + offset + 1, tz=UTC)
    return TraceEvent(
        request_id=rid, name=name, started_at=started, ended_at=ended,
        attributes=attrs or {}, error=error, span_id=sid, parent_span_id=parent,
    )


def _config(enabled=True):
    return TracingConfig(
        enabled=enabled, tracking_uri="databricks", databricks_profile="fevm-stable",
        experiment_path="/Shared/savesage/statement-agent", autolog_langchain=False,
        cost_rates_per_million={"databricks-gpt-5-6-luna": {"input": 0.0, "output": 0.0}},
    )


class _RaisingMLflow:
    """A fake mlflow module whose every method raises."""

    class _RaisingAttr:
        def __getattr__(self, name):
            raise RuntimeError(f"mlflow broken: {name}")

        def __call__(self, *a, **k):
            raise RuntimeError("mlflow broken: call")

    start_span_no_context = start_span = set_tracking_uri = set_experiment = tracing = _RaisingAttr()
    log_feedback = _RaisingAttr()

    class tracing:  # noqa: N801 - mimic mlflow.tracing namespace
        enable = staticmethod(lambda: (_ for _ in ()).throw(RuntimeError("tracing.enable broken")))

    class langchain:  # noqa: N801
        @staticmethod
        def autolog(**kw):
            raise RuntimeError("langchain.autolog broken")


def _raising_factory():
    return _RaisingMLflow


class ResilienceTest(unittest.TestCase):
    def test_record_with_raising_mlflow_does_not_raise(self):
        sink = MLflowTraceSink(_config(), mlflow_factory=_raising_factory)
        # Feed children then root to trigger a flush (the mlflow-touching path).
        sink.record(_evt("extraction", "s-extract", parent="s-parse",
                         attrs={"model_id": "databricks-gpt-5-6-luna",
                                "token_usage": TokenUsage(10, 5, 15),
                                "endpoint": "databricks-gpt-5-6-luna",
                                "schema_valid": True}))
        sink.record(_evt("parse", "s-parse", parent=None,
                         attrs={"bank": "HDFC", "schema_valid": True}))
        # No exception propagated -> pass.

    def test_record_with_missing_span_id_children_still_does_not_raise(self):
        sink = MLflowTraceSink(_config(), mlflow_factory=_raising_factory)
        # child with span_id but a parent that won't be in live_by_span; root no span_id
        sink.record(_evt("extraction", None, parent=None, attrs={}))
        # root with no span_id triggers flush with just itself
        sink.record(_evt("parse", None, parent=None, attrs={"bank": "HDFC"}))

    def test_log_field_feedback_with_raising_mlflow_does_not_raise(self):
        sink = MLflowTraceSink(_config(), mlflow_factory=_raising_factory)
        # Prime the trace-id map so we reach the mlflow call (not the early no-trace return).
        sink._trace_ids["req-1"] = "tr-fake"
        fb = FieldFeedback("req-1", "cards.0.cardMeta.cardDisplayName",
                           "Jane Doe", "Jane D", False, "synthetic-client",
                           datetime.now(UTC))
        sink.log_field_feedback(fb)
        # No exception -> pass.

    def test_log_field_feedback_without_trace_id_drops_silently(self):
        sink = MLflowTraceSink(_config(), mlflow_factory=_raising_factory)
        fb = FieldFeedback("req-2", "cards.0.cardMeta.lastFourDigit",
                           "1234", "4321", False, "synthetic-client",
                           datetime.now(UTC))
        # No trace_id for req-2 -> logs a warning, does not call mlflow, does not raise.
        sink.log_field_feedback(fb)

    def test_log_judge_verdict_with_raising_mlflow_does_not_raise(self):
        sink = MLflowTraceSink(_config(), mlflow_factory=_raising_factory)
        sink._trace_ids["req-1"] = "tr-fake"
        v = JudgeVerdict(
            "req-1", "databricks-claude-opus-5",
            (FieldComparison("rewards.closingPoints", 100, 100,
                             ComparisonOutcome.AGREE, FieldScope.SCALAR),),
            50.0,
        )
        sink.log_judge_verdict(v)

    def test_disabled_tracing_is_a_total_noop(self):
        # When disabled, record must not even buffer/flush — zero side effects.
        sink = MLflowTraceSink(_config(enabled=False), mlflow_factory=_raising_factory)
        calls = []

        class _TrackingFactory:
            def __getattr__(self, name):
                calls.append(name)
                raise AssertionError("mlflow must not be touched when disabled")

        sink._mlflow_factory = _TrackingFactory
        sink.record(_evt("parse", "s-parse", parent=None, attrs={"bank": "HDFC"}))
        self.assertEqual(calls, [])

    def test_import_safety_without_mlflow(self):
        """Importing harness.tracing must not require mlflow (CONTRACTS.md)."""
        import importlib

        import harness.tracing as mod
        importlib.reload(mod)
        self.assertTrue(hasattr(mod, "MLflowTraceSink"))


if __name__ == "__main__":
    unittest.main()
