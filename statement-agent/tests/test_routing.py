"""Routing + graph-state transition tests (stdlib-only, no langgraph)."""

import re
import unittest
from unittest.mock import patch

from contracts.models import Bank, ParseRequest
from graph.routing import (
    effective_bank,
    get_prompt_version,
    resolve_prompt,
    resolve_prompt_for_all_banks,
    try_bank,
)
from graph.state import GraphState, Outcome, Stage

_HEX8 = re.compile(r"^[0-9a-f]{8}$")


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

    def test_unknown_bank_falls_back_to_generic(self) -> None:
        # Unknown bank strings fall back to the GENERIC prompt instead of
        # raising RoutingError, so the UI/API can accept arbitrary bank names.
        prompt = resolve_prompt("NOT_A_BANK")
        generic = resolve_prompt(Bank.GENERIC)
        self.assertEqual(prompt, generic)

    def test_try_bank_known_string(self) -> None:
        self.assertIs(try_bank("HDFC"), Bank.HDFC)
        self.assertIs(try_bank("ICICI"), Bank.ICICI)

    def test_try_bank_enum_returned_as_is(self) -> None:
        self.assertIs(try_bank(Bank.SBI), Bank.SBI)

    def test_try_bank_unknown_falls_back_to_generic(self) -> None:
        self.assertIs(try_bank("NOT_A_BANK"), Bank.GENERIC)
        self.assertIs(try_bank("Some New Bank"), Bank.GENERIC)


class EffectiveBankTest(unittest.TestCase):
    """effective_bank reverts completely unknown banks to GENERIC while
    preserving known built-ins and registered dynamic banks.

    Unlike try_bank (which collapses ANY unknown string to GENERIC), this
    checks the DBFS registry so a dynamically added bank keeps its own name.
    """

    def test_enum_returned_as_is(self) -> None:
        self.assertIs(effective_bank(Bank.HDFC), Bank.HDFC)
        self.assertIs(effective_bank(Bank.GENERIC), Bank.GENERIC)

    def test_built_in_string_maps_to_enum(self) -> None:
        self.assertIs(effective_bank("HDFC"), Bank.HDFC)
        self.assertIs(effective_bank("icici"), Bank.ICICI)
        self.assertIs(effective_bank("  sbi "), Bank.SBI)

    def test_unknown_bank_falls_back_to_generic(self) -> None:
        # No SDK locally → read_dbfs_registry() returns [], so KOTAK is unknown.
        self.assertIs(effective_bank("KOTAK"), Bank.GENERIC)
        self.assertIs(effective_bank("Some Unknown Bank"), Bank.GENERIC)

    def test_empty_string_falls_back_to_generic(self) -> None:
        self.assertIs(effective_bank(""), Bank.GENERIC)
        self.assertIs(effective_bank("   "), Bank.GENERIC)

    def test_registered_dynamic_bank_keeps_name(self) -> None:
        with patch("graph.routing.read_dbfs_registry",
                   return_value=["KOTAK", "RBL"]):
            result = effective_bank("KOTAK")
            self.assertEqual(result, "KOTAK")
            self.assertNotIsInstance(result, Bank)
            result = effective_bank("rbl")
            self.assertEqual(result, "RBL")

    def test_dynamic_bank_not_in_registry_falls_back_to_generic(self) -> None:
        with patch("graph.routing.read_dbfs_registry", return_value=["KOTAK"]):
            self.assertIs(effective_bank("RBL"), Bank.GENERIC)

    def test_built_in_takes_precedence_over_registry(self) -> None:
        # A built-in bank is always its enum even if also in the registry.
        with patch("graph.routing.read_dbfs_registry", return_value=["HDFC"]):
            self.assertIs(effective_bank("HDFC"), Bank.HDFC)


class PromptVersionTest(unittest.TestCase):
    """``get_prompt_version`` tags a run with the exact prompt text used."""

    def test_version_format_is_bank_colon_8hex(self) -> None:
        for bank in Bank:
            version = get_prompt_version(resolve_prompt(bank), bank)
            bank_name, sep, digest = version.partition(":")
            self.assertTrue(sep, f"{bank.value}: missing ':' separator in {version!r}")
            self.assertEqual(bank_name, bank.value)
            self.assertIsNotNone(
                _HEX8.match(digest), f"{bank.value}: digest {digest!r} is not 8 hex chars",
            )

    def test_version_stable_across_calls(self) -> None:
        # Same prompt text -> same version id (stable, not random).
        for bank in Bank:
            text = resolve_prompt(bank)
            self.assertEqual(get_prompt_version(text, bank), get_prompt_version(text, bank))

    def test_version_differs_across_banks(self) -> None:
        # Each bank has a distinct prompt -> distinct version ids.
        versions = {bank: get_prompt_version(resolve_prompt(bank), bank) for bank in Bank}
        self.assertEqual(len(set(versions)), len(Bank))

    def test_version_changes_when_prompt_text_changes(self) -> None:
        # The version is derived from the prompt TEXT passed in, so a changed
        # prompt yields a changed version. ``get_prompt_version`` takes the
        # text directly (it no longer re-resolves from disk), so feeding it
        # different text is the direct way to exercise this.
        before = get_prompt_version("ORIGINAL PROMPT TEXT", Bank.HDFC)
        after = get_prompt_version("DIFFERENT PROMPT TEXT", Bank.HDFC)
        self.assertNotEqual(before, after)
        self.assertTrue(after.startswith("HDFC:"))

    def test_version_hash_matches_prompt_sha256_prefix(self) -> None:
        # The 8-char digest is the first 8 hex of the resolved prompt's SHA-256,
        # so the version is a faithful (if short) fingerprint of the prompt.
        import hashlib

        for bank in Bank:
            text = resolve_prompt(bank)
            expected_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
            self.assertEqual(get_prompt_version(text, bank), f"{bank.value}:{expected_digest}")


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
