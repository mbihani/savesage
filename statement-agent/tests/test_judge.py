import importlib.util
from pathlib import Path
import unittest

from contracts.models import (
    Bank, ComparisonOutcome, FieldComparison, FieldScope, MatchMethod, ParseRequest,
)
from judge.aggregation import aggregate
from judge.comparison import build_comparisons
from judge.evidence import text_supports_value
from judge.matching import match_transactions
from judge.normalization import norm_date, norm_desc, norm_num

ROOT = Path(__file__).resolve().parents[2]


def legacy_hdfc():
    spec = importlib.util.spec_from_file_location("legacy_hdfc_score", ROOT / "hdfc" / "score_lib.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


class NormalizationParityTest(unittest.TestCase):
    def test_port_matches_original_on_table(self):
        legacy = legacy_hdfc()
        dates = [None, "", "1/2/2026", "2026-02-01", "03 Feb, 2026", "18/04/2026 | 00:00"]
        numbers = [None, True, 12, "₹ 1,23,456.78 DR", "Rs. 200 Cr", "(1,000.50)", "oops"]
        descriptions = [None, "  Synthetic   Shop, Pune!  ", "A/B-C", "ＦＯＯ"]
        for value in dates:
            self.assertEqual(norm_date(value), legacy.norm_date(value))
        for value in numbers:
            self.assertEqual(norm_num(value), legacy.norm_num(value))
        for value in descriptions:
            self.assertEqual(norm_desc(value), legacy.norm_desc(value))


class MatchingDisciplineTest(unittest.TestCase):
    def test_description_only_one_to_one_and_order_insensitive(self):
        actual = [{"description": "Second Shop", "date": "09/09/2099", "amount": 999},
                  {"description": "First Shop", "date": "08/08/2088", "amount": 888}]
        expected = [{"description": "First Shop", "date": "01/01/2026", "amount": 10},
                    {"description": "Second Shop", "date": "02/01/2026", "amount": 20}]
        pairs, unmatched_actual, unmatched_expected = match_transactions(actual, expected, 0.60)
        self.assertEqual([(a, e) for a, e, _ in pairs], [(1, 0), (0, 1)])
        self.assertEqual((unmatched_actual, unmatched_expected), ([], []))

    def test_date_and_amount_cannot_rescue_wrong_description(self):
        actual = [{"description": "Completely Different", "date": "01/01/2026", "amount": 10}]
        expected = [{"description": "Synthetic Merchant", "date": "01/01/2026", "amount": 10}]
        pairs, unmatched_actual, unmatched_expected = match_transactions(actual, expected, 0.60)
        self.assertEqual(pairs, [])
        self.assertEqual((unmatched_actual, unmatched_expected), ([0], [0]))

    def test_equal_description_ties_use_relative_position(self):
        actual = [{"description": "Repeated Synthetic", "amount": 200},
                  {"description": "Repeated Synthetic", "amount": 100}]
        expected = [{"description": "Repeated Synthetic", "amount": 100},
                    {"description": "Repeated Synthetic", "amount": 200}]
        pairs, _, _ = match_transactions(actual, expected, 0.60)
        self.assertEqual([(actual_index, expected_index) for actual_index, expected_index, _ in pairs],
                         [(0, 0), (1, 1)])


class ComparisonTest(unittest.TestCase):
    def test_only_seven_fields_format_only_and_forgiven_rollup(self):
        expected = {"cards": [{"cardMeta": {"cardDisplayName": "Synthetic Card", "lastFourDigit": "1234"}}],
                    "rewards": {"pointsEarnedThisCycle": 100, "closingPoints": None},
                    "transactions": [{"date": "1/2/2026", "description": "Synthetic Shop!", "amount": "1,23,456.78"}]}
        actual = {"cards": [{"cardMeta": {"cardDisplayName": "synthetic  card", "lastFourDigit": "XXXX1234"}}],
                  "rewards": {"pointsEarnedThisCycle": "100.0", "closingPoints": 777, "openingPoints": 9},
                  "transactions": [{"date": "01/02/2026", "description": "Synthetic Shop", "amount": 123456.78,
                                    "direction": "DEBIT"}]}
        request = ParseRequest(b"synthetic", "synthetic.pdf", Bank.HDFC, "synthetic-request")
        comparisons = build_comparisons(request, expected, actual)
        self.assertEqual({item.field_path for item in comparisons}, {
            "cards[].cardMeta.cardDisplayName", "cards[].cardMeta.lastFourDigit",
            "rewards.pointsEarnedThisCycle", "rewards.closingPoints",
            "transactions[].date", "transactions[].description", "transactions[].amount"})
        by_path = {item.field_path: item for item in comparisons}
        self.assertIs(by_path["transactions[].date"].outcome, ComparisonOutcome.FORMAT_ONLY)
        self.assertIs(by_path["transactions[].amount"].outcome, ComparisonOutcome.FORMAT_ONLY)
        self.assertIs(by_path["rewards.closingPoints"].outcome, ComparisonOutcome.ABSENT_IN_PDF)
        totals = aggregate(comparisons)
        self.assertEqual(totals["strict"], {"correct": 5, "scored": 6, "accuracy": 5 / 6})
        self.assertEqual(totals["narration_forgiven"], {"correct": 6, "scored": 6, "accuracy": 1.0})
        self.assertGreater(totals["narration_forgiven"]["accuracy"], totals["strict"]["accuracy"])

    def test_card_display_name_retains_lenient_containment(self):
        expected = {"cards": [{"cardMeta": {"cardDisplayName": "HDFC Platinum Card"}}]}
        actual = {"cards": [{"cardMeta": {"cardDisplayName": "HDFC Platinum"}}]}
        request = ParseRequest(b"synthetic", "synthetic.pdf", Bank.HDFC, "synthetic-request")
        comparison = next(item for item in build_comparisons(request, expected, actual)
                          if item.field_path == "cards[].cardMeta.cardDisplayName")
        self.assertIs(comparison.outcome, ComparisonOutcome.FORMAT_ONLY)

    def test_numeric_tolerance_boundary_and_above(self):
        request = ParseRequest(b"synthetic", "synthetic.pdf", Bank.HDFC, "synthetic-request")
        expected = {"rewards": {"pointsEarnedThisCycle": 100.0}}
        at_boundary = build_comparisons(request, expected, {"rewards": {"pointsEarnedThisCycle": 100.01}})
        above_boundary = build_comparisons(request, expected, {"rewards": {"pointsEarnedThisCycle": 100.011}})
        boundary = next(item for item in at_boundary if item.field_path == "rewards.pointsEarnedThisCycle")
        above = next(item for item in above_boundary if item.field_path == "rewards.pointsEarnedThisCycle")
        self.assertIs(boundary.outcome, ComparisonOutcome.FORMAT_ONLY)
        self.assertIs(above.outcome, ComparisonOutcome.DISAGREE)

    def test_unmatched_row_is_charged_in_both_rollups(self):
        comparison = FieldComparison(
            "transactions[].description", "Synthetic Merchant", None,
            ComparisonOutcome.UNMATCHED_ROW, FieldScope.TRANSACTION_ROW,
            MatchMethod.DESCRIPTION_SIMILARITY_1TO1,
        )
        totals = aggregate([comparison])
        self.assertEqual(totals["strict"], {"correct": 0, "scored": 1, "accuracy": 0.0})
        self.assertEqual(totals["narration_forgiven"], {"correct": 0, "scored": 1, "accuracy": 0.0})


class EvidenceHardeningTest(unittest.TestCase):
    def test_wrapped_indian_grouped_unpadded_and_boundary_cases(self):
        self.assertTrue(text_supports_value("Synthetic\nCard", "Synthetic Card"))
        self.assertTrue(text_supports_value("Amount Rs 1,23,456.78", 123456.78, "number"))
        self.assertTrue(text_supports_value("Points 100", 100, "number"))
        self.assertTrue(text_supports_value("Date 1/2/2026", "01/02/2026", "date"))
        self.assertFalse(text_supports_value("VISAKHAPATNAM", "VISA"))
        self.assertFalse(text_supports_value("Value 194,022.00", 94022, "number"))


if __name__ == "__main__":
    unittest.main()
