import json
import unittest
from unittest.mock import patch

from contracts.models import Bank, ComparisonOutcome, ExtractionResult, ParseRequest
from harness.judge_adapter import OpusJudgeAdapter
from judge.opus import completion_reason, extract_response_text

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


if __name__ == "__main__":
    unittest.main()
