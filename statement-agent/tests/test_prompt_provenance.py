import hashlib
from pathlib import Path
import unittest

from contracts.models import Bank
from rules.routing import PROMPT_BY_BANK

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_SHA256 = {
    Bank.HDFC: "fd92b25b878176bbb46bed2fd78e8cb5445c7f245ee983d9f8fd01da74ce07ef",
    Bank.ICICI: "8f13a2d35d8b53d7f29f23148912dceb0035ef807165b4c7a54b58d693ca9b2f",
    Bank.SBI: "b7e06b291803cbcf46bbc6a07af427363d545d3c87d39ee8f64113c8058b3b92",
    Bank.AXIS: "ca0372008e7623b8a370f7703256812762327abce4a0a1e07c897b58488fd964",
    # GENERIC reuses axis.txt (the generic Luna prompt); same SHA-256 as AXIS.
    Bank.GENERIC: "ca0372008e7623b8a370f7703256812762327abce4a0a1e07c897b58488fd964",
}

# Judge ground-truth prompt (Opus-5).  Pinned separately from the bank
# extractor prompts above — it is a single prompt, not bank-keyed, loaded by
# harness/judge_adapter.py:PROMPT_PATH.  Mirrors the bank-prompt pattern: read
# the bytes, assert non-empty, assert the SHA-256 matches.  Non-vacuous: a
# prompt edit changes the hash and fails the assertion, directing the author
# to update JUDGE_PROMPT_SHA256 here and the matching entry in
# judge/PROVENANCE.md together.  The pin is what catches the drift that left
# the 21 fields added in PR #47 permanently ABSENT_IN_PDF — an unpinned prompt
# silently reverted to the 7-field shape and the parser rejected the rest.
JUDGE_PROMPT_SHA256 = "7843143264e37995d18e5fd3eaf64e1fc9a562f4de76ac58681332b68decbd86"


class PromptProvenanceTest(unittest.TestCase):
    def test_every_bank_resolves_to_nonempty_verified_prompt(self) -> None:
        self.assertEqual(set(PROMPT_BY_BANK), set(Bank))
        for bank, expected_hash in EXPECTED_SHA256.items():
            path = PROMPT_BY_BANK[bank]
            content = path.read_bytes()
            self.assertGreater(len(content.strip()), 0, bank.value)
            self.assertEqual(hashlib.sha256(content).hexdigest(), expected_hash, bank.value)

    def test_judge_ground_truth_prompt_matches_pinned_hash(self) -> None:
        """The Opus-5 ground-truth prompt (judge/prompt_v1.txt) is pinned so a
        silent edit surfaces as a test failure — the prompt drives which of the
        28 judged fields Opus extracts, and an unpinned drift is what left the
        21 fields added in PR #47 permanently ABSENT_IN_PDF.  Path resolution
        mirrors ``harness/judge_adapter.PROMPT_PATH`` (parents[1] / "judge" /
        "prompt_v1.txt") without importing the adapter, so the provenance test
        stays free of the Opus HTTP/auth call chain."""
        path = ROOT / "judge" / "prompt_v1.txt"
        content = path.read_bytes()
        self.assertGreater(len(content.strip()), 0, "judge prompt_v1.txt")
        self.assertEqual(
            hashlib.sha256(content).hexdigest(), JUDGE_PROMPT_SHA256,
            "judge/prompt_v1.txt changed — update JUDGE_PROMPT_SHA256 and "
            "judge/PROVENANCE.md together",
        )


if __name__ == "__main__":
    unittest.main()
