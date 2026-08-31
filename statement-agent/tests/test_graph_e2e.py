"""Graph end-to-end test through the parse pipeline (stdlib-only, no langgraph).

``run_graph`` executes nodes directly (see ``graph/graph.py``) so these tests
run without langgraph installed — they prove the full
route->extract->validate->finalize path against the in-memory fake
ports. The judge no longer runs inline — it is a post-hoc evaluation over
MLflow traces (see ``judge/scorer.py``). The node-logic tests in
``test_graph_fakes.py`` cover the individual nodes without the pipeline.
"""

import unittest

from contracts.models import Bank
from graph.fakes import (
    FakeExtractionAdapter,
    FailingExtractionAdapter,
    InMemoryTraceSink,
    make_synthetic_request,
)
from graph.nodes import NodeDeps
from graph.state import GraphState, Outcome


class GraphE2ETest(unittest.TestCase):
    def _run(self, deps: NodeDeps, bank: Bank = Bank.HDFC) -> GraphState:
        from graph.graph import run_graph
        state = GraphState(request=make_synthetic_request(bank))
        return run_graph(deps, state)

    def test_clean_run_success(self) -> None:
        trace = InMemoryTraceSink()
        deps = NodeDeps(
            extraction=FakeExtractionAdapter(),
            trace_sink=trace,
        )
        state = self._run(deps, Bank.ICICI)
        self.assertEqual(state.outcome, Outcome.SUCCESS)
        self.assertTrue(state.schema_valid)
        # BLOCKING 2: the extraction carries the validated schema_valid.
        self.assertTrue(state.extraction.schema_valid)
        self.assertEqual(state.errors, [])
        self.assertGreater(len(trace.events), 0)

    def test_extraction_failure_terminal(self) -> None:
        deps = NodeDeps(
            extraction=FailingExtractionAdapter(),
        )
        state = self._run(deps)
        self.assertEqual(state.outcome, Outcome.EXTRACTION_FAILED)

    def test_all_four_banks_route(self) -> None:
        for bank in Bank:
            deps = NodeDeps(extraction=FakeExtractionAdapter())
            state = self._run(deps, bank)
            self.assertIsNotNone(state.prompt, bank.value)
            self.assertGreater(len(state.prompt), 0, bank.value)
            self.assertIn(state.outcome, (Outcome.SUCCESS, Outcome.PARTIAL), bank.value)

    def test_validation_failure_yields_partial(self) -> None:
        # A validation failure (schema-invalid payload) yields PARTIAL.
        def corrupt(payload):
            payload["transactions"][0]["amount"] = -1.0
        deps = NodeDeps(
            extraction=FakeExtractionAdapter(mutator=corrupt),
        )
        state = self._run(deps)
        self.assertEqual(state.outcome, Outcome.PARTIAL)
        self.assertGreater(len(state.validation_errors), 0)

    def test_parse_root_event_emitted(self) -> None:
        """run_graph emits a root 'parse' TraceEvent (parent_span_id=None) in a
        finally block after the pipeline completes — this is the root event the
        SpanTreeBuilder flushes on to finalize the MLflow run (end_run).
        """
        trace = InMemoryTraceSink()
        deps = NodeDeps(
            extraction=FakeExtractionAdapter(),
            trace_sink=trace,
        )
        state = self._run(deps, Bank.HDFC)

        # The "parse" root event must be the LAST event (emitted by run_graph
        # after the graph completes).
        names = [e.name for e in trace.events]
        self.assertIn("parse", names)
        self.assertEqual(names[-1], "parse")
        parse_evt = [e for e in trace.events if e.name == "parse"][0]
        self.assertIsNone(parse_evt.parent_span_id)
        self.assertEqual(parse_evt.span_id, f"{state.request_id}:parse")

        # Child events must reference the parse root as parent.
        child_events = [e for e in trace.events if e.name != "parse"]
        for ce in child_events:
            self.assertEqual(ce.parent_span_id, f"{state.request_id}:parse")
            self.assertIsNotNone(ce.span_id)

    def test_parse_root_event_emitted_on_node_failure(self) -> None:
        """The root 'parse' event fires even when a node fails internally — the
        node catches the exception and sets a failure outcome, the graph
        completes normally, and the finally block still emits the root event
        to finalize the MLflow run.
        """
        class FailingExtraction(FakeExtractionAdapter):
            def extract(self, request):
                raise RuntimeError("boom")

        trace = InMemoryTraceSink()
        deps = NodeDeps(
            extraction=FailingExtraction(),
            trace_sink=trace,
        )
        state = self._run(deps, Bank.HDFC)
        # The graph handled the failure internally — outcome is EXTRACTION_FAILED.
        self.assertEqual(state.outcome, Outcome.EXTRACTION_FAILED)
        # But the root parse event was STILL emitted by the finally block.
        names = [e.name for e in trace.events]
        self.assertIn("parse", names)
        self.assertEqual(names[-1], "parse")
        parse_evt = [e for e in trace.events if e.name == "parse"][0]
        self.assertIsNone(parse_evt.parent_span_id)
        self.assertEqual(parse_evt.span_id, f"{state.request_id}:parse")


if __name__ == "__main__":
    unittest.main()
