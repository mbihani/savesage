"""Extraction adapter tests: response mapping + HTTP retry (stdlib, no network).

``map_response`` and ``parse_json_strict`` are pure functions tested directly.
The HTTP path is tested with a fake ``urlopen`` that returns canned responses, so
no network access is needed and no real Luna call is made.
"""

import io
import json
import unittest
from unittest.mock import MagicMock

from contracts.models import Bank, ParseRequest, TokenUsage
from harness.extraction_adapter import (
    ExtractionError,
    LunaExtractionAdapter,
    _extract_text,
    map_response,
    parse_json_strict,
)
from harness.policy import RetryPolicy


def _req() -> ParseRequest:
    return ParseRequest(
        pdf=b"%PDF-1.4 synthetic", filename="synthetic.pdf", bank=Bank.HDFC, request_id="r1"
    )


_LUNA_RESPONSE = {
    "id": "resp-synthetic-123",
    "model": "databricks-gpt-5-6-luna",
    "choices": [{
        "finish_reason": "stop",
        "message": {"content": json.dumps({
            "statementMeta": {"issuerName": "S", "statementDate": "01/04/2026",
                              "dueDate": "20/04/2026", "statementPeriodStart": "01/03/2026",
                              "statementPeriodEnd": "31/03/2026", "rawStatementId": "x"},
            "statementLevelSummary": {"totalAmountDue": 1.0, "totalMinimumAmountDue": 1.0,
                                       "totalCreditLimit": 100.0, "availableCreditLimit": 99.0},
            "cards": [{"cardMeta": {"cardDisplayName": "S", "productFamily": "S",
                                     "lastFourDigit": "0000", "network": "VISA",
                                     "isPrimaryCard": True},
                       "bigPicture": {"cardCreditLimit": 100.0, "cardAvailableCreditLimit": 99.0}}],
            "transactions": [],
            "rewards": {"programType": "S", "openingPoints": 0, "pointsEarnedThisCycle": 0,
                        "pointsRedeemedThisCycle": 0, "closingPoints": 0,
                        "pointsExpiringNext30Days": 0, "pointsExpiringNext60Days": 0,
                        "bonusPointsThisCycle": 0},
        })},
    }],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
}


class ExtractTextTest(unittest.TestCase):
    def test_string_content(self) -> None:
        resp = {"choices": [{"message": {"content": "hello"}}]}
        self.assertEqual(_extract_text(resp), "hello")

    def test_list_content_joins_text_blocks(self) -> None:
        resp = {"choices": [{"message": {"content": [
            {"type": "text", "text": "a"}, {"type": "text", "text": "b"},
        ]}}]}
        self.assertEqual(_extract_text(resp), "ab")

    def test_list_content_ignores_non_text(self) -> None:
        resp = {"choices": [{"message": {"content": [
            {"type": "reasoning", "text": "ignore"}, {"type": "text", "text": "keep"},
        ]}}]}
        self.assertEqual(_extract_text(resp), "keep")

    def test_empty_choices(self) -> None:
        self.assertEqual(_extract_text({"choices": []}), "")

    def test_no_choices_key(self) -> None:
        self.assertEqual(_extract_text({}), "")


class ParseJsonStrictTest(unittest.TestCase):
    def test_plain_json(self) -> None:
        self.assertEqual(parse_json_strict('{"a": 1}'), {"a": 1})

    def test_fenced_json(self) -> None:
        self.assertEqual(parse_json_strict('```json\n{"a": 1}\n```'), {"a": 1})

    def test_fenced_no_lang(self) -> None:
        self.assertEqual(parse_json_strict('```\n{"a": 1}\n```'), {"a": 1})

    def test_json_with_leading_prose(self) -> None:
        # Falls back to extracting the outermost braces.
        self.assertEqual(parse_json_strict('here is the answer: {"a": 1} done'), {"a": 1})

    def test_invalid_raises(self) -> None:
        with self.assertRaises(json.JSONDecodeError):
            parse_json_strict("not json at all")


class MapResponseTest(unittest.TestCase):
    def test_maps_full_response(self) -> None:
        result = map_response(_LUNA_RESPONSE, _req(), latency_ms=123.0)
        self.assertEqual(result.request_id, "r1")
        self.assertEqual(result.model_id, "databricks-gpt-5-6-luna")
        self.assertEqual(result.latency_ms, 123.0)
        self.assertEqual(result.raw_response_id, "resp-synthetic-123")
        self.assertEqual(result.token_usage, TokenUsage(10, 5, 15))
        self.assertFalse(result.schema_valid)  # validation node sets this, not the adapter
        self.assertEqual(result.payload["statementMeta"]["issuerName"], "S")

    def test_empty_content_raises(self) -> None:
        resp = {"choices": [{"message": {"content": ""}}]}
        with self.assertRaises(ExtractionError):
            map_response(resp, _req(), 1.0)

    def test_invalid_json_raises(self) -> None:
        resp = {"choices": [{"message": {"content": "not json"}}]}
        with self.assertRaises(ExtractionError):
            map_response(resp, _req(), 1.0)

    def test_missing_usage_returns_empty_token_usage(self) -> None:
        resp = {"id": "x", "model": "m", "choices": [{"message": {"content": "{}"}}]}
        result = map_response(resp, _req(), 1.0)
        self.assertEqual(result.token_usage, TokenUsage())

    def test_missing_id_leaves_none(self) -> None:
        resp = {"model": "m", "choices": [{"message": {"content": "{}"}}]}
        result = map_response(resp, _req(), 1.0)
        self.assertIsNone(result.raw_response_id)


def _fake_urlopen(payload_resp, status=200):
    """Build a fake urlopen callable returning `payload_resp` with `status`."""
    class _Ctx:
        def __init__(self, body, code):
            self._body = body
            self._code = code
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return self._body
        @property
        def status(self): return self._code
    body = json.dumps(payload_resp).encode()
    return MagicMock(return_value=_Ctx(body, status))


class LunaAdapterHttpTest(unittest.TestCase):
    def _adapter(self, urlopen, policy=None) -> LunaExtractionAdapter:
        return LunaExtractionAdapter(
            retry_policy=policy or RetryPolicy(max_attempts=2, initial_backoff_seconds=0.0),
            settings=_FakeSettings(),
            token_provider=lambda: "synthetic-token",
            urlopen=urlopen,
        )

    def test_successful_extract(self) -> None:
        adapter = self._adapter(_fake_urlopen(_LUNA_RESPONSE))
        result = adapter.extract(_req())
        self.assertEqual(result.request_id, "r1")
        self.assertEqual(result.model_id, "databricks-gpt-5-6-luna")
        self.assertEqual(result.raw_response_id, "resp-synthetic-123")
        self.assertEqual(result.token_usage.total_tokens, 15)

    def test_retryable_500_then_success(self) -> None:
        import urllib.error
        err = urllib.error.HTTPError("u", 500, "err", {}, io.BytesIO(b"server error"))
        ok = _fake_urlopen(_LUNA_RESPONSE)
        urlopen = MagicMock(side_effect=[err, ok.return_value])
        adapter = self._adapter(urlopen)
        result = adapter.extract(_req())
        self.assertEqual(result.raw_response_id, "resp-synthetic-123")
        self.assertEqual(urlopen.call_count, 2)

    def test_non_retryable_400_fails_immediately(self) -> None:
        import urllib.error
        err = urllib.error.HTTPError("u", 400, "bad", {}, io.BytesIO(b"bad request"))
        urlopen = MagicMock(side_effect=err)
        adapter = self._adapter(urlopen, RetryPolicy(max_attempts=4, initial_backoff_seconds=0.0))
        with self.assertRaises(ExtractionError):
            adapter.extract(_req())
        self.assertEqual(urlopen.call_count, 1)  # 400 is not in retry_statuses

    def test_exhausts_retries_then_raises(self) -> None:
        import urllib.error
        err = urllib.error.HTTPError("u", 503, "down", {}, io.BytesIO(b"down"))
        urlopen = MagicMock(side_effect=err)
        adapter = self._adapter(urlopen, RetryPolicy(max_attempts=3, initial_backoff_seconds=0.0))
        with self.assertRaises(ExtractionError):
            adapter.extract(_req())
        self.assertEqual(urlopen.call_count, 3)

    def test_timeout_retries_then_raises(self) -> None:
        urlopen = MagicMock(side_effect=TimeoutError("timed out"))
        adapter = self._adapter(urlopen, RetryPolicy(max_attempts=2, initial_backoff_seconds=0.0))
        with self.assertRaises(ExtractionError):
            adapter.extract(_req())
        self.assertEqual(urlopen.call_count, 2)

    def test_token_provider_called_per_request(self) -> None:
        # A fresh token is acquired for each extract() call (tokens expire ~1h).
        calls = []
        def provider():
            calls.append(len(calls))
            return f"tok-{len(calls)}"
        adapter = LunaExtractionAdapter(
            retry_policy=RetryPolicy(max_attempts=1),
            settings=_FakeSettings(),
            token_provider=provider,
            urlopen=_fake_urlopen(_LUNA_RESPONSE),
        )
        adapter.extract(_req())
        adapter.extract(ParseRequest(b"x", "x.pdf", Bank.SBI, "r2"))
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0], calls[1])


class _FakeSettings:
    """Minimal settings stub matching config.Settings' interface."""
    extraction_endpoint = "databricks-gpt-5-6-luna"
    request_timeout_seconds = 1.0

    def endpoint_url(self, endpoint):
        return f"https://synthetic-host/serving-endpoints/{endpoint}/invocations"


if __name__ == "__main__":
    unittest.main()
