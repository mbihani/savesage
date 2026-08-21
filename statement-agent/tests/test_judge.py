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
    def test_all_judged_fields_format_only_and_forgiven_rollup(self):
        expected = {"cards": [{"cardMeta": {"cardDisplayName": "Synthetic Card", "lastFourDigit": "1234"}}],
                    "rewards": {"pointsEarnedThisCycle": 100, "closingPoints": None},
                    "transactions": [{"date": "1/2/2026", "description": "Synthetic Shop!", "amount": "1,23,456.78"}]}
        actual = {"cards": [{"cardMeta": {"cardDisplayName": "synthetic  card", "lastFourDigit": "XXXX1234"}}],
                  "rewards": {"pointsEarnedThisCycle": "100.0", "closingPoints": 777, "openingPoints": 9},
                  "transactions": [{"date": "01/02/2026", "description": "Synthetic Shop", "amount": 123456.78,
                                    "direction": "DEBIT"}]}
        request = ParseRequest(b"synthetic", "synthetic.pdf", Bank.HDFC, "synthetic-request")
        comparisons = build_comparisons(request, expected, actual)
        # Every one of the 28 judged fields produces exactly one comparison
        # (one card, one matched transaction row; the new fields are absent
        # from this fixture so they surface as ABSENT_IN_PDF, not scored).
        from contracts.models import JUDGED_FIELDS
        self.assertEqual({item.field_path for item in comparisons}, set(JUDGED_FIELDS))
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


class NewFieldRoutingTest(unittest.TestCase):
    """Comparator routing coverage for the 21 new fields added in the 7→28
    expansion.  Verifies that the explicit DATE_PATHS / NUMERIC_PATHS sets in
    judge/comparison.py route each new field to the correct normalizer so
    format-only differences AGREE and wrong values DISAGREE."""

    def _comps(self, expected, actual):
        request = ParseRequest(b"synthetic", "synthetic.pdf", Bank.HDFC, "synthetic-request")
        return {c.field_path: c for c in build_comparisons(request, expected, actual)}

    # --- statementMeta date fields → norm_date ---

    def test_statement_meta_dates_normalize_format_differences(self):
        """statementMeta date fields route to norm_date: a format-only
        difference (2026-01-01 vs 01/01/2026) produces FORMAT_ONLY, while
        an identical date produces AGREE."""
        by_path = self._comps(
            {"statementMeta": {"statementDate": "2026-01-01", "dueDate": "2026-02-01"}},
            {"statementMeta": {"statementDate": "01/01/2026", "dueDate": "2026-02-01"}},
        )
        self.assertIs(by_path["statementMeta.statementDate"].outcome,
                      ComparisonOutcome.FORMAT_ONLY)
        self.assertIs(by_path["statementMeta.dueDate"].outcome,
                      ComparisonOutcome.AGREE)

    def test_statement_meta_dates_wrong_value_disagrees(self):
        """Different dates produce DISAGREE (not masked by norm_date)."""
        by_path = self._comps(
            {"statementMeta": {"statementDate": "2026-01-01"}},
            {"statementMeta": {"statementDate": "2026-03-15"}},
        )
        self.assertIs(by_path["statementMeta.statementDate"].outcome,
                      ComparisonOutcome.DISAGREE)

    # --- statementLevelSummary numeric fields → norm_num ---

    def test_statement_summary_numeric_tolerance_agrees(self):
        """statementLevelSummary numeric fields route to norm_num: a
        within-tolerance difference (5000 vs 5000.01) produces FORMAT_ONLY."""
        by_path = self._comps(
            {"statementLevelSummary": {"totalAmountDue": 5000, "totalCreditLimit": 100000}},
            {"statementLevelSummary": {"totalAmountDue": 5000.01, "totalCreditLimit": 100000}},
        )
        self.assertIs(by_path["statementLevelSummary.totalAmountDue"].outcome,
                      ComparisonOutcome.FORMAT_ONLY)
        self.assertIs(by_path["statementLevelSummary.totalCreditLimit"].outcome,
                      ComparisonOutcome.AGREE)

    def test_statement_summary_numeric_wrong_value_disagrees(self):
        """A clearly different numeric value produces DISAGREE."""
        by_path = self._comps(
            {"statementLevelSummary": {"totalCreditLimit": 100000}},
            {"statementLevelSummary": {"totalCreditLimit": 95000}},
        )
        self.assertIs(by_path["statementLevelSummary.totalCreditLimit"].outcome,
                      ComparisonOutcome.DISAGREE)

    # --- cards[].bigPicture numeric fields → norm_num ---

    def test_card_big_picture_numeric_tolerance(self):
        """bigPicture cardCreditLimit / cardAvailableCreditLimit route to
        norm_num: within-tolerance → FORMAT_ONLY."""
        by_path = self._comps(
            {"cards": [{"bigPicture": {"cardCreditLimit": 100000, "cardAvailableCreditLimit": 95000}}]},
            {"cards": [{"bigPicture": {"cardCreditLimit": 100000.01, "cardAvailableCreditLimit": 95000}}]},
        )
        self.assertIs(by_path["cards[].bigPicture.cardCreditLimit"].outcome,
                      ComparisonOutcome.FORMAT_ONLY)
        self.assertIs(by_path["cards[].bigPicture.cardAvailableCreditLimit"].outcome,
                      ComparisonOutcome.AGREE)

    # --- transactions[].direction enum → norm_desc ---

    def test_transaction_direction_case_insensitive(self):
        """transactions[].direction routes to norm_desc: case differences
        (DEBIT vs debit) produce FORMAT_ONLY (canonical equal, raw differ)."""
        by_path = self._comps(
            {"transactions": [{"description": "Shop", "direction": "DEBIT", "amount": 100}]},
            {"transactions": [{"description": "Shop", "direction": "debit", "amount": 100}]},
        )
        self.assertIs(by_path["transactions[].direction"].outcome,
                      ComparisonOutcome.FORMAT_ONLY)

    def test_transaction_direction_wrong_value_disagrees(self):
        """Different direction values (DEBIT vs CREDIT) produce DISAGREE."""
        by_path = self._comps(
            {"transactions": [{"description": "Shop", "direction": "DEBIT", "amount": 100}]},
            {"transactions": [{"description": "Shop", "direction": "CREDIT", "amount": 100}]},
        )
        self.assertIs(by_path["transactions[].direction"].outcome,
                      ComparisonOutcome.DISAGREE)

    # --- transactions[].rewardPointsOnThisTransaction → norm_num ---

    def test_transaction_reward_points_numeric_tolerance(self):
        """rewardPointsOnThisTransaction routes to norm_num: within-tolerance
        → FORMAT_ONLY."""
        by_path = self._comps(
            {"transactions": [{"description": "Shop", "amount": 100,
                                "rewardPointsOnThisTransaction": 5}]},
            {"transactions": [{"description": "Shop", "amount": 100,
                                "rewardPointsOnThisTransaction": 5.01}]},
        )
        self.assertIs(by_path["transactions[].rewardPointsOnThisTransaction"].outcome,
                      ComparisonOutcome.FORMAT_ONLY)

    def test_transaction_reward_points_wrong_value_disagrees(self):
        """Different reward points produce DISAGREE."""
        by_path = self._comps(
            {"transactions": [{"description": "Shop", "amount": 100,
                                "rewardPointsOnThisTransaction": 5}]},
            {"transactions": [{"description": "Shop", "amount": 100,
                                "rewardPointsOnThisTransaction": 50}]},
        )
        self.assertIs(by_path["transactions[].rewardPointsOnThisTransaction"].outcome,
                      ComparisonOutcome.DISAGREE)

    # --- null-in-extraction vs value-in-PDF → DISAGREE/absent (not norm_desc artifact) ---

    def test_numeric_field_null_in_extraction_disagrees(self):
        """A numeric field with a value in the PDF (expected) but null in the
        extraction (actual) produces DISAGREE — not masked by norm_desc."""
        by_path = self._comps(
            {"statementLevelSummary": {"totalAmountDue": 5000}},
            {"statementLevelSummary": {"totalAmountDue": None}},
        )
        self.assertIs(by_path["statementLevelSummary.totalAmountDue"].outcome,
                      ComparisonOutcome.DISAGREE)

    def test_numeric_field_absent_in_extraction_disagrees(self):
        """A numeric field present in the PDF but entirely absent from the
        extraction dict also produces DISAGREE (not ABSENT_IN_PDF — the
        ABSENT_IN_PDF sentinel is for expected=None, not actual=None)."""
        by_path = self._comps(
            {"statementLevelSummary": {"totalAmountDue": 5000}},
            {"statementLevelSummary": {}},
        )
        self.assertIs(by_path["statementLevelSummary.totalAmountDue"].outcome,
                      ComparisonOutcome.DISAGREE)

    def test_numeric_field_null_in_pdf_is_absent(self):
        """A numeric field absent from the PDF (expected=None) but present in
        extraction produces ABSENT_IN_PDF (not scored — not a model error)."""
        by_path = self._comps(
            {"statementLevelSummary": {"totalAmountDue": None}},
            {"statementLevelSummary": {"totalAmountDue": 5000}},
        )
        self.assertIs(by_path["statementLevelSummary.totalAmountDue"].outcome,
                      ComparisonOutcome.ABSENT_IN_PDF)

    # --- rewards.programType (string) routes to norm_desc, NOT norm_num ---

    def test_rewards_program_type_string_compare(self):
        """rewards.programType is a string and routes to norm_desc (the
        "rewards." prefix heuristic was DROPPED so it is NOT mis-compared as
        a number).  Different program types produce DISAGREE."""
        by_path = self._comps(
            {"rewards": {"programType": "Cashback", "pointsEarnedThisCycle": 100}},
            {"rewards": {"programType": "Reward Points", "pointsEarnedThisCycle": 100}},
        )
        self.assertIs(by_path["rewards.programType"].outcome,
                      ComparisonOutcome.DISAGREE)
        # pointsEarnedThisCycle is numeric and equal → AGREE.
        self.assertIs(by_path["rewards.pointsEarnedThisCycle"].outcome,
                      ComparisonOutcome.AGREE)

    # --- cards[].cardMeta.productFamily / network → norm_desc ---

    def test_card_meta_product_family_and_network(self):
        """cardMeta.productFamily and cardMeta.network route to norm_desc:
        case differences → FORMAT_ONLY; different values → DISAGREE."""
        by_path = self._comps(
            {"cards": [{"cardMeta": {"productFamily": "Platinum", "network": "VISA"}}]},
            {"cards": [{"cardMeta": {"productFamily": "platinum", "network": "Mastercard"}}]},
        )
        self.assertIs(by_path["cards[].cardMeta.productFamily"].outcome,
                      ComparisonOutcome.FORMAT_ONLY)
        self.assertIs(by_path["cards[].cardMeta.network"].outcome,
                      ComparisonOutcome.DISAGREE)

    # --- statementMeta.issuerName → norm_desc ---

    def test_issuer_name_case_insensitive(self):
        """statementMeta.issuerName routes to norm_desc: case differences
        produce FORMAT_ONLY."""
        by_path = self._comps(
            {"statementMeta": {"issuerName": "HDFC Bank"}},
            {"statementMeta": {"issuerName": "hdfc bank"}},
        )
        self.assertIs(by_path["statementMeta.issuerName"].outcome,
                      ComparisonOutcome.FORMAT_ONLY)


if __name__ == "__main__":
    unittest.main()
