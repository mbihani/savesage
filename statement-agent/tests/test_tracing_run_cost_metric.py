"""Stdlib tests: per-statement parse cost surfaces as an MLflow RUN METRIC.

The cost is already set as the extract-span attribute ``mlflow.llm.cost`` (visible
only in the trace detail view) by ``_apply_attributes``. This change ADDS the same
cost value as a run METRIC (``cost_usd`` / ``input_cost_usd`` / ``output_cost_usd``)
so it appears as a column in the MLflow experiment Runs table. The metric reuses
the value already computed from token usage + rates via ``cost_attributes``.

No mlflow import — uses an injected ``mlflow_factory`` (recording fake) so the
tests run stdlib-only, matching the existing tracing-test convention.
"""

from datetime import UTC, datetime
import unittest

from contracts.models import TraceEvent, TokenUsage
from harness.config_ws4 import TracingConfig
from harness.tracing import MLflowTraceSink


def _evt(name, sid, parent=None, attrs=None, rid="req-1", offset=0):
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    started = datetime.fromtimestamp(base.timestamp() + offset, tz=UTC)
    ended = datetime.fromtimestamp(base.timestamp() + offset + 1, tz=UTC)
    return TraceEvent(
        request_id=rid, name=name, started_at=started, ended_at=ended,
        attributes=attrs or {}, span_id=sid, parent_span_id=parent,
    )


def _config(rates=None, enabled=True):
    return TracingConfig(
        enabled=enabled, tracking_uri="databricks", databricks_profile="fevm-stable",
        experiment_path="/Shared/savesage/statement-agent", autolog_langchain=False,
        cost_rates_per_million=rates if rates is not None else {},
    )


class _FakeLiveSpan:
    def __init__(self):
        self.trace_id = "tr-fake"
        self.span_id = "fake"

    def set_attributes(self, a): pass
    def set_attribute(self, k, v): pass
    def set_inputs(self, i): pass
    def set_outputs(self, o): pass
    def record_exception(self, e): pass
    def end(self, **kw): pass


class _RecordingMLflow:
    """Fake mlflow that records log_metric / log_param calls (no real mlflow)."""

    def __init__(self):
        self.metrics: dict[str, float] = {}
        self.params: dict[str, object] = {}

    def set_tracking_uri(self, u): pass
    def set_experiment(self, p): pass

    class tracing:  # noqa: N801 - mimic mlflow.tracing namespace
        enable = staticmethod(lambda: None)

    class langchain:  # noqa: N801
        autolog = staticmethod(lambda **kw: None)

    def start_span_no_context(self, **kw): return _FakeLiveSpan()
    def start_run(self):
        class _Info:
            run_id = "run-fake-cost"
        class _Run:
            info = _Info()
        return _Run()

    def end_run(self): pass
    def set_tag(self, key, value): pass

    def log_param(self, key, value):
        self.params[key] = value

    def log_metric(self, key, value):
        self.metrics[key] = value


class _RaisingOnCostMLflow(_RecordingMLflow):
    """Records normally, but log_metric RAISES for the cost metrics only.

    Proves the cost block is best-effort: a raising cost log is swallowed and
    does NOT abort the rest of _do (latency_ms still records).
    """

    _COST_KEYS = ("cost_usd", "input_cost_usd", "output_cost_usd")

    def log_metric(self, key, value):
        if key in self._COST_KEYS:
            raise RuntimeError(f"mlflow broken: log_metric {key}")
        super().log_metric(key, value)


def _feed_extract_then_root(sink, extract_attrs, root_attrs=None):
    """Feed an extract child then the parse root to trigger a flush + metrics."""
    sink.record(_evt("extract", "s-extract", parent="s-parse", attrs=extract_attrs))
    sink.record(_evt("parse", "s-parse", parent=None,
                     attrs=root_attrs or {"bank": "ICICI", "outcome": "OK",
                                          "n_transactions": 12}))


class RunCostMetricTest(unittest.TestCase):
    def test_priced_model_logs_cost_usd_input_and_output(self):
        rates = {"gpt-5.6-luna": {"input": 0.2, "output": 1.2}}
        fake = _RecordingMLflow()
        sink = MLflowTraceSink(_config(rates=rates), mlflow_factory=lambda: fake)
        tu = {"input_tokens": 1_000_000, "output_tokens": 500_000, "total_tokens": 1_500_000}
        _feed_extract_then_root(sink, {"model_id": "gpt-5.6-luna", "token_usage": tu,
                                        "latency_ms": 1234.0})
        # cost_usd = input*0.2/1e6 + output*1.2/1e6 = 0.2 + 0.6 = 0.8
        self.assertIn("cost_usd", fake.metrics)
        self.assertAlmostEqual(fake.metrics["cost_usd"], 0.8)
        self.assertIn("input_cost_usd", fake.metrics)
        self.assertAlmostEqual(fake.metrics["input_cost_usd"], 0.2)
        self.assertIn("output_cost_usd", fake.metrics)
        self.assertAlmostEqual(fake.metrics["output_cost_usd"], 0.6)
        # Token metrics and latency still logged alongside cost.
        self.assertEqual(fake.metrics["input_tokens"], 1_000_000)
        self.assertEqual(fake.metrics["output_tokens"], 500_000)
        self.assertEqual(fake.metrics["latency_ms"], 1234.0)
        # model_id logged as a param.
        self.assertEqual(fake.params.get("model_id"), "gpt-5.6-luna")

    def test_zero_rate_model_logs_zero_cost_not_skipped(self):
        rates = {"gpt-5.6-luna": {"input": 0.0, "output": 0.0}}
        fake = _RecordingMLflow()
        sink = MLflowTraceSink(_config(rates=rates), mlflow_factory=lambda: fake)
        tu = {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500}
        _feed_extract_then_root(sink, {"model_id": "gpt-5.6-luna", "token_usage": tu})
        # Explicit 0.0 — logged, NOT skipped (cost_attributes never returns None
        # when usage is present, even for a zero-rate model).
        self.assertIn("cost_usd", fake.metrics)
        self.assertEqual(fake.metrics["cost_usd"], 0.0)
        self.assertEqual(fake.metrics["input_cost_usd"], 0.0)
        self.assertEqual(fake.metrics["output_cost_usd"], 0.0)

    def test_unpriced_model_in_empty_rate_table_logs_zero_cost(self):
        # A model absent from the rate table also gets explicit 0.0.
        fake = _RecordingMLflow()
        sink = MLflowTraceSink(_config(rates={}), mlflow_factory=lambda: fake)
        tu = {"input_tokens": 1000, "output_tokens": 500, "total_tokens": 1500}
        _feed_extract_then_root(sink, {"model_id": "unknown-model", "token_usage": tu})
        self.assertIn("cost_usd", fake.metrics)
        self.assertEqual(fake.metrics["cost_usd"], 0.0)

    def test_no_usage_does_not_log_cost_and_does_not_raise(self):
        rates = {"gpt-5.6-luna": {"input": 0.2, "output": 1.2}}
        fake = _RecordingMLflow()
        sink = MLflowTraceSink(_config(rates=rates), mlflow_factory=lambda: fake)
        # No token_usage attribute on the extract event -> cost_attributes returns
        # None -> the cost block is skipped, nothing raises.
        _feed_extract_then_root(sink, {"model_id": "gpt-5.6-luna", "latency_ms": 50.0})
        self.assertNotIn("cost_usd", fake.metrics)
        self.assertNotIn("input_cost_usd", fake.metrics)
        self.assertNotIn("output_cost_usd", fake.metrics)
        # Latency still logged (the rest of _do runs fine).
        self.assertEqual(fake.metrics.get("latency_ms"), 50.0)

    def test_none_token_usage_does_not_log_cost_and_does_not_raise(self):
        rates = {"gpt-5.6-luna": {"input": 0.2, "output": 1.2}}
        fake = _RecordingMLflow()
        sink = MLflowTraceSink(_config(rates=rates), mlflow_factory=lambda: fake)
        _feed_extract_then_root(sink, {"model_id": "gpt-5.6-luna",
                                        "token_usage": None, "latency_ms": 50.0})
        self.assertNotIn("cost_usd", fake.metrics)
        self.assertEqual(fake.metrics.get("latency_ms"), 50.0)

    def test_token_usage_dataclass_also_prices(self):
        # cost_attributes accepts a TokenUsage dataclass too (not just a dict);
        # the token-metrics loop is dict-gated but cost is computed regardless.
        rates = {"gpt-5.6-luna": {"input": 0.2, "output": 1.2}}
        fake = _RecordingMLflow()
        sink = MLflowTraceSink(_config(rates=rates), mlflow_factory=lambda: fake)
        tu = TokenUsage(input_tokens=1_000_000, output_tokens=500_000, total_tokens=1_500_000)
        _feed_extract_then_root(sink, {"model_id": "gpt-5.6-luna", "token_usage": tu})
        self.assertIn("cost_usd", fake.metrics)
        self.assertAlmostEqual(fake.metrics["cost_usd"], 0.8)

    def test_raising_mlflow_on_cost_does_not_break_record(self):
        # A raising MLflow client (best-effort) must never break the parse path.
        # log_metric raises for the cost keys only; each is swallowed by
        # best_effort and the rest of _do still runs (latency recorded).
        rates = {"gpt-5.6-luna": {"input": 0.2, "output": 1.2}}
        fake = _RaisingOnCostMLflow()
        sink = MLflowTraceSink(_config(rates=rates), mlflow_factory=lambda: fake)
        tu = {"input_tokens": 1_000_000, "output_tokens": 500_000, "total_tokens": 1_500_000}
        # Must not raise.
        _feed_extract_then_root(sink, {"model_id": "gpt-5.6-luna", "token_usage": tu,
                                        "latency_ms": 999.0})
        # Cost metrics were NOT recorded (log_metric raised, swallowed).
        self.assertNotIn("cost_usd", fake.metrics)
        self.assertNotIn("input_cost_usd", fake.metrics)
        self.assertNotIn("output_cost_usd", fake.metrics)
        # Token metrics (non-cost) and latency still recorded — best-effort is
        # per-call, so a raising cost log does not abort the rest of _do.
        self.assertEqual(fake.metrics["input_tokens"], 1_000_000)
        self.assertEqual(fake.metrics["latency_ms"], 999.0)


if __name__ == "__main__":
    unittest.main()
