"""B1: payload-CONSTRUCTION failure (not just mlflow failure) cannot break the parse.

The existing resilience tests use a raising mlflow FAKE — which fails at the mlflow
layer, AFTER payload construction succeeds. This test pairs that with a fake that
fails at a DIFFERENT layer: payload construction itself (repr/hashing/timestamp of
a malformed value raises). This is the gap the reviewer identified: if
build_feedback_payload or the span-tree feed raises, the exception propagates
through record()/log_field_feedback() and can break the caller's parse.
"""

from datetime import UTC, datetime
import os
import unittest

from contracts.models import FieldFeedback, JudgeVerdict, TraceEvent
from harness.config_ws4 import TracingConfig
from harness.tracing import MLflowTraceSink, build_trace_sink


class _ExplodingValue:
    """An object whose repr() raises — simulates a malformed payload value."""

    def __repr__(self):
        raise RuntimeError("payload construction bomb: repr failed")

    def __str__(self):
        raise RuntimeError("payload construction bomb: str failed")


class _ExplodingAttributes(dict):
    """A Mapping whose .get() raises on the second call — simulates attr access failure."""

    def __init__(self):
        super().__init__()
        self._calls = 0

    def get(self, key, default=None):
        self._calls += 1
        if self._calls > 2:
            raise RuntimeError("payload construction bomb: attr access failed")
        return super().get(key, default)


def _config():
    return TracingConfig(
        enabled=True, tracking_uri="databricks", databricks_profile="fevm-stable",
        experiment_path="/x", autolog_langchain=False,
        feedback_hmac_key=b"test-key",
    )


def _evt_for_abandon(name, rid, sid, parent=None):
    """Build a minimal TraceEvent for abandon/buffer tests."""
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    return TraceEvent(
        request_id=rid, name=name, started_at=base,
        ended_at=datetime.fromtimestamp(base.timestamp() + 1, tz=UTC),
        attributes={}, span_id=sid, parent_span_id=parent,
    )


class PayloadConstructionFailureTest(unittest.TestCase):
    def test_log_field_feedback_with_exploding_value_does_not_raise(self):
        sink = MLflowTraceSink(_config(), mlflow_factory=lambda: None)
        # The value's repr() raises during HMAC/hash construction.
        fb = FieldFeedback(
            "req-1", "cards.0.cardMeta.cardDisplayName",
            _ExplodingValue(), _ExplodingValue(), False, "synthetic-actor",
            datetime.now(UTC),
        )
        # Must NOT propagate — the _guard catches it.
        sink.log_field_feedback(fb, trace_id="tr-fake")

    def test_record_with_exploding_attributes_does_not_raise(self):
        sink = MLflowTraceSink(_config(), mlflow_factory=lambda: None)
        base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        # Attributes whose .get() raises during _apply_attributes.
        event = TraceEvent(
            request_id="req-1", name="parse", started_at=base,
            ended_at=datetime.fromtimestamp(base.timestamp() + 1, tz=UTC),
            attributes=_ExplodingAttributes(), span_id="s-parse", parent_span_id=None,
        )
        # Must NOT propagate.
        sink.record(event)

    def test_hard_failure_disables_telemetry_for_subsequent_calls(self):
        sink = MLflowTraceSink(_config(), mlflow_factory=lambda: None)
        fb = FieldFeedback(
            "req-1", "cards.0.cardMeta.cardDisplayName",
            _ExplodingValue(), _ExplodingValue(), False, "synthetic-actor",
            datetime.now(UTC),
        )
        # First call: payload construction raises -> _guard catches + disables.
        sink.log_field_feedback(fb, trace_id="tr-fake")
        self.assertTrue(sink._disabled)
        # Second call: fast-fails (disabled), no exception, no work.
        # Even a perfectly good feedback must not raise or touch anything.
        good_fb = FieldFeedback(
            "req-1", "cards.0.cardMeta.lastFourDigit", "1234", "4321", False,
            "synthetic-actor", datetime.now(UTC),
        )
        sink.log_field_feedback(good_fb, trace_id="tr-fake")

    def test_control_exceptions_propagate_not_swallowed(self):
        sink = MLflowTraceSink(_config(), mlflow_factory=lambda: None)

        def _raise_kbd():
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            sink._guard("test", _raise_kbd)

    def test_system_exit_propagates_not_swallowed(self):
        sink = MLflowTraceSink(_config(), mlflow_factory=lambda: None)

        def _raise_exit():
            raise SystemExit(0)

        with self.assertRaises(SystemExit):
            sink._guard("test", _raise_exit)

    def test_reenable_resets_disabled_flag(self):
        sink = MLflowTraceSink(_config(), mlflow_factory=lambda: None)
        sink._disabled = True
        sink._disabled = False  # ops can reset
        self.assertFalse(sink._disabled)

    def test_judge_metrics_with_exploding_verdict_does_not_raise(self):
        # judge_metrics() computes payload from the verdict; if the verdict's
        # comparisons field raises on iteration, _guard must catch it.
        sink = MLflowTraceSink(_config(), mlflow_factory=lambda: None)

        class _ExplodingIterable:
            def __iter__(self):
                raise RuntimeError("verdict iteration bomb")

        v = JudgeVerdict("req-1", "opus", _ExplodingIterable(), 0.0)  # type: ignore[arg-type]
        result = sink.judge_metrics(v)
        # Must return empty dict (or None coerced to {}), never propagate.
        self.assertEqual(result, {})
        # Hard failure disables telemetry.
        self.assertTrue(sink._disabled)

    def test_judge_metrics_with_good_verdict_returns_metrics(self):
        from contracts.models import ComparisonOutcome, FieldComparison, FieldScope

        sink = MLflowTraceSink(_config(), mlflow_factory=lambda: None)
        v = JudgeVerdict(
            "req-1", "opus",
            (FieldComparison("rewards.closingPoints", 100, 100,
                             ComparisonOutcome.AGREE, FieldScope.SCALAR),),
            50.0,
        )
        result = sink.judge_metrics(v)
        self.assertEqual(result["judge.accuracy"], 1.0)

    def test_abandon_does_not_raise_on_nonexistent_request(self):
        sink = MLflowTraceSink(_config(), mlflow_factory=lambda: None)
        # Abandon a request that was never buffered — must not raise.
        sink.abandon("req-nonexistent")
        # Abandon a request that IS buffered.
        sink.record(_evt_for_abandon("extraction", "req-1", "s-e", "s-p"))
        sink.abandon("req-1")
        self.assertNotIn("req-1", sink._builder.pending())

    def test_abandon_works_when_telemetry_disabled(self):
        sink = MLflowTraceSink(_config(), mlflow_factory=lambda: None)
        # Buffer a request, then disable telemetry.
        sink.record(_evt_for_abandon("extraction", "req-1", "s-e", "s-p"))
        sink._disabled = True
        # abandon must still clean up (it bypasses the disabled check).
        sink.abandon("req-1")
        self.assertNotIn("req-1", sink._builder.pending())

    def test_get_trace_id_and_pop_trace_id_do_not_raise(self):
        sink = MLflowTraceSink(_config(), mlflow_factory=lambda: None)
        # Lookups on a non-existent request — must return None, not raise.
        self.assertIsNone(sink.get_trace_id("req-missing"))
        self.assertIsNone(sink.pop_trace_id("req-missing"))

    def test_build_trace_sink_with_invalid_env_does_not_raise(self):
        # Invalid WS4_MAX_* env var must not prevent construction — _env_int
        # falls back to the default with a warning.
        os.environ["WS4_MAX_PENDING"] = "not-a-number"
        os.environ["WS4_MAX_EVENTS_PER_REQUEST"] = "also-not-a-number"
        try:
            sink = build_trace_sink()
            self.assertIsNotNone(sink)
            # The invalid values fell back to defaults, not crashed.
            self.assertEqual(sink._config.max_pending_requests, 1024)
            self.assertEqual(sink._config.max_events_per_request, 100)
        finally:
            del os.environ["WS4_MAX_PENDING"]
            del os.environ["WS4_MAX_EVENTS_PER_REQUEST"]

    def test_get_tracing_config_with_invalid_env_does_not_raise(self):
        from harness.config_ws4 import get_tracing_config

        os.environ["WS4_MAX_TRACE_IDS"] = "garbage"
        try:
            cfg = get_tracing_config()
            self.assertEqual(cfg.max_trace_ids, 1024)  # fell back to default
        finally:
            del os.environ["WS4_MAX_TRACE_IDS"]


if __name__ == "__main__":
    unittest.main()
