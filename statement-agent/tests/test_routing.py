"""Routing + graph-state transition tests (stdlib-only, no langgraph)."""

import unittest

from contracts.models import Bank, ParseRequest
from graph.routing import resolve_prompt, resolve_prompt_for_all_banks, RoutingError
from graph.state import GraphState, Outcome, Stage


class RoutingTest(unittest.TestCase):
    def test_all_four_banks_resolve_to_nonempty_prompt(self) -> None:
        for bank in Bank:
            prompt = resolve_prompt(bank)
            self.assertGreater(len(prompt.strip()), 0, bank.value)

    def test_axis_uses_generic_luna_prompt(self) -> None:
        # AXIS intentionally uses the generic Luna prompt (prompts/axis.txt).
        axis = resolve_prompt(Bank.AXIS)
        hdfc = resolve_prompt(Bank.HDFC)
        # AXIS must have a real prompt, and it must differ from the HDFC-specific one.
        self.assertGreater(len(axis.strip()), 0)
        self.assertNotEqual(axis, hdfc)

    def test_resolve_all_banks_covers_every_bank(self) -> None:
        all_prompts = resolve_prompt_for_all_banks()
        self.assertEqual(set(all_prompts), set(Bank))
        for bank, prompt in all_prompts.items():
            self.assertGreater(len(prompt.strip()), 0, bank.value)

    def test_unknown_bank_raises(self) -> None:
        # Bank is a closed enum; a value outside it cannot be constructed via
        # Bank(...), so simulate a broken routing table by passing a non-Bank.
        with self.assertRaises((KeyError, RoutingError, TypeError)):
            resolve_prompt("NOT_A_BANK")  # type: ignore[arg-type]


class GraphStateTest(unittest.TestCase):
    def _request(self, bank: Bank = Bank.HDFC) -> ParseRequest:
        return ParseRequest(
            pdf=b"%PDF-1.4 synthetic", filename="synthetic.pdf", bank=bank, request_id="r1"
        )

    def test_initial_state(self) -> None:
        state = GraphState(request=self._request())
        self.assertIsNone(state.prompt)
        self.assertIsNone(state.extraction)
        self.assertFalse(state.schema_valid)
        self.assertEqual(state.validation_errors, [])
        self.assertEqual(state.stage, Stage.INIT)
        self.assertIsNone(state.outcome)
        self.assertEqual(state.errors, [])
        self.assertEqual(state.request_id, "r1")

    def test_mark_failure_records_stage_and_message(self) -> None:
        state = GraphState(request=self._request())
        state.mark_failure(Stage.EXTRACTED, "boom")
        self.assertEqual(state.stage, Stage.EXTRACTED)
        self.assertEqual(state.errors, ["boom"])

    def test_as_summary_shape(self) -> None:
        state = GraphState(request=self._request(Bank.ICICI))
        summary = state.as_summary()
        self.assertEqual(summary["request_id"], "r1")
        self.assertEqual(summary["bank"], "ICICI")
        self.assertEqual(summary["stage"], "INIT")
        self.assertIsNone(summary["outcome"])
        self.assertFalse(summary["schema_valid"])
        self.assertIsNone(summary["n_transactions"])

    def test_outcome_enum_has_four_dispositions(self) -> None:
        self.assertEqual(
            {Outcome.SUCCESS, Outcome.PARTIAL, Outcome.EXTRACTION_FAILED, Outcome.JUDGE_FAILED},
            set(Outcome),
        )

    def test_as_summary_never_crashes_on_non_dict_payload(self) -> None:
        # NEW-B3 (defence in depth): as_summary is on the user-facing path and
        # must not crash even if a non-dict payload slips past map_response.
        from contracts.models import ExtractionResult
        for bad_payload in ([1, 2, 3], "a string", 42, True, None):
            state = GraphState(request=self._request())
            state.extraction = ExtractionResult(
                request_id="r1", payload=bad_payload,  # type: ignore[arg-type]
                model_id="m", latency_ms=0.0,
            )
            summary = state.as_summary()  # must not raise
            self.assertIsNone(summary["n_transactions"])

    def test_as_summary_reports_txn_count_for_valid_payload(self) -> None:
        from contracts.models import ExtractionResult
        state = GraphState(request=self._request())
        state.extraction = ExtractionResult(
            request_id="r1",
            payload={"transactions": [{}, {}, {}]},
            model_id="m", latency_ms=0.0,
        )
        self.assertEqual(state.as_summary()["n_transactions"], 3)


if __name__ == "__main__":
    unittest.main()
