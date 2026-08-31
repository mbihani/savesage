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

    def test_make_all_fakes_returns_three_distinct_fakes(self) -> None:
        trace, extraction, judge = make_all_fakes()
        self.assertIsInstance(trace, InMemoryTraceSink)
        self.assertIsInstance(extraction, FakeExtractionAdapter)
        self.assertIsInstance(judge, FakeJudgeAdapter)

    def test_trace_sink_captures_events(self) -> None:
        from contracts.models import TraceEvent
        from datetime import UTC, datetime
        sink = InMemoryTraceSink()
        ev = TraceEvent(request_id="r1", name="n", started_at=datetime.now(UTC), ended_at=datetime.now(UTC))
        sink.record(ev)
        self.assertEqual(sink.events, [ev])


class NodeUnitTest(unittest.TestCase):
    """Test nodes directly (no langgraph), to cover the short-circuit logic."""

    def _deps(self, *, extraction=None, judge=None, trace=None) -> NodeDeps:
        return NodeDeps(
            extraction=extraction or FakeExtractionAdapter(),
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

    def test_route_node_normalises_unknown_bank_to_generic(self) -> None:
        """route_node reverts a completely unknown bank to GENERIC on the state
        so downstream nodes, traces, and the prompt version all report the
        effective bank — not the unknown name the caller passed.
        """
        from contracts.models import Bank
        from graph.nodes import route_node
        state = _state("KOTAK")  # not built-in, not in DBFS registry locally
        route_node(state, self._deps())
        self.assertEqual(state.stage, Stage.ROUTED)
        self.assertIs(state.request.bank, Bank.GENERIC)
        # The prompt version is tagged with the effective bank (GENERIC).
        self.assertTrue(state.prompt_version.startswith("GENERIC:"))

    def test_extract_then_validate_then_judge_clean_run(self) -> None:
        from graph.nodes import extract_node, finalize_node, judge_node, route_node, validate_node
        _trace, extraction, judge = make_all_fakes()
        deps = self._deps(extraction=extraction, judge=judge, trace=_trace)
        state = _state(Bank.ICICI)
        route_node(state, deps)
        extract_node(state, deps)
        validate_node(state, deps)
        judge_node(state, deps)
        finalize_node(state, deps)
        self.assertEqual(state.stage, Stage.JUDGED)
        self.assertEqual(state.outcome, Outcome.SUCCESS)
        self.assertTrue(state.schema_valid)
        # BLOCKING 2: the validated schema_valid must be propagated into the
        # extraction object, not just the state field.
        self.assertTrue(state.extraction.schema_valid)
        self.assertEqual(state.errors, [])
        self.assertIsNone(state.judge_skipped_reason)
        self.assertIsNotNone(state.verdict)
        self.assertGreater(len(_trace.events), 0)

    def test_validated_object_carries_validated_schema_valid(self) -> None:
        # BLOCKING 2 regression: the fake adapter leaves schema_valid=False (as
        # the real adapter does); validate_node must propagate the validated
        # value into the extraction object.
        from graph.nodes import extract_node, route_node, validate_node
        state = _state(Bank.HDFC)
        deps = self._deps(extraction=FakeExtractionAdapter())
        route_node(state, deps)
        extract_node(state, deps)
        # After extract, the adapter left schema_valid=False (mirrors real Luna).
        self.assertFalse(state.extraction.schema_valid)
        validate_node(state, deps)
        self.assertTrue(state.extraction.schema_valid)

    def test_validated_object_carries_false_on_schema_invalid(self) -> None:
        # The propagated value must be False when validation genuinely fails.
        from graph.nodes import extract_node, route_node, validate_node

        def add_unknown_key(payload):
            payload["statementMeta"]["unexpectedExtra"] = "x"  # additionalProperties

        deps = self._deps(
            extraction=FakeExtractionAdapter(mutator=add_unknown_key))
        state = _state()
        route_node(state, deps)
        extract_node(state, deps)
        validate_node(state, deps)
        self.assertFalse(state.extraction.schema_valid)

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
        from graph.nodes import extract_node, finalize_node, judge_node, route_node, validate_node
        _trace, _ext, judge = make_all_fakes()

        def corrupt_amount(payload):
            payload["transactions"][0]["amount"] = -99.0  # violates amount_direction

        extraction = FakeExtractionAdapter(mutator=corrupt_amount)
        deps = NodeDeps(extraction=extraction, judge=judge)
        state = _state()
        route_node(state, deps)
        extract_node(state, deps)
        validate_node(state, deps)
        judge_node(state, deps)
        finalize_node(state, deps)
        self.assertEqual(state.outcome, Outcome.PARTIAL)
        # Negative amount passes schema (it's a finite number) but fails the rule.
        self.assertTrue(state.schema_valid)
        self.assertTrue(state.extraction.schema_valid)
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
        # Trace failures go to trace_errors, NOT errors, so they don't affect outcome.
        self.assertEqual(state.errors, [])
        self.assertGreater(len(state.trace_errors), 0)

    def test_trace_generates_span_ids(self) -> None:
        """_trace() generates span_id={request_id}:{name} and
        parent_span_id={request_id}:parse on every child event so the
        SpanTreeBuilder can link children to the parse root.
        """
        from graph.nodes import route_node
        trace = InMemoryTraceSink()
        deps = self._deps(trace=trace)
        state = _state()
        route_node(state, deps)
        self.assertEqual(len(trace.events), 1)
        evt = trace.events[0]
        self.assertEqual(evt.span_id, f"{state.request_id}:route")
        self.assertEqual(evt.parent_span_id, f"{state.request_id}:parse")
        self.assertEqual(evt.name, "route")
        self.assertIsNotNone(evt.span_id)

    def test_judge_skipped_when_no_section_judgeable(self) -> None:
        # NB: a payload with NO structurally judgeable section is not judged.
        # cards is a string, transactions is a string, rewards is a string.
        from graph.nodes import judge_node

        judge = FakeJudgeAdapter()
        deps = self._deps(extraction=FakeExtractionAdapter(), judge=judge)
        state = _state()
        from contracts.models import ExtractionResult
        state.extraction = ExtractionResult(
            request_id=state.request_id,
            payload={"cards": "not a list", "transactions": "also not", "rewards": "not a dict"},
            model_id="fake", latency_ms=0.0, schema_valid=False,
        )
        judge_node(state, deps)
        self.assertIsNone(state.verdict)
        self.assertEqual(judge.calls, [])  # judge never called
        self.assertIsNotNone(state.judge_skipped_reason)
        self.assertIn("no structurally judgeable sections", state.judge_skipped_reason)

    def test_judge_runs_on_schema_invalid_but_structurally_usable(self) -> None:
        # NB: schema-invalid but structurally usable -> judge STILL runs.
        from graph.nodes import judge_node

        judge = FakeJudgeAdapter()
        deps = self._deps(extraction=FakeExtractionAdapter(), judge=judge)
        state = _state()
        from contracts.models import ExtractionResult
        # Schema-invalid (extra key) but cards/transactions ARE lists.
        state.extraction = ExtractionResult(
            request_id=state.request_id,
            payload={"cards": [], "transactions": [], "unexpected": "x"},
            model_id="fake", latency_ms=0.0, schema_valid=False,
        )
        judge_node(state, deps)
        self.assertEqual(len(judge.calls), 1)
        self.assertIsNone(state.judge_skipped_reason)

    def test_judge_runs_with_only_transactions_present(self) -> None:
        # NB per-section gating: cards missing, but transactions present -> judge
        # still runs and grades the surviving sections.
        from graph.nodes import judge_node

        judge = FakeJudgeAdapter()
        deps = self._deps(judge=judge)
        state = _state()
        from contracts.models import ExtractionResult
        state.extraction = ExtractionResult(
            request_id=state.request_id,
            payload={"cards": None, "transactions": [{"date": "01/01/2026",
                "description": "x", "amount": 1.0, "direction": "DEBIT",
                "txnType": "PURCHASE", "rewardPointsOnThisTransaction": 0, "currency": "INR"}]},
            model_id="fake", latency_ms=0.0, schema_valid=False,
        )
        judge_node(state, deps)
        self.assertEqual(len(judge.calls), 1)
        self.assertIsNotNone(state.verdict)
        self.assertIsNone(state.judge_skipped_reason)

    def test_judge_runs_with_only_rewards_present(self) -> None:
        # NB per-section gating: cards/transactions missing, rewards present.
        from graph.nodes import judge_node

        judge = FakeJudgeAdapter()
        deps = self._deps(judge=judge)
        state = _state()
        from contracts.models import ExtractionResult
        state.extraction = ExtractionResult(
            request_id=state.request_id,
            payload={"cards": "broken", "transactions": "broken",
                      "rewards": {"closingPoints": 5}},
            model_id="fake", latency_ms=0.0, schema_valid=False,
        )
        judge_node(state, deps)
        self.assertEqual(len(judge.calls), 1)
        self.assertIsNotNone(state.verdict)

    def test_judge_runs_with_only_cards_present(self) -> None:
        # NB per-section gating: only cards present as a list.
        from graph.nodes import judge_node

        judge = FakeJudgeAdapter()
        deps = self._deps(judge=judge)
        state = _state()
        from contracts.models import ExtractionResult
        state.extraction = ExtractionResult(
            request_id=state.request_id,
            payload={"cards": [{"cardMeta": {"cardDisplayName": "x", "productFamily": "y",
                "lastFourDigit": "0000", "network": "VISA", "isPrimaryCard": True},
                "bigPicture": {"cardCreditLimit": 100.0, "cardAvailableCreditLimit": 99.0}}],
                "transactions": "broken", "rewards": "broken"},
            model_id="fake", latency_ms=0.0, schema_valid=False,
        )
        judge_node(state, deps)
        self.assertEqual(len(judge.calls), 1)

    def test_judge_skipped_on_non_dict_payload(self) -> None:
        from graph.nodes import judge_node

        judge = FakeJudgeAdapter()
        deps = self._deps(judge=judge)
        state = _state()
        from contracts.models import ExtractionResult
        state.extraction = ExtractionResult(
            request_id=state.request_id, payload=[1, 2, 3],
            model_id="fake", latency_ms=0.0,
        )
        judge_node(state, deps)
        self.assertIsNone(state.verdict)
        self.assertEqual(judge.calls, [])
        self.assertIsNotNone(state.judge_skipped_reason)

    def test_internal_validation_error_yields_partial_not_success(self) -> None:
        # Round 4 follow-up: the structural safety net in validate_payload
        # catches an internal error, but the graph must know about it.
        # internal_error flows into validation_errors via all_errors, and
        # validate_node also records it as a stage error, so finalize_node
        # produces PARTIAL -- never SUCCESS.
        from graph.nodes import extract_node, finalize_node, judge_node, route_node, validate_node
        _trace, _ext, judge = make_all_fakes()
        deps = NodeDeps(extraction=FakeExtractionAdapter(), judge=judge)
        state = _state()
        route_node(state, deps)
        extract_node(state, deps)
        # Inject a validator that raises: the structural catch in
        # validate_payload converts it to a report with internal_error set.
        import graph.validation as mod
        original = mod.validate_schema_conformance
        mod.validate_schema_conformance = lambda payload, schema=None: (_ for _ in ()).throw(
            RuntimeError("synthetic internal failure")
        )
        try:
            validate_node(state, deps)
        finally:
            mod.validate_schema_conformance = original
        judge_node(state, deps)
        finalize_node(state, deps)
        self.assertEqual(state.outcome, Outcome.PARTIAL)
        self.assertNotEqual(state.outcome, Outcome.SUCCESS)
        # The internal error must be visible in validation_errors (via all_errors).
        self.assertTrue(
            any("internal_error" in e for e in state.validation_errors),
            state.validation_errors,
        )
        # And as a stage error (belt-and-suspenders backstop).
        self.assertTrue(state.has_stage_errors)


if __name__ == "__main__":
    unittest.main()
