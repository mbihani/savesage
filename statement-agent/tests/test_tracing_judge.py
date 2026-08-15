"""Stdlib unit tests for judge-verdict -> metrics mapping (WS4, requirement 4; review B5).

No mlflow import — exercises harness/tracing_judge.py. Verifies the accuracy
policy MIRRORS WS5's judge/aggregation.py exactly: FORMAT_ONLY counts as
correct, ABSENT_IN_PDF is excluded from the denominator, UNMATCHED_ROW is scored-
but-wrong, and narration-forgiven forgives DISAGREE on transactions[].description.
Covers EVERY ComparisonOutcome value plus an unmatched-row case.
"""

import unittest

from contracts.models import (
    ComparisonOutcome,
    FieldComparison,
    FieldScope,
    JudgeVerdict,
    MatchMethod,
)
from harness.tracing_judge import build_judge_feedback, verdict_to_metrics


def _scalar(field, exp, act, outcome=ComparisonOutcome.AGREE):
    return FieldComparison(field, exp, act, outcome, FieldScope.SCALAR)


def _row(field, exp, act, outcome=ComparisonOutcome.AGREE):
    return FieldComparison(
        field, exp, act, outcome, FieldScope.TRANSACTION_ROW,
        MatchMethod.DESCRIPTION_SIMILARITY_1TO1,
    )


class VerdictMetricsTest(unittest.TestCase):
    def test_all_agree_scalar(self):
        v = JudgeVerdict(
            "req-1", "databricks-claude-opus-5",
            (
                _scalar("cards[].cardMeta.cardDisplayName", "A", "A"),
                _scalar("cards[].cardMeta.lastFourDigit", "1234", "1234"),
                _scalar("rewards.pointsEarnedThisCycle", 10, 10),
                _scalar("rewards.closingPoints", 500, 500),
            ),
            100.0,
        )
        m = verdict_to_metrics(v)
        self.assertEqual(m["judge.accuracy"], 1.0)
        self.assertEqual(m["judge.comparisons"], 4.0)
        self.assertEqual(m["judge.scored"], 4.0)
        self.assertEqual(m["judge.correct"], 4.0)
        self.assertEqual(m["judge.cards_cardMeta_cardDisplayName"], 1.0)

    def test_format_only_counts_as_correct(self):
        # FORMAT_ONLY is NOT an error (repo scoring discipline, mirror WS5).
        v = JudgeVerdict("req-1", "opus",
                         (_scalar("cards[].cardMeta.lastFourDigit", "1234", "1234",
                                  ComparisonOutcome.FORMAT_ONLY),
                          _scalar("rewards.closingPoints", 100, 100)),
                         100.0)
        m = verdict_to_metrics(v)
        self.assertEqual(m["judge.accuracy"], 1.0)  # both correct
        self.assertEqual(m["judge.correct"], 2.0)
        self.assertEqual(m["judge.scored"], 2.0)

    def test_absent_in_pdf_excluded_from_denominator(self):
        # ABSENT_IN_PDF is not a model error -> excluded from scored (denominator).
        v = JudgeVerdict("req-1", "opus",
                         (_scalar("rewards.closingPoints", 100, 100),  # AGREE
                          _scalar("rewards.pointsEarnedThisCycle", None, None,
                                  ComparisonOutcome.ABSENT_IN_PDF)),
                         100.0)
        m = verdict_to_metrics(v)
        self.assertEqual(m["judge.scored"], 1.0)  # only the AGREE is scored
        self.assertEqual(m["judge.correct"], 1.0)
        self.assertEqual(m["judge.accuracy"], 1.0)  # 1/1
        self.assertEqual(m["judge.comparisons"], 2.0)  # total still 2

    def test_disagree_is_wrong_strict(self):
        v = JudgeVerdict("req-1", "opus",
                         (_scalar("cards[].cardMeta.cardDisplayName", "A", "B",
                                  ComparisonOutcome.DISAGREE),
                          _scalar("cards[].cardMeta.lastFourDigit", "1234", "1234")),
                         100.0)
        m = verdict_to_metrics(v)
        self.assertEqual(m["judge.accuracy"], 0.5)

    def test_unmatched_row_scored_but_wrong(self):
        v = JudgeVerdict("req-1", "opus",
                         (_row("transactions[].amount", None, 100.0, ComparisonOutcome.UNMATCHED_ROW),
                          _row("transactions[].date", None, "01/01", ComparisonOutcome.UNMATCHED_ROW),
                          _row("transactions[].description", None, "X", ComparisonOutcome.UNMATCHED_ROW)),
                         100.0)
        m = verdict_to_metrics(v)
        # All 3 scored (denominator=3), none correct.
        self.assertEqual(m["judge.scored"], 3.0)
        self.assertEqual(m["judge.correct"], 0.0)
        self.assertEqual(m["judge.accuracy"], 0.0)

    def test_narration_forgiven_forgives_description_disagree(self):
        v = JudgeVerdict("req-1", "opus",
                         (_row("transactions[].description", "A", "B", ComparisonOutcome.DISAGREE),
                          _row("transactions[].amount", 100.0, 100.0)),
                         100.0)
        m = verdict_to_metrics(v)
        self.assertEqual(m["judge.accuracy"], 0.5)  # strict: 1/2
        self.assertEqual(m["judge.accuracy_forgiven"], 1.0)  # forgiven: 2/2

    def test_narration_forgiven_does_not_forgive_amount_disagree(self):
        v = JudgeVerdict("req-1", "opus",
                         (_row("transactions[].amount", 100.0, 200.0, ComparisonOutcome.DISAGREE),
                          _row("transactions[].description", "A", "A")),
                         100.0)
        m = verdict_to_metrics(v)
        self.assertEqual(m["judge.accuracy"], 0.5)
        self.assertEqual(m["judge.accuracy_forgiven"], 0.5)  # amount not forgiven

    def test_transaction_row_aggregation(self):
        v = JudgeVerdict("req-1", "opus",
                         (_row("transactions[].amount", 100.0, 100.0),
                          _row("transactions[].amount", 200.0, 999.0, ComparisonOutcome.DISAGREE),
                          _row("transactions[].amount", 300.0, 300.0)),
                         100.0)
        m = verdict_to_metrics(v)
        self.assertAlmostEqual(m["judge.transactions_amount"], 2 / 3, places=4)
        self.assertEqual(m["judge.comparisons"], 3.0)
        self.assertAlmostEqual(m["judge.accuracy"], 2 / 3, places=4)

    def test_empty_verdict_accuracy_is_none(self):
        # No scored comparisons -> accuracy is None (mirrors WS5 returning None).
        v = JudgeVerdict("req-1", "opus", (), 0.0)
        m = verdict_to_metrics(v)
        self.assertIsNone(m["judge.accuracy"])
        self.assertEqual(m["judge.comparisons"], 0.0)
        self.assertEqual(m["judge.scored"], 0.0)

    def test_all_absent_accuracy_is_none(self):
        # All ABSENT_IN_PDF -> scored=0 -> accuracy=None.
        v = JudgeVerdict("req-1", "opus",
                         (_scalar("rewards.closingPoints", None, None, ComparisonOutcome.ABSENT_IN_PDF),),
                         0.0)
        m = verdict_to_metrics(v)
        self.assertIsNone(m["judge.accuracy"])
        self.assertEqual(m["judge.scored"], 0.0)

    def test_every_outcome_coverage(self):
        """One comparison per ComparisonOutcome value — verifies each is handled."""
        cases = [
            ("rewards.closingPoints", 1, 1, ComparisonOutcome.AGREE, True),  # correct
            ("rewards.pointsEarnedThisCycle", 1, 2, ComparisonOutcome.DISAGREE, False),
            ("cards[].cardMeta.lastFourDigit", "1", "1", ComparisonOutcome.FORMAT_ONLY, True),  # correct
            ("cards[].cardMeta.cardDisplayName", None, None, ComparisonOutcome.ABSENT_IN_PDF, None),  # excluded
        ]
        comps = [_scalar(f, e, a, o) for f, e, a, o, _ in cases]
        comps.append(_row("transactions[].amount", None, 1.0, ComparisonOutcome.UNMATCHED_ROW))
        v = JudgeVerdict("req-1", "opus", tuple(comps), 0.0)
        m = verdict_to_metrics(v)
        # scored = AGREE + DISAGREE + FORMAT_ONLY + UNMATCHED_ROW = 4 (ABSENT excluded)
        self.assertEqual(m["judge.scored"], 4.0)
        # correct = AGREE + FORMAT_ONLY = 2
        self.assertEqual(m["judge.correct"], 2.0)
        self.assertEqual(m["judge.accuracy"], 0.5)
        self.assertEqual(m["judge.comparisons"], 5.0)


class JudgeFeedbackPayloadTest(unittest.TestCase):
    def test_payload_shape(self):
        v = JudgeVerdict(
            "req-1", "databricks-claude-opus-5",
            (_scalar("rewards.closingPoints", 100, 100),),
            50.0, summary="all good",
        )
        payload = build_judge_feedback(v)
        self.assertEqual(payload["name"], "judge_accuracy")
        self.assertEqual(payload["value"], 1.0)
        self.assertEqual(payload["source_type"], "LLM_JUDGE")
        self.assertEqual(payload["source_id"], "databricks-claude-opus-5")
        self.assertEqual(payload["rationale"], "all good")
        self.assertEqual(payload["metadata"]["comparisons"], 1.0)
        self.assertEqual(payload["metadata"]["scored"], 1.0)
        self.assertEqual(payload["metadata"]["correct"], 1.0)
        self.assertEqual(payload["metadata"]["accuracy_strict"], 1.0)
        self.assertEqual(payload["metadata"]["accuracy_narration_forgiven"], 1.0)
        self.assertIn("rewards_closingPoints", payload["metadata"]["per_field"])
        self.assertIn("mirrors judge/aggregation.py", payload["metadata"]["policy"])

    def test_payload_value_none_when_no_scored(self):
        v = JudgeVerdict("req-1", "opus", (), 0.0)
        payload = build_judge_feedback(v)
        self.assertIsNone(payload["value"])
        self.assertIn("no scored comparisons", payload["rationale"])


if __name__ == "__main__":
    unittest.main()
