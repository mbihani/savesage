from datetime import UTC, datetime
import unittest

from contracts.models import FieldFeedback
from contracts.paths import canonical_feedback_path, is_valid_feedback_path


class FeedbackPathTest(unittest.TestCase):
    def test_scalar_path(self) -> None:
        self.assertEqual(canonical_feedback_path("rewards.closingPoints"), "rewards.closingPoints")

    def test_indexed_transaction_path(self) -> None:
        self.assertEqual(
            canonical_feedback_path("transactions[].amount", row_index=14),
            "transactions.14.amount",
        )

    def test_indexed_card_path(self) -> None:
        self.assertEqual(
            canonical_feedback_path("cards[].cardMeta.cardDisplayName", card_index=0),
            "cards.0.cardMeta.cardDisplayName",
        )

    def test_malformed_paths_are_rejected(self) -> None:
        for path in ("/cards/*/cardMeta/lastFourDigit", "transactions[].amount", "cards.01.cardMeta.lastFourDigit"):
            with self.subTest(path=path):
                self.assertFalse(is_valid_feedback_path(path))
                with self.assertRaises(ValueError):
                    FieldFeedback("req", path, None, None, True, "synthetic-actor", datetime.now(UTC))

    def test_helper_rejects_missing_or_irrelevant_index(self) -> None:
        with self.assertRaises(ValueError):
            canonical_feedback_path("transactions[].description")
        with self.assertRaises(ValueError):
            canonical_feedback_path("rewards.closingPoints", row_index=0)


if __name__ == "__main__":
    unittest.main()
