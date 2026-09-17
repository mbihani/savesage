"""Regression tests for the production hang fix (telemetry blocking the parse).

Three properties, all stdlib-only (mlflow is NOT imported — an injected
``mlflow_factory`` / ``client_factory`` or a fake sink stands in), matching the
existing tracing-test convention so they run on the python3.14 stdlib gate too:

1. Telemetry can NEVER block or slow the graph — a trace sink whose ``record``
   blocks forever must not stop a parse from completing and emitting its
   ``extraction`` + ``complete`` events within a few seconds.
2. The RetryPolicy the LunaExtractionAdapter actually uses reflects
   ``REQUEST_TIMEOUT_SECONDS`` / ``MAX_ATTEMPTS`` from config (not the old
   hardcoded default).
3. The MLflowTraceSink writes runs through an EXPLICIT ``MlflowClient`` +
   ``run_id`` (create_run/set_tag/set_terminated), and a raising client never
   propagates out of ``record`` / ``log_artifact``.
"""

from datetime import UTC, datetime
import os
import queue
import threading
import time
import unittest
from unittest.mock import patch

from contracts.models import TokenUsage, TraceEvent
from contracts.ports import TraceSink
from harness.config_ws4 import TracingConfig
from harness.tracing import MLflowTraceSink


# ---------------------------------------------------------------------------
# 1. Telemetry can never block the graph thread
# ---------------------------------------------------------------------------

class _BlockingSink(TraceSink):
    """A wrapped sink whose record()/log_artifact() block effectively forever."""

    def __init__(self, gate: threading.Event) -> None:
        self._gate = gate
        self.record_calls = 0
        self.artifact_calls = 0

    def record(self, event: TraceEvent) -> None:
        self.record_calls += 1
        self._gate.wait()  # never returns until the test tears down

    def log_artifact(self, data: bytes, path: str, request_id: str | None = None) -> None:
        self.artifact_calls += 1
        self._gate.wait()


class TelemetryNeverBlocksGraphTest(unittest.TestCase):
    def test_blocking_trace_sink_does_not_stall_the_parse(self):
        # A wrapped MLflow sink that hangs on the FIRST event (the route trace —
        # exactly where the production hang lived) must not freeze the graph.
        from app.main import RequestContext, _ProgressTraceSink, _run_parse
        from graph.fakes import FakeExtractionAdapter
        from graph.nodes import NodeDeps

        gate = threading.Event()  # kept closed → wrapped sink blocks
        blocking = _BlockingSink(gate)

        def _fake_build_deps(ctx, state, prompt_override=None, schema_override=None):
            # Real _ProgressTraceSink wrapping the hanging sink, but a fake
            # (instant) extraction adapter so the only slow thing is telemetry.
            return NodeDeps(
                extraction=FakeExtractionAdapter(),
                trace_sink=_ProgressTraceSink(blocking, ctx, state),
            )

        ctx = RequestContext("req-000000000abc")
        # Small per-call sink timeout so the ONE unavoidable timeout (before the
        # degrade latch trips) is sub-second; assert the whole parse is fast.
        with patch.dict(os.environ, {"TELEMETRY_SINK_TIMEOUT_SECONDS": "0.3"}), \
                patch("app.main._build_deps", side_effect=_fake_build_deps):
            start = time.monotonic()
            # prompt/schema overrides keep routing + validation hermetic (no
            # workspace/DBFS reads), isolating the telemetry-blocking behaviour.
            _run_parse(
                ctx, b"%PDF-1.4 synthetic", "f.pdf", "HDFC",
                prompt_override="SYNTHETIC PROMPT",
                schema_override={"type": "object"},
            )
            elapsed = time.monotonic() - start

        gate.set()  # release the abandoned worker thread(s)

        # The parse finished quickly despite the sink hanging on every event.
        self.assertLess(
            elapsed, 5.0,
            f"parse took {elapsed:.1f}s — telemetry blocked the graph thread",
        )
        # The wrapped sink WAS invoked (at least the route event) — proving the
        # timeout path, not a skipped sink, is what kept us fast.
        self.assertGreaterEqual(blocking.record_calls, 1)

        # The parse still emitted its extraction + terminal complete events.
        seen = []
        while True:
            try:
                e = ctx.events.get_nowait()
            except queue.Empty:
                break
            if e is not None:
                seen.append(e["event"])
        self.assertIn("extraction", seen)
        self.assertIn("complete", seen)
        # And the graph itself reached a terminal outcome.
        self.assertIsNotNone(ctx.outcome)


# ---------------------------------------------------------------------------
# 2. RetryPolicy is wired from config (not the hardcoded default)
# ---------------------------------------------------------------------------

class RetryPolicyWiredFromConfigTest(unittest.TestCase):
    def test_adapter_policy_reflects_env_config(self):
        from harness.extraction_adapter import LunaExtractionAdapter

        with patch.dict(os.environ, {"REQUEST_TIMEOUT_SECONDS": "37", "MAX_ATTEMPTS": "5"}):
            adapter = LunaExtractionAdapter()  # no explicit policy → derive from config
            policy = adapter._policy_obj()
        self.assertEqual(policy.timeout_seconds, 37.0)
        self.assertEqual(policy.max_attempts, 5)

    def test_new_defaults_are_two_attempts_sixty_seconds(self):
        # Wiring defect fix: the shipped defaults are MAX_ATTEMPTS=2 /
        # REQUEST_TIMEOUT_SECONDS=60 (per-attempt), not the old 4 / 180.
        from config import get_settings
        from harness.extraction_adapter import LunaExtractionAdapter

        # Clear any env override so we observe the code defaults.
        with patch.dict(os.environ, {}, clear=False):
            for var in ("REQUEST_TIMEOUT_SECONDS", "MAX_ATTEMPTS"):
                os.environ.pop(var, None)
            settings = get_settings()
            adapter = LunaExtractionAdapter()
            policy = adapter._policy_obj()
        self.assertEqual(settings.max_attempts, 2)
        self.assertEqual(settings.request_timeout_seconds, 60.0)
        self.assertEqual(policy.max_attempts, 2)
        self.assertEqual(policy.timeout_seconds, 60.0)

    def test_explicit_policy_is_not_overridden_by_config(self):
        from harness.extraction_adapter import LunaExtractionAdapter
        from harness.policy import RetryPolicy

        explicit = RetryPolicy(timeout_seconds=99.0, max_attempts=1)
        with patch.dict(os.environ, {"REQUEST_TIMEOUT_SECONDS": "5", "MAX_ATTEMPTS": "9"}):
            adapter = LunaExtractionAdapter(retry_policy=explicit)
            policy = adapter._policy_obj()
        self.assertIs(policy, explicit)
        self.assertEqual(policy.timeout_seconds, 99.0)
        self.assertEqual(policy.max_attempts, 1)


# ---------------------------------------------------------------------------
# 3. Explicit MlflowClient + run_id path; raising client never propagates
# ---------------------------------------------------------------------------

class _FakeLiveSpan:
    def __init__(self):
        self.trace_id = "tr-fake-blockfix"

    def set_attributes(self, a): pass
    def set_attribute(self, k, v): pass
    def set_inputs(self, i): pass
    def set_outputs(self, o): pass
    def record_exception(self, e): pass
    def end(self, **kw): pass


class _FakeMLflowModule:
    """Minimal mlflow module stand-in for span creation + configuration."""

    def set_tracking_uri(self, u): pass
    def set_experiment(self, p): pass

    class tracing:  # noqa: N801
        enable = staticmethod(lambda: None)

    class langchain:  # noqa: N801
        autolog = staticmethod(lambda **kw: None)

    def start_span_no_context(self, **kw):
        return _FakeLiveSpan()


class _RecordingClient:
    """Explicit-client stand-in recording every run-scoped write with its run_id."""

    def __init__(self):
        self.created = []      # list of (experiment_id, tags)
        self.terminated = []   # list of run_id
        self.params = []       # (run_id, key, value)
        self.metrics = []      # (run_id, key, value)
        self.tags = []         # (run_id, key, value)
        self.artifacts = []    # (run_id, local_path, artifact_path)

    def get_experiment_by_name(self, name):
        class _E:
            experiment_id = "exp-from-name"
        return _E()

    def create_run(self, experiment_id, tags=None, run_name=None, start_time=None):
        self.created.append((experiment_id, dict(tags or {})))
        class _Info:
            run_id = "run-blockfix-1"
        class _Run:
            info = _Info()
        return _Run()

    def set_terminated(self, run_id, status=None, end_time=None):
        self.terminated.append(run_id)

    def set_tag(self, run_id, key, value):
        self.tags.append((run_id, key, value))

    def log_param(self, run_id, key, value):
        self.params.append((run_id, key, value))

    def log_metric(self, run_id, key, value):
        self.metrics.append((run_id, key, value))

    def log_artifact(self, run_id, local_path, artifact_path=None):
        self.artifacts.append((run_id, local_path, artifact_path))


def _config():
    return TracingConfig(
        enabled=True, tracking_uri="databricks", databricks_profile="fevm-stable",
        experiment_path="/Shared/savesage/statement-agent", autolog_langchain=False,
        cost_rates_per_million={"m": {"input": 0.0, "output": 0.0}},
    )


def _evt(name, sid, parent=None, attrs=None, rid="req-1", offset=0):
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    started = datetime.fromtimestamp(base.timestamp() + offset, tz=UTC)
    ended = datetime.fromtimestamp(base.timestamp() + offset + 1, tz=UTC)
    return TraceEvent(
        request_id=rid, name=name, started_at=started, ended_at=ended,
        attributes=attrs or {}, span_id=sid, parent_span_id=parent,
    )


class ExplicitRunIdPathTest(unittest.TestCase):
    def _sink(self, client):
        return MLflowTraceSink(
            _config(),
            mlflow_factory=lambda: _FakeMLflowModule(),
            client_factory=lambda: client,
        )

    def test_run_created_and_tagged_and_terminated_via_client(self):
        client = _RecordingClient()
        sink = self._sink(client)
        # MLFLOW_EXPERIMENT_ID drives create_run's experiment id deterministically.
        with patch.dict(os.environ, {"MLFLOW_EXPERIMENT_ID": "exp-777"}):
            sink.record(_evt("extract", "s-extract", parent="s-parse",
                             attrs={"model_id": "m", "token_usage": TokenUsage(1, 1, 2)}))
            sink.record(_evt("parse", "s-parse", parent=None,
                             attrs={"bank": "HDFC", "outcome": "OK", "n_transactions": 3}))

        # Exactly one run created, in the resolved experiment, tagged request_id.
        self.assertEqual(len(client.created), 1)
        exp_id, tags = client.created[0]
        self.assertEqual(exp_id, "exp-777")
        self.assertEqual(tags.get("request_id"), "req-1")
        # Every run-scoped write used the explicit run_id (never an active run).
        self.assertTrue(all(rid == "run-blockfix-1" for rid, _, _ in client.params))
        self.assertTrue(all(rid == "run-blockfix-1" for rid, _, _ in client.metrics))
        # bank logged as param + tag; n_transactions as metric.
        self.assertIn(("run-blockfix-1", "bank", "HDFC"), client.params)
        self.assertIn(("run-blockfix-1", "bank", "HDFC"), client.tags)
        self.assertIn(("run-blockfix-1", "n_transactions", 3), client.metrics)
        # Run finalized via set_terminated (not fluent end_run) and slot freed.
        self.assertEqual(client.terminated, ["run-blockfix-1"])
        self.assertNotIn("req-1", sink._run_ids)

    def test_log_artifact_targets_the_requests_run(self):
        client = _RecordingClient()
        sink = self._sink(client)
        with patch.dict(os.environ, {"MLFLOW_EXPERIMENT_ID": "exp-777"}):
            # First a trace event creates the run for this request.
            sink.record(_evt("extract", "s-e", parent="s-parse",
                             attrs={"model_id": "m"}))
            sink.log_artifact(b"PDFDATA", "statement.pdf", request_id="req-1")
        self.assertEqual(len(client.artifacts), 1)
        run_id, _local, artifact_path = client.artifacts[0]
        self.assertEqual(run_id, "run-blockfix-1")

    def test_raising_client_does_not_propagate_from_record(self):
        def _boom():
            raise RuntimeError("mlflow client broken")

        sink = MLflowTraceSink(
            _config(),
            mlflow_factory=lambda: _FakeMLflowModule(),
            client_factory=_boom,  # obtaining the client raises
        )
        with patch.dict(os.environ, {"MLFLOW_EXPERIMENT_ID": "exp-777"}):
            # Must not raise despite the client blowing up on every run write.
            sink.record(_evt("extract", "s-e", parent="s-parse", attrs={"model_id": "m"}))
            sink.record(_evt("parse", "s-parse", parent=None, attrs={"bank": "HDFC"}))
            sink.log_artifact(b"x", "statement.pdf", request_id="req-1")


if __name__ == "__main__":
    unittest.main()
