"""B2: bounded memory — all request-scoped collections stay bounded under load.

Databricks Apps are LONG-LIVED processes. The trace-id map, pending buffer, and
flushed set must not grow unbounded under sustained traffic, including requests
whose root never arrives (a parse that crashes before recording the root).
"""

from datetime import UTC, datetime
import unittest

from contracts.models import TraceEvent
from harness.config_ws4 import TracingConfig
from harness.tracing import MLflowTraceSink
from harness.tracing_spans import SpanTreeBuilder


def _evt(name, rid, sid, parent=None, offset=0):
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    started = datetime.fromtimestamp(base.timestamp() + offset, tz=UTC)
    ended = datetime.fromtimestamp(base.timestamp() + offset + 1, tz=UTC)
    return TraceEvent(
        request_id=rid, name=name, started_at=started, ended_at=ended,
        attributes={}, span_id=sid, parent_span_id=parent,
    )


class _FakeLiveSpan:
    def __init__(self):
        self.trace_id = "tr-fake"
        self.span_id = "fake"

    def set_attributes(self, a): pass
    def set_attribute(self, k, v): pass
    def record_exception(self, e): pass
    def end(self, **kw): pass


class _FakeMLflow:
    def set_tracking_uri(self, u): pass
    def set_experiment(self, p): pass
    class tracing:
        enable = staticmethod(lambda: None)
    class langchain:
        autolog = staticmethod(lambda **kw: None)
    def start_span_no_context(self, **kw): return _FakeLiveSpan()


class BoundedMemoryTest(unittest.TestCase):
    def _sink(self, max_trace_ids=5, max_pending=5, max_flushed=5):
        cfg = TracingConfig(
            enabled=True, tracking_uri="databricks", databricks_profile="fevm-stable",
            experiment_path="/x", autolog_langchain=False,
            max_trace_ids=max_trace_ids, max_pending_requests=max_pending,
            max_flushed=max_flushed,
        )
        return MLflowTraceSink(cfg, mlflow_factory=lambda: _FakeMLflow())

    def test_trace_ids_bounded_under_many_requests(self):
        sink = self._sink(max_trace_ids=5)
        for i in range(20):
            sink.record(_evt("parse", rid=f"req-{i}", sid=f"s-{i}", parent=None))
        # LRU must evict; never exceed the bound.
        self.assertLessEqual(len(sink._trace_ids), 5)

    def test_pop_trace_id_removes_entry(self):
        sink = self._sink(max_trace_ids=10)
        sink.record(_evt("parse", rid="req-0", sid="s-0", parent=None))
        self.assertEqual(sink.get_trace_id("req-0"), "tr-fake")
        popped = sink.pop_trace_id("req-0")
        self.assertEqual(popped, "tr-fake")
        self.assertIsNone(sink.get_trace_id("req-0"))

    def test_pending_buffer_bounded_when_roots_never_arrive(self):
        # Requests whose root never arrives (crashed parse) must not accumulate.
        sink = self._sink(max_pending=5)
        for i in range(20):
            # Children only — no root ever arrives for these.
            sink.record(_evt("extraction", rid=f"req-{i}", sid=f"s-{i}", parent="s-parse"))
        self.assertLessEqual(len(sink._builder.pending()), 5)

    def test_abandon_drops_request_state(self):
        sink = self._sink()
        sink.record(_evt("extraction", rid="req-0", sid="s-0", parent="s-parse"))
        sink.record(_evt("parse", rid="req-1", sid="s-1", parent=None))
        sink.abandon("req-0")
        sink.abandon("req-1")
        self.assertNotIn("req-0", sink._builder.pending())
        self.assertIsNone(sink.get_trace_id("req-1"))

    def test_lru_evicts_oldest_trace_id(self):
        sink = self._sink(max_trace_ids=3)
        for i in range(3):
            sink.record(_evt("parse", rid=f"req-{i}", sid=f"s-{i}", parent=None))
        # Access req-0 (makes it most-recently-used), then add 1 more.
        sink.get_trace_id("req-0")
        sink.record(_evt("parse", rid="req-3", sid="s-3", parent=None))
        # req-1 should be evicted (least recently used), req-0 retained.
        self.assertIsNotNone(sink.get_trace_id("req-0"))
        self.assertIsNone(sink.get_trace_id("req-1"))

    def test_span_tree_builder_pending_bounded_directly(self):
        b = SpanTreeBuilder(max_pending=3)
        for i in range(10):
            b.feed(_evt("extraction", rid=f"r-{i}", sid=f"s-{i}", parent="s-p"))
        self.assertLessEqual(len(b.pending()), 3)


if __name__ == "__main__":
    unittest.main()
