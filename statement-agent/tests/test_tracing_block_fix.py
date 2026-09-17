"""Regression tests for the production hang fix (telemetry blocking the parse).

Round 2 reworks the mechanism to a SINGLE background telemetry consumer + bounded
queue (harness.telemetry_dispatch). These tests exercise the FULL composition —
``_ProgressTraceSink`` → ``TelemetryDispatcher`` → a real ``MLflowTraceSink`` whose
injected MLflow client op blocks ~forever — rather than a bare fake sink, so they
catch the properties round 1 could not:

1. Telemetry can NEVER block or slow the graph — the graph thread enqueues each
   telemetry event with a non-blocking put and returns; a wrapped MLflow client
   that blocks forever on ``create_run`` cannot stop a parse from completing and
   emitting its ``extraction`` + ``complete`` events within a few seconds.
2. No unbounded thread/backlog growth — MLflow work runs on ONE serial consumer
   thread (never a fresh thread per op), and the queue is bounded.
3. Late completion of a blocked op does NOT orphan a RUNNING run or corrupt the
   request_id → run join key: exactly one run is created and it is terminated,
   and the trace is linked to it (SOURCE_RUN) once the block clears.
4. The RetryPolicy the LunaExtractionAdapter uses reflects
   ``REQUEST_TIMEOUT_SECONDS`` / ``MAX_ATTEMPTS`` from config.
5. The MLflowTraceSink writes runs through an EXPLICIT ``MlflowClient`` +
   ``run_id`` (create_run/set_tag/set_terminated), links the trace to its run,
   and a raising client never propagates out of ``record`` / ``log_artifact``.

All stdlib-only (mlflow is NOT imported — an injected ``mlflow_factory`` /
``client_factory`` stands in), so they run on the python3.14 stdlib gate too.
"""

from datetime import UTC, datetime
import os
import queue
import threading
import time
import unittest
from unittest.mock import patch

from contracts.models import TokenUsage, TraceEvent
from harness.config_ws4 import TracingConfig
from harness.telemetry_dispatch import TelemetryDispatcher
from harness.tracing import MLflowTraceSink


# ---------------------------------------------------------------------------
# Shared fakes: an explicit MlflowClient stand-in + a minimal mlflow module.
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
    """Explicit-client stand-in recording every run-scoped write with its run_id.

    ``create_run`` optionally blocks on ``gate`` (used to simulate a tracking
    server that hangs), so the test can prove the graph thread is not held up
    and that a late-clearing block does not spawn duplicate/orphan runs.
    """

    def __init__(self, gate: threading.Event | None = None):
        self._gate = gate
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
        if self._gate is not None:
            self._gate.wait()  # block the CONSUMER until the test releases it
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


def _sink(client, *, link_spy=None):
    sink = MLflowTraceSink(
        _config(),
        mlflow_factory=lambda: _FakeMLflowModule(),
        client_factory=lambda: client,
    )
    if link_spy is not None:
        orig = sink._link_trace_to_run

        def _spy(trace_id, run_id):
            link_spy.append((trace_id, run_id))
            return orig(trace_id, run_id)

        sink._link_trace_to_run = _spy  # record SOURCE_RUN linkage attempts
    return sink


def _evt(name, sid, parent=None, attrs=None, rid="req-1", offset=0):
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    started = datetime.fromtimestamp(base.timestamp() + offset, tz=UTC)
    ended = datetime.fromtimestamp(base.timestamp() + offset + 1, tz=UTC)
    return TraceEvent(
        request_id=rid, name=name, started_at=started, ended_at=ended,
        attributes=attrs or {}, span_id=sid, parent_span_id=parent,
    )


# ---------------------------------------------------------------------------
# 1. Telemetry never blocks the graph; single consumer; no orphan on unblock.
# ---------------------------------------------------------------------------

class TelemetryNeverBlocksGraphTest(unittest.TestCase):
    def test_blocking_mlflow_client_does_not_stall_the_parse(self):
        # A real MLflowTraceSink whose injected client BLOCKS on create_run (the
        # first thing the route event triggers — exactly where the production
        # hang lived) must not freeze the graph, and its late unblock must leave
        # exactly ONE run, terminated, with its trace linked.
        from app.main import RequestContext, _ProgressTraceSink, _run_parse
        from graph.fakes import FakeExtractionAdapter
        from graph.nodes import NodeDeps

        gate = threading.Event()  # kept closed → consumer blocks in create_run
        client = _RecordingClient(gate=gate)
        linked: list = []
        wrapped = _sink(client, link_spy=linked)
        dispatcher = TelemetryDispatcher(maxsize=64)

        def _fake_build_deps(ctx, state, prompt_override=None, schema_override=None):
            return NodeDeps(
                extraction=FakeExtractionAdapter(),
                trace_sink=_ProgressTraceSink(wrapped, ctx, state, dispatcher=dispatcher),
            )

        ctx = RequestContext("req-000000000abc")
        # Baseline consumer-thread count (other tests may hold the process-wide
        # singleton's consumer alive) so we can assert OUR dispatcher adds exactly
        # one — never one-per-op.
        consumers_before = _count_threads("telemetry-consumer")
        with patch("app.main._build_deps", side_effect=_fake_build_deps), \
                patch.dict(os.environ, {"MLFLOW_EXPERIMENT_ID": "exp-777"}):
            start = time.monotonic()
            _run_parse(
                ctx, b"%PDF-1.4 synthetic", "f.pdf", "HDFC",
                prompt_override="SYNTHETIC PROMPT",
                schema_override={"type": "object"},
            )
            elapsed = time.monotonic() - start

            # (a)+(b) The parse finished quickly despite MLflow hanging on the
            # very first (route) event — the graph thread never waited on it.
            self.assertLess(
                elapsed, 5.0,
                f"parse took {elapsed:.1f}s — telemetry blocked the graph thread",
            )

            # (c) MLflow work runs on ONE serial consumer thread, never a fresh
            # thread per op (round 1's leak). The consumer is currently BLOCKED
            # in create_run, and all ~6 subsequent events/artifacts are backed up
            # behind it — yet OUR dispatcher added exactly one consumer thread,
            # and there are no per-op ("telemetry-mlflow*"/"telemetry-sink*")
            # worker threads that round 1 would have spawned.
            self.assertEqual(
                _count_threads("telemetry-consumer") - consumers_before, 1,
                "expected exactly one added consumer thread (no thread-per-op)",
            )
            self.assertEqual(
                [t.name for t in threading.enumerate()
                 if t.name.startswith(("telemetry-mlflow", "telemetry-sink"))], [],
                "found per-op telemetry worker threads — thread-per-op regressed",
            )
            # The consumer has NOT drained (it is stuck on the gated create_run).
            self.assertFalse(dispatcher.join(timeout=0.5))
            # No run has been created yet (create_run is still blocked).
            self.assertEqual(client.created, [])

            # The parse still emitted its extraction + terminal complete events,
            # and reached a terminal outcome — all without telemetry.
            seen = _drain_events(ctx)
            self.assertIn("extraction", seen)
            self.assertIn("complete", seen)
            self.assertIsNotNone(ctx.outcome)

            # (d) Release the block: the single consumer now drains the whole
            # backlog IN ORDER on the same thread. Exactly one run is created and
            # it is terminated (no duplicate/orphan RUNNING run), and the trace
            # is linked to that run (SOURCE_RUN) — the judge join key is intact.
            gate.set()
            self.assertTrue(dispatcher.join(timeout=5.0),
                            "consumer did not drain after unblock")

        self.assertEqual(len(client.created), 1, "expected exactly one run")
        self.assertEqual(client.created[0][1].get("request_id"), "req-000000000abc")
        self.assertEqual(client.terminated, ["run-blockfix-1"],
                         "the run the consumer created was not terminated")
        self.assertNotIn("req-000000000abc", wrapped._run_ids)  # slot freed
        # Trace linked to its own run exactly once (SOURCE_RUN linkage).
        self.assertEqual(linked, [("tr-fake-blockfix", "run-blockfix-1")])
        # The source PDF + extraction.json artifacts were attached to that run.
        self.assertTrue(client.artifacts)
        self.assertTrue(all(rid == "run-blockfix-1" for rid, _, _ in client.artifacts))
        dispatcher.stop()


def _count_threads(name: str) -> int:
    return sum(1 for t in threading.enumerate() if t.name == name)


def _drain_events(ctx) -> list:
    seen = []
    while True:
        try:
            e = ctx.events.get_nowait()
        except queue.Empty:
            break
        if e is not None:
            seen.append(e["event"])
    return seen


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
# 3. Cooperative cancellation of the extraction worker (issue #3)
# ---------------------------------------------------------------------------

class ExtractionCancellationTest(unittest.TestCase):
    def _req(self, rid="req-cancel-1"):
        from contracts.models import Bank, ParseRequest
        return ParseRequest(b"%PDF-1.4", "x.pdf", Bank.HDFC, rid)

    def test_cancel_before_attempt_raises_without_calling_luna(self):
        from harness.extraction_adapter import (
            ExtractionCancelled, LunaExtractionAdapter,
        )
        from harness.policy import RetryPolicy

        cancel = threading.Event()
        cancel.set()  # already cancelled before extract runs
        calls = {"urlopen": 0}

        def _urlopen(req, timeout=None):
            calls["urlopen"] += 1
            raise AssertionError("Luna must not be called after cancellation")

        adapter = LunaExtractionAdapter(
            retry_policy=RetryPolicy(max_attempts=2, initial_backoff_seconds=0.0),
            settings=_FakeSettings(),
            token_provider=lambda: "tok",
            urlopen=_urlopen,
            prompt_override="P", schema_override={"type": "object"},
            cancel_event=cancel,
        )
        with self.assertRaises(ExtractionCancelled):
            adapter.extract(self._req())
        self.assertEqual(calls["urlopen"], 0)  # no network work at all

    def test_cancel_during_backoff_wakes_and_raises_fast(self):
        import urllib.error
        from harness.extraction_adapter import (
            ExtractionCancelled, LunaExtractionAdapter,
        )
        from harness.policy import RetryPolicy

        cancel = threading.Event()

        def _urlopen(req, timeout=None):
            # First attempt fails retryably; the backoff would normally sleep
            # for a long time, but cancellation must cut it short.
            raise urllib.error.HTTPError("u", 503, "busy", {}, None)

        adapter = LunaExtractionAdapter(
            # Long backoff so a non-cancellable sleep would blow the deadline.
            retry_policy=RetryPolicy(
                max_attempts=3, initial_backoff_seconds=30.0, max_backoff_seconds=30.0,
            ),
            settings=_FakeSettings(),
            token_provider=lambda: "tok",
            urlopen=_urlopen,
            prompt_override="P", schema_override={"type": "object"},
            cancel_event=cancel,
        )

        # Cancel shortly after extract starts (during the first backoff).
        threading.Timer(0.2, cancel.set).start()
        start = time.monotonic()
        with self.assertRaises(ExtractionCancelled):
            adapter.extract(self._req("req-cancel-2"))
        self.assertLess(time.monotonic() - start, 5.0,
                        "backoff ignored cancellation (slept the full 30s)")

    def test_token_acquisition_is_bounded(self):
        from harness.extraction_adapter import ExtractionError, LunaExtractionAdapter
        from harness.policy import RetryPolicy

        def _hanging_token():
            threading.Event().wait()  # blocks forever

        adapter = LunaExtractionAdapter(
            retry_policy=RetryPolicy(max_attempts=1),
            settings=_FakeSettings(),
            token_provider=_hanging_token,
            urlopen=lambda req, timeout=None: self.fail("must not reach Luna"),
            prompt_override="P", schema_override={"type": "object"},
            token_timeout_seconds=0.3,  # bound so the hang is abandoned quickly
        )
        start = time.monotonic()
        with self.assertRaises(ExtractionError):
            adapter.extract(self._req("req-cancel-3"))
        self.assertLess(time.monotonic() - start, 5.0,
                        "token acquisition was not bounded")


class _FakeSettings:
    """Minimal settings stub matching config.Settings' interface."""

    workspace_host = "https://example.databricks.com"
    extraction_endpoint = "databricks-gpt-5-6-luna"

    def endpoint_url(self, endpoint: str) -> str:
        return f"{self.workspace_host}/serving-endpoints/{endpoint}/invocations"


# ---------------------------------------------------------------------------
# 4. Explicit MlflowClient + run_id path; trace linkage; raising client is safe.
# ---------------------------------------------------------------------------

class ExplicitRunIdPathTest(unittest.TestCase):
    def test_run_created_and_tagged_and_terminated_via_client(self):
        client = _RecordingClient()
        linked: list = []
        sink = _sink(client, link_spy=linked)
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
        self.assertIn(("run-blockfix-1", "bank", "HDFC"), client.params)
        self.assertIn(("run-blockfix-1", "bank", "HDFC"), client.tags)
        self.assertIn(("run-blockfix-1", "n_transactions", 3), client.metrics)
        # Run finalized via set_terminated (not fluent end_run) and slot freed.
        self.assertEqual(client.terminated, ["run-blockfix-1"])
        self.assertNotIn("req-1", sink._run_ids)
        # Happy-path: the trace is linked to its own run (SOURCE_RUN) exactly once.
        self.assertEqual(linked, [("tr-fake-blockfix", "run-blockfix-1")])

    def test_no_duplicate_run_when_create_run_first_fails(self):
        # If the first create_run does not land a run_id, a LATER event must not
        # create a SECOND run (which would make the request_id → run join key
        # ambiguous). At-most-once creation.
        class _FlakyClient(_RecordingClient):
            def create_run(self, experiment_id, tags=None, run_name=None, start_time=None):
                self.created.append((experiment_id, dict(tags or {})))
                raise RuntimeError("create_run failed")

        client = _FlakyClient()
        sink = _sink(client)
        with patch.dict(os.environ, {"MLFLOW_EXPERIMENT_ID": "exp-777"}):
            sink.record(_evt("route", "s-route", parent="s-parse"))
            sink.record(_evt("extract", "s-extract", parent="s-parse"))
            sink.record(_evt("parse", "s-parse", parent=None, attrs={"bank": "HDFC"}))
        # create_run attempted exactly ONCE despite three events.
        self.assertEqual(len(client.created), 1)

    def test_log_artifact_targets_the_requests_run(self):
        client = _RecordingClient()
        sink = _sink(client)
        with patch.dict(os.environ, {"MLFLOW_EXPERIMENT_ID": "exp-777"}):
            sink.record(_evt("extract", "s-e", parent="s-parse", attrs={"model_id": "m"}))
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
            sink.record(_evt("extract", "s-e", parent="s-parse", attrs={"model_id": "m"}))
            sink.record(_evt("parse", "s-parse", parent=None, attrs={"bank": "HDFC"}))
            sink.log_artifact(b"x", "statement.pdf", request_id="req-1")


if __name__ == "__main__":
    unittest.main()
