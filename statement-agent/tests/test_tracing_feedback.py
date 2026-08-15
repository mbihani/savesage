"""Stdlib unit tests for field-wise feedback PII redaction (WS4, requirement 3+7).

No mlflow import — exercises harness/tracing_feedback.py. Verifies canonical
field paths (contracts.paths) are carried verbatim and PII values are redacted
per the tiered policy (cardholder names + descriptions hashed; amounts/dates/
last4 kept raw).
"""

from datetime import UTC, datetime
import unittest

from contracts.models import FieldFeedback
from harness.tracing_feedback import build_feedback_payload, redact_feedback_value


class FeedbackRedactionTest(unittest.TestCase):
    def test_cardholder_name_is_hashed(self):
        v = redact_feedback_value("cards.0.cardMeta.cardDisplayName", "Jane Doe")
        self.assertTrue(str(v).startswith("sha256:"))
        self.assertNotIn("Jane", str(v))

    def test_transaction_description_is_hashed(self):
        v = redact_feedback_value("transactions.3.description", "ACME CORP PAYMENT")
        self.assertTrue(str(v).startswith("sha256:"))
        self.assertNotIn("ACME", str(v))

    def test_lastFour_kept_raw(self):
        v = redact_feedback_value("cards.0.cardMeta.lastFourDigit", "1234")
        self.assertEqual(v, "1234")

    def test_amount_kept_raw(self):
        v = redact_feedback_value("transactions.0.amount", 1499.50)
        self.assertEqual(v, 1499.50)

    def test_date_kept_raw(self):
        v = redact_feedback_value("transactions.0.date", "01/01/2026")
        self.assertEqual(v, "01/01/2026")

    def test_rewards_kept_raw(self):
        v = redact_feedback_value("rewards.closingPoints", 500)
        self.assertEqual(v, 500)

    def test_redact_disabled_keeps_pii(self):
        v = redact_feedback_value("cards.0.cardMeta.cardDisplayName", "Jane Doe", redact_pii=False)
        self.assertEqual(v, "Jane Doe")

    def test_nonpii_disabled_drops_kept_values(self):
        v = redact_feedback_value("transactions.0.amount", 100.0, log_nonpii=False)
        self.assertIsNone(v)


class FeedbackPayloadTest(unittest.TestCase):
    def _fb(self, path, original, corrected, accepted):
        return FieldFeedback(
            request_id="req-1", field_path=path,
            original_value=original, corrected_value=corrected,
            accepted=accepted, actor="synthetic-client",
            timestamp=datetime.now(UTC),
        )

    def test_payload_carries_canonical_path_verbatim(self):
        fb = self._fb("cards.0.cardMeta.cardDisplayName", "Jane Doe", "Jane D", False)
        payload = build_feedback_payload(fb)
        self.assertEqual(payload.metadata["field_path"], "cards.0.cardMeta.cardDisplayName")
        self.assertFalse(payload.metadata["accepted"])
        self.assertEqual(payload.metadata["disposition"], "CORRECT")
        self.assertEqual(payload.value, False)
        self.assertEqual(payload.source_id, "synthetic-client")
        self.assertEqual(payload.source_type, "HUMAN")

    def test_accept_payload(self):
        fb = self._fb("transactions.5.amount", 100.0, 100.0, True)
        payload = build_feedback_payload(fb)
        self.assertTrue(payload.metadata["accepted"])
        self.assertEqual(payload.metadata["disposition"], "ACCEPT")
        self.assertEqual(payload.value, True)
        # amount is non-PII -> kept raw
        self.assertEqual(payload.metadata["original_value"], 100.0)
        self.assertEqual(payload.metadata["corrected_value"], 100.0)

    def test_pii_values_hashed_in_payload(self):
        fb = self._fb("transactions.2.description", "ACME PAY", "ACME PAYMENT", False)
        payload = build_feedback_payload(fb)
        self.assertTrue(str(payload.metadata["original_value"]).startswith("sha256:"))
        self.assertTrue(str(payload.metadata["corrected_value"]).startswith("sha256:"))
        self.assertNotIn("ACME", str(payload.metadata["original_value"]))

    def test_metadata_records_redacted_flag(self):
        fb = self._fb("cards.0.cardMeta.lastFourDigit", "1234", "4321", False)
        payload = build_feedback_payload(fb, redact_pii=True)
        self.assertTrue(payload.metadata["redacted"])


if __name__ == "__main__":
    unittest.main()
