"""Graph end-to-end test through the compiled LangGraph (skip-if-langgraph-absent).

LangGraph cannot be installed on this machine (pypi is blackholed), so this test
is skipped locally. It runs at deploy/on a machine with langgraph installed and
proves the full route->extract->validate->persist->judge->finalize path against
the in-memory fake ports. The non-skipped tests in test_graph_fakes.py cover the
node logic without langgraph.
"""

import unittest

from contracts.models import Bank
from graph.fakes import (
    FakeExtractionAdapter,
    FailingExtractionAdapter,
    InMemoryResultStore,
    InMemoryTraceSink,
    FakeJudgeAdapter,
    make_synthetic_request,
)
from graph.nodes import NodeDeps
from graph.state import GraphState, Outcome

try:
    import langgraph  # noqa: F401
    _HAS_LANGGRAPH = True
except ImportError:
    _HAS_LANGGRAPH = False


@unittest.skipUnless(_HAS_LANGGRAPH, "langgraph not installed (pypi blackholed locally)")
class GraphE2ETest(unittest.TestCase):
    def _run(self, deps: NodeDeps, bank: Bank = Bank.HDFC) -> GraphState:
        from graph.graph import run_graph
        state = GraphState(request=make_synthetic_request(bank))
        return run_graph(deps, state)

    def test_clean_run_success(self) -> None:
        store = InMemoryResultStore()
        trace = InMemoryTraceSink()
        deps = NodeDeps(
            extraction=FakeExtractionAdapter(),
            result_store=store,
            trace_sink=trace,
            judge=FakeJudgeAdapter(),
        )
        state = self._run(deps, Bank.ICICI)
        self.assertEqual(state.outcome, Outcome.SUCCESS)
        self.assertTrue(state.schema_valid)
        self.assertEqual(state.errors, [])
        self.assertIsNotNone(state.verdict)
        self.assertIsNotNone(store.get_extraction("synthetic-req-001"))
        self.assertIsNotNone(store.get_verdict("synthetic-req-001"))
        self.assertGreater(len(trace.events), 0)

    def test_extraction_failure_terminal(self) -> None:
        deps = NodeDeps(
            extraction=FailingExtractionAdapter(),
            result_store=InMemoryResultStore(),
            judge=FakeJudgeAdapter(),
        )
        state = self._run(deps)
        self.assertEqual(state.outcome, Outcome.EXTRACTION_FAILED)
        self.assertIsNone(state.verdict)

    def test_all_four_banks_route(self) -> None:
        for bank in Bank:
            deps = NodeDeps(extraction=FakeExtractionAdapter(), judge=FakeJudgeAdapter())
            state = self._run(deps, bank)
            self.assertIsNotNone(state.prompt, bank.value)
            self.assertGreater(len(state.prompt), 0, bank.value)
            self.assertIn(state.outcome, (Outcome.SUCCESS, Outcome.PARTIAL), bank.value)

    def test_validation_failure_judge_still_runs(self) -> None:
        def corrupt(payload):
            payload["transactions"][0]["amount"] = -1.0
        judge = FakeJudgeAdapter()
        deps = NodeDeps(
            extraction=FakeExtractionAdapter(mutator=corrupt),
            result_store=InMemoryResultStore(),
            judge=judge,
        )
        state = self._run(deps)
        self.assertEqual(state.outcome, Outcome.PARTIAL)
        self.assertGreater(len(state.validation_errors), 0)
        self.assertEqual(len(judge.calls), 1)


if __name__ == "__main__":
    unittest.main()
