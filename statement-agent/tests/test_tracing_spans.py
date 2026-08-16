"""Stdlib unit tests for span-tree assembly and telemetry PII redaction (WS4).

No mlflow import — these exercise the pure logic in harness/tracing_spans.py.
Covers: root identification by explicit stage name (B3), malformed graph
handling (B3), bounded buffers (B2), and recursive PII redaction (B4).
"""

from datetime import UTC, datetime
import unittest

from contracts.models import TraceEvent
from harness.tracing_spans import (
    ROOT_STAGE_DEFAULT,
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
        self.assertEqual(ops[0].event.name, "parse")
        self.assertTrue(ops[0].is_root)
        self.assertIsNone(ops[0].event.parent_span_id)
        child_names = [op.event.name for op in ops[1:]]
        self.assertCountEqual(child_names, ["extraction", "validation", "persistence", "judging"])
        for op in ops[1:]:
            self.assertFalse(op.is_root)
            self.assertEqual(op.event.parent_span_id, "s-parse")
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


class RootInvariantTest(unittest.TestCase):
    """B3: root is identified by explicit stage name, not 'first event lacking a parent'."""

    def test_phase_event_with_missing_parent_does_not_flush_early(self):
        b = SpanTreeBuilder()
        # A phase event with parent_span_id=None (linkage bug) is an orphan,
        # NOT a root — it must NOT trigger a flush.
        orphan = _evt("extraction", sid="s-extract", parent=None, offset=1)
        self.assertIsNone(b.feed(orphan))
        self.assertEqual(b.pending(), {"req-1"})
        # The real root arrives later and flushes correctly.
        root = _evt("parse", sid="s-parse", parent=None, offset=0)
        ops = b.feed(root)
        self.assertIsNotNone(ops)
        # The orphan (parent=None, name!="parse") is NOT in the tree (disconnected).
        names = [op.event.name for op in ops]
        self.assertEqual(names, ["parse"])

    def test_non_root_name_with_parent_none_is_orphan_not_root(self):
        b = SpanTreeBuilder()
        # "validation" with parent=None is NOT the root stage.
        orphan = _evt("validation", sid="s-v", parent=None, offset=0)
        self.assertIsNone(b.feed(orphan))
        self.assertEqual(b.pending(), {"req-1"})

    def test_custom_root_stage(self):
        b = SpanTreeBuilder(root_stage="my_root")
        b.feed(_evt("child", sid="s-c", parent="s-r", offset=1))
        ops = b.feed(_evt("my_root", sid="s-r", parent=None, offset=0))
        self.assertIsNotNone(ops)
        self.assertEqual(len(ops), 2)
        self.assertEqual(ops[0].event.name, "my_root")


class MalformedGraphTest(unittest.TestCase):
    """B3: duplicate ids, self-references, and cycles don't crash or loop forever."""

    def test_self_referential_id_dropped(self):
        b = SpanTreeBuilder()
        # span_id == parent_span_id (self-ref) on a child.
        b.feed(_evt("extraction", sid="s-self", parent="s-self", offset=1))
        ops = b.feed(_evt("parse", sid="s-parse", parent=None, offset=0))
        self.assertIsNotNone(ops)
        # Only the root; the self-ref child is dropped.
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0].event.name, "parse")

    def test_self_referential_event_buffered_not_flushed(self):
        # A self-referential event (span_id == parent_span_id) has a non-None
        # parent_span_id, so it is NOT the root and never triggers a flush.
        b = SpanTreeBuilder()
        self_ref = _evt("parse", sid="s-x", parent="s-x", offset=0)
        self.assertIsNone(b.feed(self_ref))  # buffered, not flushed
        self.assertEqual(b.pending(), {"req-1"})

    def test_duplicate_span_id_dropped(self):
        b = SpanTreeBuilder()
        b.feed(_evt("extraction", sid="s-dup", parent="s-parse", offset=1))
        b.feed(_evt("validation", sid="s-dup", parent="s-parse", offset=2))  # duplicate id
        ops = b.feed(_evt("parse", sid="s-parse", parent=None, offset=0))
        self.assertIsNotNone(ops)
        # Root + exactly one of the duplicates (the other is dropped).
        self.assertEqual(len(ops), 2)

    def test_two_node_cycle_does_not_loop_forever(self):
        b = SpanTreeBuilder()
        # A -> B -> A cycle (A's parent is B, B's parent is A's span_id).
        b.feed(_evt("phaseA", sid="s-a", parent="s-b", offset=1))
        b.feed(_evt("phaseB", sid="s-b", parent="s-a", offset=2))
        ops = b.feed(_evt("parse", sid="s-parse", parent=None, offset=0))
        # Root only; the cycle is detected and broken (no infinite loop, no crash).
        self.assertIsNotNone(ops)
        self.assertEqual(ops[0].event.name, "parse")

    def test_missing_parent_orphan_omitted(self):
        b = SpanTreeBuilder()
        # Child whose parent_span_id references a non-existent span.
        b.feed(_evt("extraction", sid="s-e", parent="s-missing", offset=1))
        ops = b.feed(_evt("parse", sid="s-parse", parent=None, offset=0))
        # Root only; the orphan (parent not found) is omitted.
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0].event.name, "parse")


class BoundedBufferTest(unittest.TestCase):
    """B2: pending buffer evicts oldest when over capacity."""

    def test_pending_buffer_evicts_oldest(self):
        b = SpanTreeBuilder(max_pending=3)
        # Feed 4 requests with no root (they stay pending).
        for i in range(4):
            b.feed(_evt("extraction", rid=f"req-{i}", sid=f"s-{i}", parent="s-parse", offset=i))
        # Only 3 should remain (oldest evicted).
        self.assertEqual(len(b.pending()), 3)
        self.assertNotIn("req-0", b.pending())  # oldest evicted

    def test_flushed_set_bounded(self):
        b = SpanTreeBuilder(max_flushed=2)
        for i in range(3):
            b.feed(_evt("parse", rid=f"req-{i}", sid=f"s-{i}", parent=None, offset=i))
        # Only 2 flushed records retained.
        self.assertEqual(b.flushed_count(), 2)


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

    def test_recursive_pii_redaction_in_nested_dict(self):
        # B4: a benign top-level key whose value is a nested dict carrying PII
        # must be scrubbed recursively, not just at the top level.
        out = redact_telemetry_attributes({
            "meta": {
                "cardholder_name": "Jane Doe",
                "nested": {"description": "ACME PAYMENT", "safe": "ok"},
                "card_number": "4111111111111111",
            },
            "items": [
                {"description": "secret", "amount": 100},
                {"cardholder": "Bob"},
            ],
        })
        self.assertEqual(out["meta"]["cardholder_name"], "[REDACTED]")
        self.assertEqual(out["meta"]["nested"]["description"], "[REDACTED]")
        self.assertEqual(out["meta"]["nested"]["safe"], "ok")
        self.assertEqual(out["meta"]["card_number"], "[REDACTED]")
        self.assertEqual(out["items"][0]["description"], "[REDACTED]")
        self.assertEqual(out["items"][0]["amount"], 100)
        self.assertEqual(out["items"][1]["cardholder"], "[REDACTED]")

    def test_card_number_in_string_value_is_scrubbed(self):
        out = redact_telemetry_attributes({"note": "card 4111 1111 1111 1111 here"})
        self.assertNotIn("4111", str(out["note"]))
        self.assertIn("[REDACTED_CARD]", str(out["note"]))

    def test_card_number_in_nested_string_is_scrubbed(self):
        out = redact_telemetry_attributes({"meta": {"text": "num 4111111111111111 end"}})
        self.assertNotIn("4111111111111111", str(out["meta"]["text"]))

    def test_long_strings_are_truncated(self):
        out = redact_telemetry_attributes({"x": "a" * 500})
        self.assertLess(len(out["x"]), 500)
        self.assertTrue(str(out["x"]).endswith("...[truncated]"))

    def test_prompt_key_uses_larger_truncation_cap(self):
        # The "prompt" key carries bank template text (not PII) that must be
        # VISIBLE in the trace. It gets a larger truncation cap than the default
        # 200 so the prompt's instructions are not clipped to just the title line.
        out = redact_telemetry_attributes({"prompt": "P" * 5000})
        # NOT clipped to the default 200; substantially more survives.
        self.assertGreater(len(out["prompt"]), 500)
        # Still bounded (the larger cap, not the full 5000).
        self.assertLess(len(out["prompt"]), 5000)
        self.assertTrue(str(out["prompt"]).endswith("...[truncated]"))

    def test_default_cap_still_applies_to_non_prompt_keys(self):
        # Regression guard: only the "prompt" key gets the larger cap; an
        # ordinary key with a long string is still truncated to the default.
        out = redact_telemetry_attributes({"note": "x" * 5000})
        self.assertLess(len(out["note"]), 500)
        self.assertTrue(str(out["note"]).endswith("...[truncated]"))

    def test_prompt_card_numbers_still_scrubbed(self):
        # The larger cap does not bypass card-number scrubbing: a prompt that
        # contains a card-number-shaped sequence still has it redacted.
        out = redact_telemetry_attributes({"prompt": "card 4111111111111111 end"})
        self.assertNotIn("4111111111111111", str(out["prompt"]))
        self.assertIn("[REDACTED_CARD]", str(out["prompt"]))


class ToNsTest(unittest.TestCase):
    def test_tz_aware_datetime_to_nanoseconds(self):
        dt = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        self.assertEqual(to_ns(dt), int(dt.timestamp() * 1_000_000_000))

    def test_tz_naive_assumed_utc(self):
        dt = datetime(2026, 1, 1, 0, 0, 0)
        self.assertEqual(to_ns(dt), int(dt.replace(tzinfo=UTC).timestamp() * 1_000_000_000))


if __name__ == "__main__":
    unittest.main()
