"""Stdlib unit tests for field-wise feedback PII redaction (WS4, requirement 3+7; review B4).

No mlflow import — exercises harness/tracing_feedback.py. Verifies canonical
field paths (contracts.paths) are carried verbatim, PII values use keyed HMAC
(not unsalted reversible hash), actor is pseudonymised, and amounts/dates/last4
are kept raw.
"""

from datetime import UTC, datetime
import unittest

from contracts.models import FieldFeedback
from harness.tracing_feedback import build_feedback_payload, pseudonymise_actor, redact_feedback_value

_KEY = b"deploy-secret-test-key"


class FeedbackRedactionTest(unittest.TestCase):
    def test_cardholder_name_is_hmac_with_key(self):
        v = redact_feedback_value("cards.0.cardMeta.cardDisplayName", "Jane Doe", hmac_key=_KEY)
        self.assertTrue(str(v).startswith("hmac:"))
        self.assertNotIn("Jane", str(v))

    def test_cardholder_name_omitted_without_key(self):
        # No HMAC key -> OMIT rather than send a reversible unsalted digest.
        v = redact_feedback_value("cards.0.cardMeta.cardDisplayName", "Jane Doe")
        self.assertIsNone(v)

    def test_transaction_description_is_hmac_with_key(self):
        v = redact_feedback_value("transactions.3.description", "ACME CORP PAYMENT", hmac_key=_KEY)
        self.assertTrue(str(v).startswith("hmac:"))
        self.assertNotIn("ACME", str(v))

    def test_transaction_description_omitted_without_key(self):
        v = redact_feedback_value("transactions.3.description", "UPI-Amazon")
        self.assertIsNone(v)

    def test_hmac_is_deterministic_and_keyed(self):
        v1 = redact_feedback_value("cards.0.cardMeta.cardDisplayName", "Jane", hmac_key=_KEY)
        v2 = redact_feedback_value("cards.0.cardMeta.cardDisplayName", "Jane", hmac_key=_KEY)
        self.assertEqual(v1, v2)  # deterministic for same key+value
        v3 = redact_feedback_value("cards.0.cardMeta.cardDisplayName", "Jane", hmac_key=b"different-key")
        self.assertNotEqual(v1, v3)  # different key -> different pseudonym

    def test_lastFour_kept_raw(self):
        v = redact_feedback_value("cards.0.cardMeta.lastFourDigit", "1234", hmac_key=_KEY)
        self.assertEqual(v, "1234")

    def test_amount_kept_raw(self):
        v = redact_feedback_value("transactions.0.amount", 1499.50, hmac_key=_KEY)
        self.assertEqual(v, 1499.50)

    def test_date_kept_raw(self):
        v = redact_feedback_value("transactions.0.date", "01/01/2026", hmac_key=_KEY)
        self.assertEqual(v, "01/01/2026")

    def test_rewards_kept_raw(self):
        v = redact_feedback_value("rewards.closingPoints", 500, hmac_key=_KEY)
        self.assertEqual(v, 500)

    def test_redact_disabled_keeps_pii(self):
        v = redact_feedback_value("cards.0.cardMeta.cardDisplayName", "Jane Doe", redact_pii=False)
        self.assertEqual(v, "Jane Doe")

    def test_nonpii_disabled_drops_kept_values(self):
        v = redact_feedback_value("transactions.0.amount", 100.0, log_nonpii=False)
        self.assertIsNone(v)


class ActorPseudonymTest(unittest.TestCase):
    def test_actor_pseudonymised_with_key(self):
        p = pseudonymise_actor("client-user@example.com", _KEY)
        self.assertTrue(str(p).startswith("hmac:"))
        self.assertNotIn("client-user", str(p))

    def test_actor_redacted_without_key(self):
        # No key -> "redacted" (no raw actor ever sent).
        p = pseudonymise_actor("client-user@example.com", b"")
        self.assertEqual(p, "redacted")

    def test_actor_pseudonym_stable(self):
        p1 = pseudonymise_actor("alice", _KEY)
        p2 = pseudonymise_actor("alice", _KEY)
        self.assertEqual(p1, p2)
        p3 = pseudonymise_actor("bob", _KEY)
        self.assertNotEqual(p1, p3)


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
        payload = build_feedback_payload(fb, hmac_key=_KEY)
        self.assertEqual(payload.metadata["field_path"], "cards.0.cardMeta.cardDisplayName")
        self.assertFalse(payload.metadata["accepted"])
        self.assertEqual(payload.metadata["disposition"], "CORRECT")
        self.assertEqual(payload.value, False)
        # Actor is pseudonymised, never raw.
        self.assertTrue(str(payload.source_id).startswith("hmac:"))
        self.assertNotIn("synthetic-client", str(payload.source_id))
        self.assertEqual(payload.source_type, "HUMAN")

    def test_accept_payload(self):
        fb = self._fb("transactions.5.amount", 100.0, 100.0, True)
        payload = build_feedback_payload(fb, hmac_key=_KEY)
        self.assertTrue(payload.metadata["accepted"])
        self.assertEqual(payload.metadata["disposition"], "ACCEPT")
        self.assertEqual(payload.value, True)
        # amount is non-PII -> kept raw
        self.assertEqual(payload.metadata["original_value"], 100.0)
        self.assertEqual(payload.metadata["corrected_value"], 100.0)

    def test_pii_values_hmac_in_payload(self):
        fb = self._fb("transactions.2.description", "ACME PAY", "ACME PAYMENT", False)
        payload = build_feedback_payload(fb, hmac_key=_KEY)
        self.assertTrue(str(payload.metadata["original_value"]).startswith("hmac:"))
        self.assertTrue(str(payload.metadata["corrected_value"]).startswith("hmac:"))
        self.assertNotIn("ACME", str(payload.metadata["original_value"]))

    def test_pii_values_omitted_without_key(self):
        fb = self._fb("transactions.2.description", "ACME PAY", "ACME PAYMENT", False)
        payload = build_feedback_payload(fb)  # no key
        self.assertIsNone(payload.metadata["original_value"])
        self.assertIsNone(payload.metadata["corrected_value"])
        self.assertEqual(payload.metadata["actor"], "redacted")

    def test_metadata_records_redacted_flag(self):
        fb = self._fb("cards.0.cardMeta.lastFourDigit", "1234", "4321", False)
        payload = build_feedback_payload(fb, redact_pii=True, hmac_key=_KEY)
        self.assertTrue(payload.metadata["redacted"])

    def test_raw_actor_never_in_payload(self):
        fb = self._fb("cards.0.cardMeta.cardDisplayName", "Jane", "Jane D", False)
        payload = build_feedback_payload(fb, hmac_key=_KEY)
        # Raw actor string must not appear anywhere in the payload.
        import json
        serialized = json.dumps(payload.metadata, default=str) + payload.rationale + payload.source_id
        self.assertNotIn("synthetic-client", serialized)


if __name__ == "__main__":
    unittest.main()
