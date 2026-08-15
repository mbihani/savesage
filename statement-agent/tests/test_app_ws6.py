"""Stdlib-only tests for the workstream-6 FastAPI app (routes, SSE, feedback, app.yaml).

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

from app.main import (
    PIPELINE_STAGES,
    RequestContext,
    _comparison_to_dict,
    _new_request_id,
    _sse_event,
    _validate_feedback_body,
)
from contracts.models import (
    ComparisonOutcome,
    FieldComparison,
    FieldScope,
    MatchMethod,
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

class FeedbackValidationTest(unittest.TestCase):
    def test_accept_scalar(self) -> None:
        v = _validate_feedback_body({
            "field_path": "rewards.closingPoints",
            "disposition": "ACCEPT",
            "original_value": 42,
        })
        self.assertTrue(v["accepted"])
        self.assertEqual(v["field_path"], "rewards.closingPoints")
        self.assertEqual(v["disposition"], "ACCEPT")

    def test_correct_scalar(self) -> None:
        v = _validate_feedback_body({
            "field_path": "rewards.closingPoints",
            "disposition": "CORRECT",
            "original_value": 42,
            "corrected_value": 99,
        })
        self.assertFalse(v["accepted"])
        self.assertEqual(v["corrected_value"], 99)

    def test_correct_card_field(self) -> None:
        v = _validate_feedback_body({
            "field_path": "cards.0.cardMeta.cardDisplayName",
            "disposition": "CORRECT",
            "original_value": "WRONG",
            "corrected_value": "RIGHT",
        })
        self.assertEqual(v["field_path"], "cards.0.cardMeta.cardDisplayName")
        self.assertFalse(v["accepted"])

    def test_correct_transaction_field(self) -> None:
        v = _validate_feedback_body({
            "field_path": "transactions.14.amount",
            "disposition": "CORRECT",
            "original_value": 100.0,
            "corrected_value": 200.0,
        })
        self.assertEqual(v["field_path"], "transactions.14.amount")

    def test_disposition_case_insensitive(self) -> None:
        v = _validate_feedback_body({
            "field_path": "rewards.closingPoints",
            "disposition": "accept",
        })
        self.assertTrue(v["accepted"])

    def test_reject_template_path(self) -> None:
        """Template paths (with ``[]``) are not canonical and must be rejected."""
        with self.assertRaises(ValueError):
            _validate_feedback_body({
                "field_path": "cards[].cardMeta.cardDisplayName",
                "disposition": "ACCEPT",
            })

    def test_reject_json_pointer_path(self) -> None:
        with self.assertRaises(ValueError):
            _validate_feedback_body({
                "field_path": "/cards/0/cardMeta/cardDisplayName",
                "disposition": "ACCEPT",
            })

    def test_reject_wildcard_path(self) -> None:
        with self.assertRaises(ValueError):
            _validate_feedback_body({
                "field_path": "cards.*.cardMeta.cardDisplayName",
                "disposition": "ACCEPT",
            })

    def test_reject_leading_zero_index(self) -> None:
        with self.assertRaises(ValueError):
            _validate_feedback_body({
                "field_path": "transactions.01.amount",
                "disposition": "ACCEPT",
            })

    def test_reject_unknown_disposition(self) -> None:
        with self.assertRaises(ValueError):
            _validate_feedback_body({
                "field_path": "rewards.closingPoints",
                "disposition": "MAYBE",
            })

    def test_reject_empty_disposition(self) -> None:
        with self.assertRaises(ValueError):
            _validate_feedback_body({
                "field_path": "rewards.closingPoints",
                "disposition": "",
            })

    def test_correct_requires_corrected_value(self) -> None:
        with self.assertRaises(ValueError):
            _validate_feedback_body({
                "field_path": "rewards.closingPoints",
                "disposition": "CORRECT",
                "original_value": 42,
                # corrected_value missing
            })

    def test_accept_does_not_require_corrected_value(self) -> None:
        v = _validate_feedback_body({
            "field_path": "rewards.closingPoints",
            "disposition": "ACCEPT",
        })
        self.assertIsNone(v["corrected_value"])

    def test_default_actor(self) -> None:
        v = _validate_feedback_body({
            "field_path": "rewards.closingPoints",
            "disposition": "ACCEPT",
        })
        self.assertEqual(v["actor"], "web-ui")

    def test_custom_actor(self) -> None:
        v = _validate_feedback_body({
            "field_path": "rewards.closingPoints",
            "disposition": "ACCEPT",
            "actor": "admin@example.com",
        })
        self.assertEqual(v["actor"], "admin@example.com")


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
            "route", "extract", "validate", "persist", "judge", "finalize",
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

    def test_lakebase_env_vars(self) -> None:
        """WS3 env vars for the Lakebase connection."""
        names = set(self._env_names())
        self.assertIn("ENDPOINT_NAME", names)
        self.assertIn("PGSSLMODE", names)

    def test_mlflow_env_vars(self) -> None:
        """WS4 env vars for MLflow tracing."""
        names = set(self._env_names())
        self.assertIn("WS4_TRACING_ENABLED", names)
        self.assertIn("WS4_TRACKING_URI", names)

    def test_mlflow_experiment_resource(self) -> None:
        if self.parsed is not None:
            resources = {r["name"]: r for r in self.parsed.get("resources", [])}
            self.assertIn("savesage-statement-agent-mlflow", resources)
            exp = resources["savesage-statement-agent-mlflow"]
            self.assertIn("experiment", exp)
            self.assertEqual(exp["experiment"]["permission"], "CAN_EDIT")
            self.assertTrue(exp["experiment"]["experiment_id"])
        else:
            self.assertIn("experiment", self.text)
            self.assertIn("CAN_EDIT", self.text)

    def test_lakebase_database_resource(self) -> None:
        if self.parsed is not None:
            resources = {r["name"]: r for r in self.parsed.get("resources", [])}
            self.assertIn("savesage-lakebase", resources)
            db = resources["savesage-lakebase"]
            self.assertIn("database", db)
            self.assertEqual(db["database"]["permission"], "CAN_CONNECT_AND_CREATE")
            self.assertTrue(db["database"]["instance_name"])
            self.assertTrue(db["database"]["database_name"])
        else:
            self.assertIn("database", self.text)
            self.assertIn("CAN_CONNECT_AND_CREATE", self.text)

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

    def test_feedback_accept_round_trip(self) -> None:
        """A valid ACCEPT body produces a FieldFeedback with accepted=True."""
        from contracts.models import FieldFeedback

        v = _validate_feedback_body({
            "field_path": "rewards.pointsEarnedThisCycle",
            "disposition": "ACCEPT",
            "original_value": 10,
        })
        fb = FieldFeedback(
            request_id="req-1",
            field_path=v["field_path"],
            original_value=v["original_value"],
            corrected_value=None,  # ACCEPT → no corrected value
            accepted=v["accepted"],
            actor=v["actor"],
            timestamp=datetime.now(UTC),
        )
        self.assertTrue(fb.accepted)
        self.assertEqual(fb.disposition.value, "ACCEPT")

    def test_feedback_correct_round_trip(self) -> None:
        """A valid CORRECT body produces a FieldFeedback with accepted=False."""
        from contracts.models import FieldFeedback

        v = _validate_feedback_body({
            "field_path": "cards.0.cardMeta.lastFourDigit",
            "disposition": "CORRECT",
            "original_value": "0000",
            "corrected_value": "1234",
        })
        fb = FieldFeedback(
            request_id="req-2",
            field_path=v["field_path"],
            original_value=v["original_value"],
            corrected_value=v["corrected_value"],
            accepted=v["accepted"],
            actor=v["actor"],
            timestamp=datetime.now(UTC),
        )
        self.assertFalse(fb.accepted)
        self.assertEqual(fb.corrected_value, "1234")
        self.assertEqual(fb.disposition.value, "CORRECT")

    def test_sse_format_matches_sse_spec(self) -> None:
        """The SSE frame must have exactly one ``event:`` and one ``data:`` line."""
        line = _sse_event("progress", {"stage": "route"})
        lines = line.rstrip("\n").split("\n")
        event_lines = [l for l in lines if l.startswith("event: ")]
        data_lines = [l for l in lines if l.startswith("data: ")]
        self.assertEqual(len(event_lines), 1)
        self.assertEqual(len(data_lines), 1)
        self.assertTrue(line.endswith("\n\n"))  # SSE requires blank-line terminator


if __name__ == "__main__":
    unittest.main()
