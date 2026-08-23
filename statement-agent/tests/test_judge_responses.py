import json
import unittest
from unittest.mock import patch

from contracts.models import Bank, ComparisonOutcome, ExtractionResult, ParseRequest
from harness.judge_adapter import OpusJudgeAdapter
from judge.opus import completion_reason, extract_response_text, parse_ground_truth

GROUND_TRUTH = json.dumps({
    "cards": [],
    "rewards": {"pointsEarnedThisCycle": None, "closingPoints": None},
    "transactions": [],
})

# Synthetic native Messages fixture. The live gateway currently normalizes to OpenAI,
# but native parsing is retained for gateways that pass Anthropic's envelope through.
ANTHROPIC_RESPONSE = {
    "id": "msg_synthetic",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "thinking", "thinking": "synthetic"},
                {"type": "text", "text": GROUND_TRUTH}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 1, "output_tokens": 1},
}

# Synthetic fixture with the exact top-level keys observed in the one live call.
OPENAI_NORMALIZED_RESPONSE = {
    "model": "synthetic-opus",
    "choices": [{"index": 0, "message": {"role": "assistant", "content": GROUND_TRUTH},
                 "finish_reason": "stop", "id": "choice_synthetic"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    "object": "chat.completion",
    "id": "response_synthetic",
    "created": 0,
}


class ResponseEnvelopeTest(unittest.TestCase):
    def test_anthropic_native_fixture(self):
        self.assertEqual(extract_response_text(ANTHROPIC_RESPONSE), GROUND_TRUTH)
        self.assertEqual(completion_reason(ANTHROPIC_RESPONSE), "end_turn")

    def test_openai_normalized_recorded_fixture(self):
        self.assertEqual(extract_response_text(OPENAI_NORMALIZED_RESPONSE), GROUND_TRUTH)
        self.assertEqual(completion_reason(OPENAI_NORMALIZED_RESPONSE), "stop")

    def test_anthropic_plain_string_content(self):
        self.assertEqual(extract_response_text({"content": GROUND_TRUTH}), GROUND_TRUTH)


class DegenerateJudgeResponseTest(unittest.TestCase):
    def setUp(self):
        self.request = ParseRequest(b"synthetic-pdf", "synthetic.pdf", Bank.HDFC, "synthetic-request")
        self.extraction = ExtractionResult("synthetic-request", {}, "synthetic-model", 1.0)

    def assert_judge_error(self, response, expected_fragment):
        with patch("harness.judge_adapter.invoke_opus", return_value=(response, 2.0)):
            verdict = OpusJudgeAdapter().judge(self.request, self.extraction)
        summary = json.loads(verdict.summary)
        self.assertEqual(summary["status"], "JUDGE_ERROR")
        self.assertIn(expected_fragment, summary["judge_error"])
        self.assertEqual(len(verdict.comparisons), 28)
        self.assertTrue(all(item.outcome is ComparisonOutcome.ABSENT_IN_PDF
                            and item.rationale == summary["judge_error"]
                            for item in verdict.comparisons))

    def test_refusal_is_unscored_judge_error(self):
        self.assert_judge_error({
            "id": "msg_refusal_synthetic", "content": [{"type": "refusal", "refusal": "synthetic"}],
            "stop_reason": "end_turn",
        }, "JSONDecodeError")

    def test_truncation_is_unscored_judge_error(self):
        self.assert_judge_error({
            "id": "response_truncated_synthetic",
            "choices": [{"message": {"content": GROUND_TRUTH[:10]}, "finish_reason": "length"}],
        }, "truncated (length)")

    def test_non_json_is_unscored_judge_error(self):
        self.assert_judge_error({
            "id": "msg_non_json_synthetic", "content": [{"type": "text", "text": "synthetic prose"}],
            "stop_reason": "end_turn",
        }, "JSONDecodeError")

    def test_native_response_id_is_preserved(self):
        with patch("harness.judge_adapter.invoke_opus", return_value=(ANTHROPIC_RESPONSE, 2.0)):
            verdict = OpusJudgeAdapter().judge(self.request, self.extraction)
        self.assertEqual(verdict.raw_response_id, "msg_synthetic")
        self.assertEqual(json.loads(verdict.summary)["status"], "OK")

    def test_openai_choice_response_id_fallback_is_preserved(self):
        response = dict(OPENAI_NORMALIZED_RESPONSE)
        response.pop("id")
        with patch("harness.judge_adapter.invoke_opus", return_value=(response, 2.0)):
            verdict = OpusJudgeAdapter().judge(self.request, self.extraction)
        self.assertEqual(verdict.raw_response_id, "choice_synthetic")


class ParseGroundTruthShapeTest(unittest.TestCase):
    """Verifies the Opus ground-truth parser accepts all 28 judged fields and
    rejects keys outside the roster.

    Before the fix in this change, ``parse_ground_truth`` allow-listed only the
    original 7 fields (cardDisplayName/lastFourDigit + two rewards + three
    transaction leaves), so an Opus response carrying any of the 21 fields
    added in PR #47 was rejected as a JUDGE_ERROR — and, with the old prompt
    that never emitted them, the new fields came back permanently
    ABSENT_IN_PDF with ``expected=None``.  These tests pin the 28-field shape.
    """

    # A full 28-field ground-truth object mirroring judge/prompt_v1.txt's
    # output shape (23 scalar leaves across cards/statementMeta/
    # statementLevelSummary/rewards + 5 per-transaction leaves).
    FULL = {
        "cards": [{"cardMeta": {"cardDisplayName": "Synthetic Card",
                               "lastFourDigit": "1234",
                               "productFamily": "Platinum",
                               "network": "VISA"},
                   "bigPicture": {"cardCreditLimit": 100000,
                                  "cardAvailableCreditLimit": 95000}}],
        "statementMeta": {"issuerName": "Synthetic Bank",
                          "statementDate": "2026-01-01",
                          "dueDate": "2026-02-01",
                          "statementPeriodStart": "2025-12-01",
                          "statementPeriodEnd": "2026-01-01"},
        "statementLevelSummary": {"totalAmountDue": 5000,
                                  "totalMinimumAmountDue": 500,
                                  "totalCreditLimit": 100000,
                                  "availableCreditLimit": 95000},
        "rewards": {"programType": "Reward Points",
                    "openingPoints": 1000,
                    "pointsEarnedThisCycle": 100,
                    "pointsRedeemedThisCycle": 50,
                    "closingPoints": 1050,
                    "pointsExpiringNext30Days": 20,
                    "pointsExpiringNext60Days": 40,
                    "bonusPointsThisCycle": 10},
        "transactions": [{"date": "2026-01-05", "description": "Synthetic Shop",
                          "amount": 1234.56, "direction": "DEBIT",
                          "rewardPointsOnThisTransaction": 12}],
    }

    def test_accepts_full_28_field_ground_truth(self):
        # Every one of the 28 judged leaves is accepted — no exception.
        parse_ground_truth(json.dumps(self.FULL))

    def test_legacy_seven_field_ground_truth_still_parses(self):
        """The original 7-field shape (no statementMeta/statementLevelSummary,
        no bigPicture, none of the 21 new leaves) is still a valid subset —
        backward compatible with the existing response fixtures."""
        parse_ground_truth(GROUND_TRUTH)

    def test_empty_containers_parse(self):
        parse_ground_truth('{"cards": [], "rewards": {}, "transactions": []}')

    def test_top_level_objects_may_be_omitted(self):
        # A PDF that prints only rewards need not carry the other containers.
        parse_ground_truth('{"rewards": {"closingPoints": 10}}')

    def test_strips_markdown_fences(self):
        parse_ground_truth("```json\n" + json.dumps(self.FULL) + "\n```")

    def test_rejects_non_object_root(self):
        with self.assertRaises(ValueError):
            parse_ground_truth(json.dumps([1, 2, 3]))

    def test_rejects_unknown_top_level_key(self):
        gt = {**self.FULL, "unknownTop": 1}
        with self.assertRaises(ValueError):
            parse_ground_truth(json.dumps(gt))

    def test_rejects_unknown_card_container_key(self):
        gt = {"cards": [{"cardMeta": {}, "unknownCardKey": 1}]}
        with self.assertRaises(ValueError):
            parse_ground_truth(json.dumps(gt))

    def test_rejects_unknown_cardmeta_key(self):
        gt = {"cards": [{"cardMeta": {"cardDisplayName": "x", "unknownMeta": 1}}]}
        with self.assertRaises(ValueError):
            parse_ground_truth(json.dumps(gt))

    def test_rejects_unknown_bigpicture_key(self):
        gt = {"cards": [{"bigPicture": {"cardCreditLimit": 1, "unknownBig": 1}}]}
        with self.assertRaises(ValueError):
            parse_ground_truth(json.dumps(gt))

    def test_rejects_unknown_statementmeta_key(self):
        gt = {"statementMeta": {"issuerName": "x", "unknownMeta": 1}}
        with self.assertRaises(ValueError):
            parse_ground_truth(json.dumps(gt))

    def test_rejects_unknown_statement_summary_key(self):
        gt = {"statementLevelSummary": {"totalAmountDue": 1, "unknownSummary": 1}}
        with self.assertRaises(ValueError):
            parse_ground_truth(json.dumps(gt))

    def test_rejects_unknown_reward_key(self):
        gt = {"rewards": {"pointsEarnedThisCycle": 1, "unknownReward": 1}}
        with self.assertRaises(ValueError):
            parse_ground_truth(json.dumps(gt))

    def test_rejects_unknown_transaction_key(self):
        gt = {"transactions": [{"date": "x", "unknownTxn": 1}]}
        with self.assertRaises(ValueError):
            parse_ground_truth(json.dumps(gt))


if __name__ == "__main__":
    unittest.main()
