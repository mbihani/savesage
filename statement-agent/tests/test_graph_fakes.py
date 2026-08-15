"""Graph fakes + node unit tests (stdlib-only, non-skipped).

These exercise the fake ports and the node functions directly, WITHOUT
langgraph. The graph-level end-to-end test (which needs langgraph compiled) lives
in test_graph_e2e.py and is skipped when langgraph is absent.
"""

import unittest

from contracts.models import (
    ComparisonOutcome,
    FieldComparison,
    FieldScope,
    MatchMethod,
    ParseRequest,
)
from contracts.models import Bank
from graph.fakes import (
    FakeExtractionAdapter,
    FakeJudgeAdapter,
    FailingExtractionAdapter,
    InMemoryFeedbackStore,
    InMemoryResultStore,
    InMemoryTraceSink,
    _synthetic_valid_payload,
    make_all_fakes,
    make_synthetic_request,
)
from graph.nodes import NodeDeps
from graph.state import GraphState, Outcome, Stage
from graph.validation import validate_payload


def _state(bank: Bank = Bank.HDFC) -> GraphState:
    return GraphState(request=make_synthetic_request(bank))


class FakesTest(unittest.TestCase):
    def test_synthetic_payload_is_valid(self) -> None:
        report = validate_payload(_synthetic_valid_payload())
        self.assertTrue(report.ok, report.all_errors)

    def test_make_all_fakes_returns_five_distinct_fakes(self) -> None:
        store, feedback, trace, extraction, judge = make_all_fakes()
        self.assertIsInstance(store, InMemoryResultStore)
        self.assertIsInstance(feedback, InMemoryFeedbackStore)
        self.assertIsInstance(trace, InMemoryTraceSink)
        self.assertIsInstance(extraction, FakeExtractionAdapter)
        self.assertIsInstance(judge, FakeJudgeAdapter)

    def test_result_store_roundtrip(self) -> None:
        store = InMemoryResultStore()
        from contracts.models import ExtractionResult
        result = ExtractionResult(request_id="r1", payload={}, model_id="m", latency_ms=1.0)
        store.save_extraction(result)
        self.assertIs(store.get_extraction("r1"), result)
        self.assertIsNone(store.get_extraction("missing"))

    def test_feedback_store_filters_by_request(self) -> None:
        from datetime import UTC, datetime
        store = InMemoryFeedbackStore()
        fb1 = _make_feedback("r1")
        fb2 = _make_feedback("r2")
        store.append_feedback(fb1)
        store.append_feedback(fb2)
        self.assertEqual(list(store.list_feedback("r1")), [fb1])
        self.assertEqual(list(store.list_feedback("r2")), [fb2])

    def test_trace_sink_captures_events(self) -> None:
        from contracts.models import TraceEvent
        from datetime import UTC, datetime
        sink = InMemoryTraceSink()
        ev = TraceEvent(request_id="r1", name="n", started_at=datetime.now(UTC), ended_at=datetime.now(UTC))
        sink.record(ev)
        self.assertEqual(sink.events, [ev])


def _make_feedback(request_id: str):
    from datetime import UTC, datetime
    from contracts.models import FieldFeedback
    return FieldFeedback(request_id, "rewards.closingPoints", 1, 2, True, "actor", datetime.now(UTC))


class NodeUnitTest(unittest.TestCase):
    """Test nodes directly (no langgraph), to cover the short-circuit logic."""

    def _deps(self, *, extraction=None, judge=None, store=None, trace=None) -> NodeDeps:
        return NodeDeps(
            extraction=extraction or FakeExtractionAdapter(),
            result_store=store,
            trace_sink=trace,
            judge=judge,
        )

    def test_route_node_sets_prompt(self) -> None:
        from graph.nodes import route_node
        state = _state(Bank.HDFC)
        route_node(state, self._deps())
        self.assertEqual(state.stage, Stage.ROUTED)
        self.assertIsNotNone(state.prompt)
        self.assertGreater(len(state.prompt), 0)

    def test_extract_then_validate_then_persist_then_judge_clean_run(self) -> None:
        from graph.nodes import extract_node, finalize_node, judge_node, persist_node, route_node, validate_node
        store, _fb, trace, extraction, judge = make_all_fakes()
        deps = self._deps(extraction=extraction, judge=judge, store=store, trace=trace)
        state = _state(Bank.ICICI)
        route_node(state, deps)
        extract_node(state, deps)
        validate_node(state, deps)
        persist_node(state, deps)
        judge_node(state, deps)
        finalize_node(state, deps)
        self.assertEqual(state.stage, Stage.JUDGED)
        self.assertEqual(state.outcome, Outcome.SUCCESS)
        self.assertTrue(state.schema_valid)
        self.assertIsNone(state.errors or None or None)  # no errors
        self.assertEqual(state.errors, [])
        self.assertIsNotNone(state.verdict)
        self.assertIsNotNone(store.get_extraction("synthetic-req-001"))
        self.assertIsNotNone(store.get_verdict("synthetic-req-001"))
        self.assertGreater(len(trace.events), 0)

    def test_extraction_failure_short_circuits_judge(self) -> None:
        from graph.nodes import extract_node, judge_node, route_node
        judge = FakeJudgeAdapter()
        deps = self._deps(extraction=FailingExtractionAdapter(), judge=judge)
        state = _state()
        route_node(state, deps)
        extract_node(state, deps)
        # downstream nodes must no-op on a terminal EXTRACTION_FAILED
        judge_node(state, deps)
        self.assertEqual(state.outcome, Outcome.EXTRACTION_FAILED)
        self.assertIsNone(state.verdict)
        self.assertEqual(judge.calls, [])  # judge never called

    def test_validation_failure_does_not_short_circuit_judge(self) -> None:
        # Documented decision: validation failure -> PARTIAL, but judge still runs.
        from graph.nodes import extract_node, finalize_node, judge_node, persist_node, route_node, validate_node
        store, _fb, _trace, _ext, judge = make_all_fakes()

        def corrupt_amount(payload):
            payload["transactions"][0]["amount"] = -99.0  # violates amount_direction

        extraction = FakeExtractionAdapter(mutator=corrupt_amount)
        deps = NodeDeps(extraction=extraction, result_store=store, judge=judge)
        state = _state()
        route_node(state, deps)
        extract_node(state, deps)
        validate_node(state, deps)
        persist_node(state, deps)
        judge_node(state, deps)
        finalize_node(state, deps)
        self.assertEqual(state.outcome, Outcome.PARTIAL)
        self.assertFalse(state.schema_valid is False and not state.validation_errors)
        self.assertGreater(len(state.validation_errors), 0)
        self.assertEqual(len(judge.calls), 1)  # judge DID run despite validation errors

    def test_no_judge_wired_skips_judge_stage(self) -> None:
        from graph.nodes import finalize_node, judge_node, route_node
        deps = self._deps()  # judge=None
        state = _state()
        route_node(state, deps)
        judge_node(state, deps)
        finalize_node(state, deps)
        self.assertIsNone(state.verdict)
        # no terminal failure; outcome set by finalize (SUCCESS since no validation ran yet)
        self.assertEqual(state.outcome, Outcome.SUCCESS)

    def test_no_store_wired_skips_persist(self) -> None:
        from graph.nodes import persist_node
        deps = self._deps(store=None)
        state = _state()
        state.extraction = FakeExtractionAdapter().extract(make_synthetic_request())
        persist_node(state, deps)
        # no exception, stage unchanged (still INIT since route wasn't called)
        self.assertEqual(state.stage, Stage.INIT)

    def test_trace_failure_does_not_kill_graph(self) -> None:
        from graph.nodes import route_node

        class BrokenTrace:
            def record(self, event):
                raise RuntimeError("trace broken")
        deps = self._deps(trace=BrokenTrace())  # type: ignore[arg-type]
        state = _state()
        # must not raise
        route_node(state, deps)
        self.assertEqual(state.stage, Stage.ROUTED)


if __name__ == "__main__":
    unittest.main()
