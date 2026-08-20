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
    Bank.AXIS: "e8e90c6cf0fa68e7ddae91a4aa008ca32d65546811c79c10cc9025e4fd47cd9f",
}


class PromptProvenanceTest(unittest.TestCase):
    def test_every_bank_resolves_to_nonempty_verified_prompt(self) -> None:
        self.assertEqual(set(PROMPT_BY_BANK), set(Bank))
        for bank, expected_hash in EXPECTED_SHA256.items():
            path = PROMPT_BY_BANK[bank]
            content = path.read_bytes()
            self.assertGreater(len(content.strip()), 0, bank.value)
            self.assertEqual(hashlib.sha256(content).hexdigest(), expected_hash, bank.value)


if __name__ == "__main__":
    unittest.main()
