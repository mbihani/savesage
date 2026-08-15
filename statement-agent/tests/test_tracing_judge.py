"""Stdlib unit tests for judge-verdict -> metrics mapping (WS4, requirement 4).

No mlflow import — exercises harness/tracing_judge.py. Verifies per-field
agreement fractions and aggregate accuracy over the seven judged fields.
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
        self.assertEqual(m["judge.cards_cardMeta_cardDisplayName"], 1.0)
        self.assertEqual(m["judge.cards_cardMeta_lastFourDigit"], 1.0)
        self.assertEqual(m["judge.rewards_pointsEarnedThisCycle"], 1.0)
        self.assertEqual(m["judge.rewards_closingPoints"], 1.0)

    def test_partial_disagreement(self):
        v = JudgeVerdict(
            "req-1", "opus",
            (
                _scalar("cards[].cardMeta.cardDisplayName", "A", "B", ComparisonOutcome.DISAGREE),
                _scalar("cards[].cardMeta.lastFourDigit", "1234", "1234"),
            ),
            100.0,
        )
        m = verdict_to_metrics(v)
        self.assertEqual(m["judge.accuracy"], 0.5)
        self.assertEqual(m["judge.cards_cardMeta_cardDisplayName"], 0.0)
        self.assertEqual(m["judge.cards_cardMeta_lastFourDigit"], 1.0)

    def test_transaction_row_aggregation(self):
        v = JudgeVerdict(
            "req-1", "opus",
            (
                _row("transactions[].amount", 100.0, 100.0, ComparisonOutcome.AGREE),
                _row("transactions[].amount", 200.0, 999.0, ComparisonOutcome.DISAGREE),
                _row("transactions[].amount", 300.0, 300.0, ComparisonOutcome.AGREE),
            ),
            100.0,
        )
        m = verdict_to_metrics(v)
        self.assertAlmostEqual(m["judge.transactions_amount"], 2 / 3, places=4)
        self.assertEqual(m["judge.comparisons"], 3.0)
        self.assertAlmostEqual(m["judge.accuracy"], 2 / 3, places=4)

    def test_empty_verdict(self):
        v = JudgeVerdict("req-1", "opus", (), 0.0)
        m = verdict_to_metrics(v)
        self.assertEqual(m["judge.accuracy"], 0.0)
        self.assertEqual(m["judge.comparisons"], 0.0)


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
        self.assertEqual(payload["metadata"]["accuracy"], 1.0)
        self.assertIn("rewards_closingPoints", payload["metadata"]["per_field"])
        self.assertEqual(payload["metadata"]["match_method"], "DESCRIPTION_SIMILARITY_1TO1")
        self.assertEqual(payload["metadata"]["latency_ms"], 50.0)


if __name__ == "__main__":
    unittest.main()
