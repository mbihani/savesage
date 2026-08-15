"""Stdlib unit tests for span-tree assembly and telemetry PII redaction (WS4).

No mlflow import — these exercise the pure logic in harness/tracing_spans.py,
which is the part that must be correct without a live MLflow.
"""

from datetime import UTC, datetime
import unittest

from contracts.models import TraceEvent
from harness.tracing_spans import (
    SpanTreeBuilder,
    redact_telemetry_attributes,
    span_type_for,
    to_ns,
)


def _evt(name, rid="req-1", parent=None, sid=None, attrs=None, error=None, offset=0):
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    started = datetime.fromtimestamp(base.timestamp() + offset, tz=UTC)
    ended = datetime.fromtimestamp(base.timestamp() + offset + 1, tz=UTC)
    return TraceEvent(
        request_id=rid, name=name, started_at=started, ended_at=ended,
        attributes=attrs or {}, error=error, span_id=sid, parent_span_id=parent,
    )


class SpanTreeBuilderTest(unittest.TestCase):
    def test_returns_none_until_root_arrives(self):
        b = SpanTreeBuilder()
        child = _evt("extraction", sid="s-extract", parent="s-parse", offset=1)
        self.assertIsNone(b.feed(child))
        self.assertEqual(b.pending(), {"req-1"})

    def test_flushes_ordered_tree_on_root(self):
        b = SpanTreeBuilder()
        b.feed(_evt("extraction", sid="s-extract", parent="s-parse", offset=1))
        b.feed(_evt("validation", sid="s-valid", parent="s-parse", offset=2))
        b.feed(_evt("persistence", sid="s-persist", parent="s-parse", offset=3))
        b.feed(_evt("judging", sid="s-judge", parent="s-parse", offset=4))
        root = _evt("parse", sid="s-parse", parent=None, offset=0)
        ops = b.feed(root)
        self.assertIsNotNone(ops)
        self.assertEqual(len(ops), 5)
        # Root first, then children pre-order.
        self.assertEqual(ops[0].event.name, "parse")
        self.assertTrue(ops[0].is_root)
        self.assertIsNone(ops[0].event.parent_span_id)
        child_names = [op.event.name for op in ops[1:]]
        self.assertCountEqual(child_names, ["extraction", "validation", "persistence", "judging"])
        # All children share the root's span_id as parent_span_id.
        for op in ops[1:]:
            self.assertFalse(op.is_root)
            self.assertEqual(op.event.parent_span_id, "s-parse")
        # After flush, no longer pending.
        self.assertEqual(b.pending(), set())

    def test_root_span_id_propagates_to_children_parents(self):
        b = SpanTreeBuilder()
        b.feed(_evt("extraction", sid="s-extract", parent="s-parse", offset=1))
        root = _evt("parse", sid="s-parse", parent=None, offset=0)
        ops = b.feed(root)
        self.assertEqual(ops[0].event.span_id, "s-parse")
        self.assertEqual(ops[1].event.parent_span_id, "s-parse")
        self.assertEqual(ops[1].event.span_id, "s-extract")

    def test_late_arrival_after_flush_is_dropped(self):
        b = SpanTreeBuilder()
        root = _evt("parse", sid="s-parse", parent=None, offset=0)
        b.feed(root)
        late = _evt("extraction", sid="s-extract", parent="s-parse", offset=1)
        self.assertIsNone(b.feed(late))

    def test_no_root_yields_no_flush(self):
        b = SpanTreeBuilder()
        b.feed(_evt("extraction", sid="s-extract", parent="s-parse", offset=1))
        self.assertEqual(b.pending(), {"req-1"})


class SpanTypeMapTest(unittest.TestCase):
    def test_phase_to_span_type(self):
        self.assertEqual(span_type_for("parse"), "CHAIN")
        self.assertEqual(span_type_for("extraction"), "LLM")
        self.assertEqual(span_type_for("validation"), "GUARDRAIL")
        self.assertEqual(span_type_for("persistence"), "TOOL")
        self.assertEqual(span_type_for("judging"), "EVALUATOR")
        self.assertEqual(span_type_for("unknown_phase"), "UNKNOWN")


class RedactTelemetryTest(unittest.TestCase):
    def test_pii_key_substrings_are_redacted(self):
        out = redact_telemetry_attributes({
            "card_number": "4111111111111111",
            "cardholder_name": "Jane Doe",
            "description": "ACME CORP PAYMENT",
            "filename": "secret.pdf",
            "raw_response": "stuff",
            "schema_valid": True,
            "bank": "HDFC",
            "row_count": 42,
        })
        self.assertEqual(out["card_number"], "[REDACTED]")
        self.assertEqual(out["cardholder_name"], "[REDACTED]")
        self.assertEqual(out["description"], "[REDACTED]")
        self.assertEqual(out["filename"], "[REDACTED]")
        self.assertEqual(out["raw_response"], "[REDACTED]")
        self.assertEqual(out["schema_valid"], True)
        self.assertEqual(out["bank"], "HDFC")
        self.assertEqual(out["row_count"], 42)

    def test_card_number_in_string_value_is_scrubbed(self):
        out = redact_telemetry_attributes({"note": "card 4111 1111 1111 1111 here"})
        self.assertNotIn("4111", str(out["note"]))
        self.assertIn("[REDACTED_CARD]", str(out["note"]))

    def test_long_strings_are_truncated(self):
        out = redact_telemetry_attributes({"x": "a" * 500})
        self.assertLess(len(out["x"]), 500)
        self.assertTrue(str(out["x"]).endswith("...[truncated]"))


class ToNsTest(unittest.TestCase):
    def test_tz_aware_datetime_to_nanoseconds(self):
        dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        self.assertEqual(to_ns(dt), int(dt.timestamp() * 1_000_000_000))

    def test_tz_naive_assumed_utc(self):
        dt = datetime(2026, 1, 1, 0, 0, 0)
        self.assertEqual(to_ns(dt), int(dt.replace(tzinfo=UTC).timestamp() * 1_000_000_000))


if __name__ == "__main__":
    unittest.main()
