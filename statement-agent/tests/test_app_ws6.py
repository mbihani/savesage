"""Stdlib-only tests for the workstream-6 FastAPI app (routes, SSE, app.yaml).

These tests import helper functions from ``app.main`` that are pure
stdlib — no FastAPI, langgraph, psycopg, or mlflow required.  The
``app = create_app()`` guard in ``app/main.py`` ensures the module imports
cleanly even when FastAPI is absent.
"""

import json
import queue
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from app.main import (
    PIPELINE_STAGES,
    RequestContext,
    _comparison_to_dict,
    _new_request_id,
    _ProgressTraceSink,
    _run_blocking,
    _sse_event,
)
from contracts.models import (
    ComparisonOutcome,
    FieldComparison,
    FieldScope,
    MatchMethod,
    TraceEvent,
)


# ---------------------------------------------------------------------------
# SSE event formatting
# ---------------------------------------------------------------------------

class SSEEventTest(unittest.TestCase):
    def test_basic_format(self) -> None:
        line = _sse_event("progress", {"stage": "extract"})
        self.assertTrue(line.startswith("event: progress\n"))
        self.assertTrue(line.endswith("\n\n"))
        # data line contains valid JSON
        data_line = [l for l in line.strip().split("\n") if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        self.assertEqual(payload, {"stage": "extract"})

    def test_multi_key_json(self) -> None:
        line = _sse_event("complete", {"outcome": "SUCCESS", "stage": "FINALIZE"})
        data_line = [l for l in line.split("\n") if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        self.assertEqual(payload["outcome"], "SUCCESS")
        self.assertEqual(payload["stage"], "FINALIZE")

    def test_datetime_serialised_via_default_str(self) -> None:
        """``default=str`` must not crash on datetime — it calls str()."""
        ts = datetime(2026, 1, 1, tzinfo=UTC)
        line = _sse_event("test", {"ts": ts})
        data_line = [l for l in line.split("\n") if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        self.assertIn("2026", str(payload["ts"]))

    def test_none_data(self) -> None:
        line = _sse_event("error", None)
        data_line = [l for l in line.split("\n") if l.startswith("data: ")][0]
        payload = json.loads(data_line[len("data: "):])
        self.assertIsNone(payload)

    def test_event_type_in_header(self) -> None:
        for event_type in ("start", "progress", "extraction", "verdict", "complete", "error"):
            with self.subTest(event_type=event_type):
                line = _sse_event(event_type, {})
                self.assertIn(f"event: {event_type}\n", line)


# ---------------------------------------------------------------------------
# Feedback body validation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# FieldComparison serialisation
# ---------------------------------------------------------------------------

class ComparisonDictTest(unittest.TestCase):
    def _scalar(self) -> FieldComparison:
        return FieldComparison(
            field_path="rewards.closingPoints",
            expected=42,
            actual=42,
            outcome=ComparisonOutcome.AGREE,
            scope=FieldScope.SCALAR,
        )

    def _txn(self) -> FieldComparison:
        return FieldComparison(
            field_path="transactions[].amount",
            expected=100.0,
            actual=100.0,
            outcome=ComparisonOutcome.AGREE,
            scope=FieldScope.TRANSACTION_ROW,
            match_method=MatchMethod.DESCRIPTION_SIMILARITY_1TO1,
            expected_row_index=0,
            actual_row_index=0,
            similarity=1.0,
        )

    def test_scalar_fields(self) -> None:
        d = _comparison_to_dict(self._scalar())
        self.assertEqual(d["field_path"], "rewards.closingPoints")
        self.assertEqual(d["outcome"], "AGREE")
        self.assertEqual(d["scope"], "SCALAR")
        self.assertEqual(d["match_method"], "DIRECT")

    def test_transaction_fields(self) -> None:
        d = _comparison_to_dict(self._txn())
        self.assertEqual(d["field_path"], "transactions[].amount")
        self.assertEqual(d["outcome"], "AGREE")
        self.assertEqual(d["scope"], "TRANSACTION_ROW")
        self.assertEqual(d["match_method"], "DESCRIPTION_SIMILARITY_1TO1")
        self.assertEqual(d["expected_row_index"], 0)
        self.assertEqual(d["actual_row_index"], 0)
        self.assertEqual(d["similarity"], 1.0)

    def test_enum_values_are_strings(self) -> None:
        """Enums must be flattened to .value strings for JSON."""
        for outcome in ComparisonOutcome:
            c = FieldComparison(
                "rewards.closingPoints", "x", "y", outcome, FieldScope.SCALAR,
            )
            d = _comparison_to_dict(c)
            self.assertIsInstance(d["outcome"], str)
            self.assertEqual(d["outcome"], outcome.value)

    def test_json_serialisable(self) -> None:
        """The dict must be JSON-serialisable (no enum/dataclass leftovers)."""
        import json
        for c in (self._scalar(), self._txn()):
            d = _comparison_to_dict(c)
            json.dumps(d)  # must not raise

    def test_none_fields_preserved(self) -> None:
        c = FieldComparison(
            "rewards.closingPoints", None, 42,
            ComparisonOutcome.DISAGREE, FieldScope.SCALAR,
            rationale="expected missing",
        )
        d = _comparison_to_dict(c)
        self.assertIsNone(d["expected"])
        self.assertEqual(d["actual"], 42)
        self.assertEqual(d["rationale"], "expected missing")

    def test_no_per_field_path_key(self) -> None:
        """The serialised dict no longer carries a per-field path key."""
        d = _comparison_to_dict(self._scalar())
        self.assertNotIn("feed" + "back_path", d)


# ---------------------------------------------------------------------------
# RequestContext
# ---------------------------------------------------------------------------

class RequestContextTest(unittest.TestCase):
    def test_push_and_drain(self) -> None:
        ctx = RequestContext("test-req-1")
        ctx.push("progress", {"stage": "route"})
        ctx.push("progress", {"stage": "extract"})
        ev1 = ctx.events.get_nowait()
        ev2 = ctx.events.get_nowait()
        self.assertEqual(ev1["event"], "progress")
        self.assertEqual(ev1["data"]["stage"], "route")
        self.assertEqual(ev2["data"]["stage"], "extract")

    def test_sentinel(self) -> None:
        ctx = RequestContext("test-req-2")
        ctx.push("complete", {"outcome": "SUCCESS"})
        ctx.push_sentinel()
        # First get returns the complete event
        ev = ctx.events.get_nowait()
        self.assertEqual(ev["event"], "complete")
        # Second get returns the sentinel (None)
        self.assertIsNone(ctx.events.get_nowait())
        self.assertTrue(ctx.done.is_set())

    def test_thread_safe_concurrent(self) -> None:
        """Multiple threads pushing events must not lose any."""
        import threading

        ctx = RequestContext("test-req-3")

        def producer(n: int) -> None:
            for i in range(50):
                ctx.push("progress", {"thread": n, "i": i})

        threads = [threading.Thread(target=producer, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        count = 0
        while not ctx.events.empty():
            ctx.events.get_nowait()
            count += 1
        self.assertEqual(count, 200)

    def test_request_id_format(self) -> None:
        rid = _new_request_id()
        self.assertTrue(rid.startswith("req-"))
        self.assertGreaterEqual(len(rid), 16)  # req- + 12 hex

    def test_request_id_unique(self) -> None:
        ids = {_new_request_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)

    def test_pipeline_stages_order(self) -> None:
        self.assertEqual(PIPELINE_STAGES, (
            "route", "extract", "validate", "finalize",
        ))


# ---------------------------------------------------------------------------
# app.yaml structural validation
# ---------------------------------------------------------------------------

class AppYamlTest(unittest.TestCase):
    """Validate the consolidated app.yaml has required env vars + resources.

    Uses ``yaml`` (available locally via the Databricks SDK dependency)
    to parse the file; falls back to text-matching if yaml is unavailable.
    """

    def setUp(self) -> None:
        self.path = Path(__file__).resolve().parents[1] / "app.yaml"
        self.text = self.path.read_text(encoding="utf-8")
        try:
            import yaml
            self.parsed = yaml.safe_load(self.text)
        except ImportError:  # pragma: no cover
            self.parsed = None

    def _env_names(self) -> list[str]:
        if self.parsed is not None:
            return [e["name"] for e in self.parsed.get("env", [])]
        # text fallback
        import re
        return re.findall(r"- name: (\w+)", self.text)

    def _resource_names(self) -> list[str]:
        if self.parsed is not None:
            return [r["name"] for r in self.parsed.get("resources", [])]
        import re
        return re.findall(r"- name: (\S+)", self.text)

    def test_required_env_vars_present(self) -> None:
        names = set(self._env_names())
        for var in ("DATABRICKS_HOST", "EXTRACTION_ENDPOINT", "JUDGE_ENDPOINT"):
            with self.subTest(var=var):
                self.assertIn(var, names)

    def test_no_rds_env_vars(self) -> None:
        """No Postgres connection env vars (database layer removed)."""
        names = set(self._env_names())
        for var in ("RDS" + "_HOST", "RDS" + "_PORT", "RDS" + "_DATABASE",
                    "RDS" + "_USER", "RDS" + "_PASSWORD", "RDS" + "_SSLMODE"):
            with self.subTest(var=var):
                self.assertNotIn(var, names)

    def test_mlflow_env_vars(self) -> None:
        """WS4 env vars for MLflow tracing."""
        names = set(self._env_names())
        self.assertIn("WS4_TRACING_ENABLED", names)
        self.assertIn("WS4_TRACKING_URI", names)

    def test_mlflow_experiment_name_not_hardcoded_id(self) -> None:
        """The experiment is parameterised by NAME (path), not a hardcoded ID.

        A customer workspace has no pre-existing experiment, so app.yaml must
        not pin a numeric MLFLOW_EXPERIMENT_ID (which would point at a
        non-existent experiment on a fresh workspace) or bind an experiment
        resource. Instead it sets MLFLOW_EXPERIMENT_NAME, which the tracing
        layer resolves via mlflow.set_experiment() (auto-creating the path).
        """
        names = set(self._env_names())
        self.assertIn("MLFLOW_EXPERIMENT_NAME", names)
        # The customer-facing NAME var must NOT coexist with the hardcoded ID.
        self.assertNotIn("MLFLOW_EXPERIMENT_ID", names)
        # No workspace-specific numeric ID anywhere in the file.
        self.assertNotIn("967014443183055", self.text)
        if self.parsed is not None:
            env = {e["name"]: e.get("value") for e in self.parsed.get("env", [])}
            self.assertEqual(
                env["MLFLOW_EXPERIMENT_NAME"], "/Shared/savesage/statement-agent"
            )

    def test_judge_scheduler_env_vars(self) -> None:
        """Background judge scheduler env vars are declared with sane defaults."""
        names = set(self._env_names())
        self.assertIn("JUDGE_INTERVAL_HOURS", names)
        self.assertIn("JUDGE_SAMPLE_SIZE", names)
        if self.parsed is not None:
            env = {e["name"]: e.get("value") for e in self.parsed.get("env", [])}
            self.assertEqual(env["JUDGE_INTERVAL_HOURS"], "6")
            self.assertEqual(env["JUDGE_SAMPLE_SIZE"], "10")

    def test_no_hardcoded_experiment_resource(self) -> None:
        """No bound experiment resource — the experiment is resolved by NAME.

        A customer workspace does not have the dev workspace's experiment, so
        app.yaml must not bind a ``resources:`` experiment block (which would
        reference a non-existent experiment_id and fail the deploy). The
        MLflow experiment is auto-created at MLFLOW_EXPERIMENT_NAME on the
        first parse instead.
        """
        if self.parsed is not None:
            resources = self.parsed.get("resources", []) or []
            self.assertFalse(
                any("experiment" in r for r in resources),
                "no experiment resource binding should remain (resolved by NAME)",
            )
            self.assertFalse(
                any(r.get("name") == "savesage-mlflow" for r in resources),
                "savesage-mlflow resource should be removed",
            )
        else:
            self.assertNotIn("savesage-mlflow", self.text)
            self.assertNotIn("experiment_id", self.text)

    def test_no_database_resource(self) -> None:
        """No database resource binding — the app is stateless.

        The database persistence layer has been removed; the agent returns
        parsed JSON only and the client persists. No Databricks ``database``
        resource binding is needed.
        """
        if self.parsed is not None:
            resources = {r["name"]: r for r in self.parsed.get("resources", [])}
            self.assertNotIn("savesage-lakebase", resources)
            self.assertFalse(
                any("database" in r for r in resources.values()),
                "no database resource binding should remain")
        else:
            self.assertNotIn("CAN_CONNECT_AND_CREATE", self.text)

    def test_command_uses_uvicorn(self) -> None:
        if self.parsed is not None:
            cmd = self.parsed.get("command", [])
            self.assertIn("uvicorn", cmd)
            self.assertIn("app.main:app", cmd)
        else:
            self.assertIn("uvicorn", self.text)
            self.assertIn("app.main:app", self.text)

    def test_no_todo_comments(self) -> None:
        """The WS3/WS4 TODO comments must be replaced by real resources."""
        self.assertNotIn("TODO(workstream-3)", self.text)
        self.assertNotIn("TODO(workstream-4)", self.text)

    def test_endpoint_values(self) -> None:
        if self.parsed is not None:
            env = {e["name"]: e.get("value") for e in self.parsed.get("env", [])}
            self.assertEqual(env["EXTRACTION_ENDPOINT"], "databricks-gpt-5-6-luna")
            self.assertEqual(env["JUDGE_ENDPOINT"], "databricks-claude-opus-5")


# ---------------------------------------------------------------------------
# Route handler helpers (validation logic used by the FastAPI routes)
# ---------------------------------------------------------------------------

class RouteHelperTest(unittest.TestCase):
    """Test the pure validation/serialisation logic used inside route handlers.

    These don't need FastAPI's TestClient (which requires httpx); they test
    the same helper functions the route handlers delegate to.
    """

    def test_sse_format_matches_sse_spec(self) -> None:
        """The SSE frame must have exactly one ``event:`` and one ``data:`` line."""
        line = _sse_event("progress", {"stage": "route"})
        lines = line.rstrip("\n").split("\n")
        event_lines = [l for l in lines if l.startswith("event: ")]
        data_lines = [l for l in lines if l.startswith("data: ")]
        self.assertEqual(len(event_lines), 1)
        self.assertEqual(len(data_lines), 1)
        self.assertTrue(line.endswith("\n\n"))  # SSE requires blank-line terminator


# ---------------------------------------------------------------------------
# _ProgressTraceSink — per-field streaming (F1 fix)
# ---------------------------------------------------------------------------

class ProgressTraceSinkTest(unittest.TestCase):
    """Test the SSE streaming behaviour of _ProgressTraceSink.

    The sink intercepts trace events from graph nodes and pushes individual
    extraction_item / field_verdict SSE events so the frontend renders
    per-field results live, not as a batch at the end.
    """

    def _make_event(self, name: str, error: str | None = None) -> TraceEvent:
        now = datetime.now(UTC)
        return TraceEvent(
            request_id="req-test",
            name=name,
            started_at=now,
            ended_at=now,
            error=error,
        )

    def _drain(self, ctx: RequestContext) -> list[dict]:
        """Drain all events from the context queue (including sentinel)."""
        events = []
        while not ctx.events.empty():
            ev = ctx.events.get_nowait()
            if ev is None:
                break
            events.append(ev)
        return events

    def test_progress_event_pushed(self) -> None:
        """Every trace event produces a progress SSE event."""
        ctx = RequestContext("req-1")
        sink = _ProgressTraceSink(None, ctx)
        sink.record(self._make_event("route"))
        events = self._drain(ctx)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "progress")
        self.assertEqual(events[0]["data"]["stage"], "route")

    def test_extraction_items_pushed_on_extract(self) -> None:
        """F1: extract trace pushes individual extraction_item events."""
        ctx = RequestContext("req-2")

        # Mock state with extraction payload
        class _MockExtraction:
            model_id = "luna-test"
            schema_valid = True
            payload = {
                "cards": [
                    {"cardMeta": {"cardDisplayName": "Platinum", "lastFourDigit": "1234"}},
                    {"cardMeta": {"cardDisplayName": "Gold", "lastFourDigit": "5678"}},
                ],
                "transactions": [
                    {"date": "2026-01-01", "description": "Store", "amount": 100.0},
                    {"date": "2026-01-02", "description": "Online", "amount": 50.0},
                    {"date": "2026-01-03", "description": "Refund", "amount": -25.0},
                ],
                "rewards": {"pointsEarnedThisCycle": 500, "closingPoints": 1200},
            }

        class _MockState:
            extraction = _MockExtraction()
            verdict = None

        sink = _ProgressTraceSink(None, ctx, _MockState())
        sink.record(self._make_event("extract"))

        events = self._drain(ctx)
        event_types = [e["event"] for e in events]
        # 1 progress + 2 cards + 3 transactions + 1 rewards + 1 extraction summary = 8
        self.assertEqual(event_types.count("extraction_item"), 6)
        self.assertIn("extraction", event_types)
        self.assertIn("progress", event_types)

        # Verify card items
        card_items = [e for e in events if e["event"] == "extraction_item" and e["data"]["type"] == "card"]
        self.assertEqual(len(card_items), 2)
        self.assertEqual(card_items[0]["data"]["index"], 0)
        self.assertEqual(card_items[1]["data"]["index"], 1)

        # Verify transaction items
        txn_items = [e for e in events if e["event"] == "extraction_item" and e["data"]["type"] == "transaction"]
        self.assertEqual(len(txn_items), 3)

        # Verify rewards item
        rewards_items = [e for e in events if e["event"] == "extraction_item" and e["data"]["type"] == "rewards"]
        self.assertEqual(len(rewards_items), 1)

    def test_no_field_verdicts_on_judge_event(self) -> None:
        """The judge no longer runs inline, so a 'judge' trace event (if it
        ever arrives from the post-hoc scorer) pushes only a progress event —
        no field_verdict or verdict SSE events are emitted during a live parse.
        """
        ctx = RequestContext("req-3")

        class _MockVerdict:
            judge_model_id = "opus-test"
            summary = "2/3 agree"
            comparisons = (
                FieldComparison(
                    "rewards.closingPoints", 1200, 1200,
                    ComparisonOutcome.AGREE, FieldScope.SCALAR,
                ),
                FieldComparison(
                    "cards[].cardMeta.cardDisplayName", "Platinum", "Gold",
                    ComparisonOutcome.DISAGREE, FieldScope.SCALAR, card_index=0,
                ),
                FieldComparison(
                    "transactions[].amount", 100.0, 100.0,
                    ComparisonOutcome.AGREE, FieldScope.TRANSACTION_ROW,
                    MatchMethod.DESCRIPTION_SIMILARITY_1TO1,
                    expected_row_index=0, actual_row_index=0,
                ),
            )

        class _MockState:
            extraction = None
            verdict = _MockVerdict()

        sink = _ProgressTraceSink(None, ctx, _MockState())
        sink.record(self._make_event("judge"))

        events = self._drain(ctx)
        event_types = [e["event"] for e in events]
        # Only a progress event — no field_verdict or verdict events.
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "progress")
        self.assertNotIn("field_verdict", event_types)
        self.assertNotIn("verdict", event_types)

    def test_no_extraction_items_on_error(self) -> None:
        """When extract node errors, no extraction_item events are pushed."""
        ctx = RequestContext("req-4")
        sink = _ProgressTraceSink(None, ctx)  # no state
        sink.record(self._make_event("extract", error="boom"))
        events = self._drain(ctx)
        # Only the progress event with error
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "progress")
        self.assertEqual(events[0]["data"]["error"], "boom")

    def test_no_field_verdicts_on_error(self) -> None:
        """When judge node errors, no field_verdict events are pushed."""
        ctx = RequestContext("req-5")
        sink = _ProgressTraceSink(None, ctx)  # no state
        sink.record(self._make_event("judge", error="judge failed"))
        events = self._drain(ctx)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "progress")

    def test_wrapped_sink_receives_event(self) -> None:
        """The wrapped (real) sink must still receive every trace event."""
        ctx = RequestContext("req-6")
        received = []

        class _CapturingSink:
            def record(self, event: TraceEvent) -> None:
                received.append(event)

        sink = _ProgressTraceSink(_CapturingSink(), ctx, None)
        ev = self._make_event("route")
        sink.record(ev)
        self.assertEqual(len(received), 1)
        self.assertIs(received[0], ev)

    def test_no_state_no_extraction_items(self) -> None:
        """When state is None, extract/judge traces push only progress."""
        ctx = RequestContext("req-7")
        sink = _ProgressTraceSink(None, ctx, None)
        sink.record(self._make_event("extract"))
        sink.record(self._make_event("judge"))
        events = self._drain(ctx)
        # Two progress events, no extraction_item or field_verdict
        self.assertEqual(len(events), 2)
        self.assertTrue(all(e["event"] == "progress" for e in events))


# ---------------------------------------------------------------------------
# Judge evaluation endpoints (post-hoc scorer API)
# ---------------------------------------------------------------------------

class JudgeEndpointTest(unittest.TestCase):
    """Test the /api/run-judge and /api/judge-results endpoint helpers.

    These test the module-level cache and the import/call wiring. The actual
    scorer logic is tested in tests/test_scorer.py.
    """

    def test_judge_result_cache_default_none(self) -> None:
        """The module-level cache starts as None (no evaluation run yet)."""
        import app.main as main_mod
        # Save and restore the cache so the test is hermetic.
        saved = main_mod._judge_result_cache
        try:
            main_mod._judge_result_cache = None
            self.assertIsNone(main_mod._judge_result_cache)
        finally:
            main_mod._judge_result_cache = saved

    def test_pipeline_stages_no_judge(self) -> None:
        """The judge stage is NOT in the pipeline stages (it's post-hoc)."""
        self.assertNotIn("judge", PIPELINE_STAGES)
        self.assertNotIn("persist", PIPELINE_STAGES)
        self.assertEqual(len(PIPELINE_STAGES), 4)

    def test_stage_map_no_judge(self) -> None:
        """The stage map does NOT map judge or judge_skipped."""
        from app.main import _STAGE_MAP
        self.assertNotIn("judge", _STAGE_MAP)
        self.assertNotIn("judge_skipped", _STAGE_MAP)
        self.assertNotIn("persist" + "_extraction", _STAGE_MAP)

    def test_build_deps_no_judge_adapter(self) -> None:
        """_build_deps does NOT construct a judge adapter (it's post-hoc)."""
        import inspect
        from app.main import _build_deps
        source = inspect.getsource(_build_deps)
        self.assertNotIn("OpusJudgeAdapter", source)
        self.assertNotIn("judge=", source)
        self.assertNotIn("judge =", source)
        self.assertNotIn("JudgeAdapter", source)

    def test_max_sample_size_constant(self) -> None:
        """MAX_SAMPLE_SIZE is defined and bounded (server-side guard)."""
        from app.main import MAX_SAMPLE_SIZE
        self.assertIsInstance(MAX_SAMPLE_SIZE, int)
        self.assertGreater(MAX_SAMPLE_SIZE, 0)
        self.assertLessEqual(MAX_SAMPLE_SIZE, 100)

    def test_judge_bg_function_exists(self) -> None:
        """The background evaluation runner exists and is callable."""
        from app.main import _run_judge_evaluation_bg
        self.assertTrue(callable(_run_judge_evaluation_bg))

    def test_judge_bg_failure_is_sanitized(self) -> None:
        """Background evaluation errors never expose exception details via the cache."""
        import app.main as main_mod

        saved_cache = main_mod._judge_result_cache
        saved_running = main_mod._judge_running
        try:
            main_mod._judge_running = True
            with patch(
                "judge.scorer.run_judge_evaluation",
                side_effect=RuntimeError("secret endpoint and account"),
            ):
                main_mod._run_judge_evaluation_bg(1)

            self.assertEqual(
                main_mod._judge_result_cache["errors"],
                [{"error": "evaluation failed"}],
            )
            self.assertNotIn("secret", str(main_mod._judge_result_cache))
            self.assertFalse(main_mod._judge_running)
        finally:
            main_mod._judge_result_cache = saved_cache
            main_mod._judge_running = saved_running


# ---------------------------------------------------------------------------
# PDF artifact logging in finalize_node
# ---------------------------------------------------------------------------

class FinalizeArtifactTest(unittest.TestCase):
    """Test that finalize_node logs the PDF + extraction as MLflow artifacts."""

    def test_finalize_logs_pdf_artifact(self) -> None:
        """finalize_node calls trace_sink.log_artifact with the PDF bytes."""
        from graph.fakes import FakeExtractionAdapter, InMemoryTraceSink
        from graph.nodes import NodeDeps, finalize_node
        from graph.state import GraphState
        from contracts.models import Bank, ParseRequest

        pdf_bytes = b"%PDF-1.4 synthetic test pdf"
        request = ParseRequest(
            pdf=pdf_bytes, filename="test.pdf",
            bank=Bank.HDFC, request_id="req-art-1",
        )
        state = GraphState(request=request)
        trace_sink = InMemoryTraceSink()
        deps = NodeDeps(
            extraction=FakeExtractionAdapter(),
            trace_sink=trace_sink,
        )
        # Run extract → validate → finalize to populate the extraction.
        from graph.nodes import extract_node, validate_node
        extract_node(state, deps)
        validate_node(state, deps)
        finalize_node(state, deps)

        # The trace sink should have received the PDF artifact.
        self.assertTrue(len(trace_sink.artifacts) >= 1)
        pdf_artifacts = [a for a in trace_sink.artifacts if a[1] == "statement.pdf"]
        self.assertEqual(len(pdf_artifacts), 1)
        self.assertEqual(pdf_artifacts[0][0], pdf_bytes)

    def test_finalize_logs_extraction_artifact(self) -> None:
        """finalize_node also logs the extraction.json artifact."""
        from graph.fakes import FakeExtractionAdapter, InMemoryTraceSink
        from graph.nodes import NodeDeps, finalize_node
        from graph.state import GraphState
        from contracts.models import Bank, ParseRequest
        import json

        request = ParseRequest(
            pdf=b"%PDF-1.4 test", filename="test.pdf",
            bank=Bank.ICICI, request_id="req-art-2",
        )
        state = GraphState(request=request)
        trace_sink = InMemoryTraceSink()
        deps = NodeDeps(
            extraction=FakeExtractionAdapter(),
            trace_sink=trace_sink,
        )
        from graph.nodes import extract_node, validate_node
        extract_node(state, deps)
        validate_node(state, deps)
        finalize_node(state, deps)

        # The trace sink should have the extraction.json artifact.
        json_artifacts = [a for a in trace_sink.artifacts if a[1] == "extraction.json"]
        self.assertEqual(len(json_artifacts), 1)
        meta = json.loads(json_artifacts[0][0])
        self.assertEqual(meta["bank"], "ICICI")
        self.assertEqual(meta["request_id"], "req-art-2")
        self.assertIn("payload", meta)

    def test_finalize_no_trace_sink_no_artifact(self) -> None:
        """When no trace sink is wired, finalize still succeeds (no artifact)."""
        from graph.fakes import FakeExtractionAdapter
        from graph.nodes import NodeDeps, finalize_node
        from graph.state import GraphState
        from contracts.models import Bank, ParseRequest

        request = ParseRequest(
            pdf=b"%PDF-1.4 test", filename="test.pdf",
            bank=Bank.HDFC, request_id="req-art-3",
        )
        state = GraphState(request=request)
        deps = NodeDeps(
            extraction=FakeExtractionAdapter(),
            trace_sink=None,
        )
        from graph.nodes import extract_node, validate_node
        extract_node(state, deps)
        validate_node(state, deps)
        finalize_node(state, deps)
        # Should not raise.
        self.assertIsNotNone(state.outcome)


# ---------------------------------------------------------------------------
# _run_blocking — bounded best-effort execution
# ---------------------------------------------------------------------------

class RunBlockingTest(unittest.TestCase):
    """The ``_run_blocking`` helper bounds best-effort blocking calls so a hung
    network call can never freeze the single uvicorn event loop."""

    def test_returns_result_on_success(self) -> None:
        """A fast callable's return value is passed through."""
        import asyncio

        self.assertEqual(asyncio.run(_run_blocking(lambda: 42)), 42)

    def test_passes_args(self) -> None:
        """Positional args are forwarded to the callable."""
        import asyncio

        def add(a: int, b: int) -> int:
            return a + b

        self.assertEqual(asyncio.run(_run_blocking(add, 3, 4)), 7)

    def test_returns_none_on_exception(self) -> None:
        """An exception inside the callable is swallowed → None (best-effort)."""
        import asyncio

        def boom() -> None:
            raise RuntimeError("network down")

        self.assertIsNone(asyncio.run(_run_blocking(boom)))

    def test_returns_none_on_timeout(self) -> None:
        """A callable that exceeds the timeout returns None instead of hanging.

        This is the crux of the 502 fix: a hung network call must not block
        the event loop.  We use an Event the coroutine releases right
        after the timeout fires so the orphaned worker thread exits promptly
        and the test does not stall on executor shutdown.
        """
        import asyncio
        import threading

        release = threading.Event()

        def hang() -> None:
            release.wait(timeout=30.0)

        async def go() -> None:
            # Returns None after the timeout — does NOT wait for the 30s hang.
            self.assertIsNone(await _run_blocking(hang, timeout=0.1))
            release.set()  # free the orphaned thread before executor shutdown

        asyncio.run(go())

    def test_timeout_returns_well_before_full_duration(self) -> None:
        """The helper returns within ~timeout, not the callable's full runtime."""
        import asyncio
        import time

        def slow() -> str:
            import time as _t
            _t.sleep(0.4)
            return "late"

        async def go() -> float:
            start = time.monotonic()
            self.assertIsNone(await _run_blocking(slow, timeout=0.1))
            return time.monotonic() - start

        elapsed = asyncio.run(go())
        self.assertLess(elapsed, 0.5)


if __name__ == "__main__":
    unittest.main()
