"""Tests for the genai.evaluate-based judge scorer (judge/evaluator.py).

Two layers:

* ``BuildFieldFeedbacksTest`` — pure-logic unit tests for
  :func:`build_field_feedbacks` (the verdict → 7 per-field Feedback objects
  builder).  Uses the real ``mlflow.entities.Feedback`` (importable locally)
  but no MLflow tracking store.  Verifies the SEVEN per-field assessment
  names, their values, PII redaction (cardDisplayName/description HMAC'd or
  omitted, rationale dropped), and the two overall assessments.

* ``RunGenaiEvaluationRealTest`` — end-to-end against a REAL local mlflow
  file store (temp dir), verifying that ``mlflow.genai.evaluate`` drives the
  scorer once per trace, logs per-field assessments to the original parse
  trace, calls Opus exactly once per trace, and persists the verdict to
  Lakebase.  Skipped if ``mlflow``/``pandas`` are not importable.
"""

import json
import os
import shutil
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

from contracts.models import (
    ComparisonOutcome,
    FieldComparison,
    FieldScope,
    JudgeVerdict,
    MatchMethod,
)

from judge.scorer import JUDGED_FIELDS
from harness.tracing_judge import verdict_to_metrics


# ---------------------------------------------------------------------------
# Sample verdicts (all 7 judged fields, including PII + transaction rows)
# ---------------------------------------------------------------------------

def _full_verdict(request_id: str = "req-test") -> JudgeVerdict:
    """A verdict carrying ALL 7 judged fields, including PII fields
    (cardDisplayName, transaction description) and transaction-row fields."""
    return JudgeVerdict(
        request_id=request_id,
        judge_model_id="databricks-claude-opus-5",
        comparisons=(
            FieldComparison(
                "cards[].cardMeta.cardDisplayName", "Platinum Card",
                "Platinum Card", ComparisonOutcome.AGREE,
                FieldScope.SCALAR, card_index=0,
            ),
            FieldComparison(
                "cards[].cardMeta.lastFourDigit", "1234", "1234",
                ComparisonOutcome.AGREE, FieldScope.SCALAR, card_index=0,
            ),
            FieldComparison(
                "rewards.pointsEarnedThisCycle", 100, 100,
                ComparisonOutcome.AGREE, FieldScope.SCALAR,
            ),
            FieldComparison(
                "rewards.closingPoints", 500, 500,
                ComparisonOutcome.AGREE, FieldScope.SCALAR,
            ),
            FieldComparison(
                "transactions[].date", "2026-01-01", "2026-01-01",
                ComparisonOutcome.AGREE, FieldScope.TRANSACTION_ROW,
                MatchMethod.DESCRIPTION_SIMILARITY_1TO1,
                expected_row_index=0, actual_row_index=0,
            ),
            FieldComparison(
                "transactions[].description", "UPI-Amazon Pay",
                "UPI-Amazon Pay", ComparisonOutcome.AGREE,
                FieldScope.TRANSACTION_ROW,
                MatchMethod.DESCRIPTION_SIMILARITY_1TO1,
                expected_row_index=0, actual_row_index=0, similarity=1.0,
            ),
            FieldComparison(
                "transactions[].amount", 150.0, 150.0,
                ComparisonOutcome.AGREE, FieldScope.TRANSACTION_ROW,
                MatchMethod.DESCRIPTION_SIMILARITY_1TO1,
                expected_row_index=0, actual_row_index=0,
            ),
        ),
        latency_ms=50.0,
        summary=json.dumps({"status": "OK"}),
    )


def _comparisons(feedback: Any) -> list[dict[str, Any]]:
    """Decode the JSON-string ``comparisons`` metadata a per-field Feedback
    carries back into a list of dicts (the Databricks-flat-metadata form)."""
    from judge.evaluator import COMPARISONS_METADATA_KEY

    return json.loads(feedback.metadata[COMPARISONS_METADATA_KEY])


# ---------------------------------------------------------------------------
# Pure-logic tests for build_field_feedbacks
# ---------------------------------------------------------------------------

class BuildFieldFeedbacksTest(unittest.TestCase):
    """Verifies the verdict → 7 per-field Feedback + 2 overall builder."""

    def setUp(self):
        self.verdict = _full_verdict()
        self.metrics = verdict_to_metrics(self.verdict)

    def test_returns_exactly_7_per_field_plus_2_overall(self):
        """The builder returns exactly 9 Feedback objects: 7 per-field + 2 overall."""
        from judge.evaluator import build_field_feedbacks

        feedbacks = build_field_feedbacks(self.verdict, self.metrics)
        self.assertEqual(len(feedbacks), 9)

    def test_seven_per_field_names_match_expected(self):
        """Each of the 7 per-field Feedbacks has the correct assessment name."""
        from judge.evaluator import FIELD_ASSESSMENT_NAMES, build_field_feedbacks

        feedbacks = build_field_feedbacks(self.verdict, self.metrics)
        per_field = feedbacks[:7]
        actual_names = {f.name for f in per_field}
        expected_names = set(FIELD_ASSESSMENT_NAMES.values())
        self.assertEqual(actual_names, expected_names)
        # Each name is distinct so each field is its own row in the tab.
        self.assertEqual(len(actual_names), 7)

    def test_per_field_values_are_accuracies(self):
        """Each per-field Feedback value equals the per-field strict accuracy."""
        from judge.evaluator import FIELD_ASSESSMENT_NAMES, build_field_feedbacks

        feedbacks = build_field_feedbacks(self.verdict, self.metrics)
        by_name = {f.name: f for f in feedbacks[:7]}
        for field_path, name in FIELD_ASSESSMENT_NAMES.items():
            field_key = field_path.replace("[]", "").replace(".", "_")
            expected_acc = self.metrics[f"judge.{field_key}"]
            self.assertEqual(by_name[name].value, expected_acc,
                             f"value mismatch for {name}")

    def test_overall_strict_and_forgiven_values(self):
        """The 2 overall Feedbacks carry the aggregate strict + forgiven accuracy."""
        from judge.evaluator import (
            OVERALL_FORGIVEN_NAME,
            OVERALL_STRICT_NAME,
            build_field_feedbacks,
        )

        feedbacks = build_field_feedbacks(self.verdict, self.metrics)
        overall = {f.name: f for f in feedbacks[7:]}
        self.assertIn(OVERALL_STRICT_NAME, overall)
        self.assertIn(OVERALL_FORGIVEN_NAME, overall)
        self.assertEqual(overall[OVERALL_STRICT_NAME].value,
                         self.metrics["judge.accuracy"])
        self.assertEqual(overall[OVERALL_FORGIVEN_NAME].value,
                         self.metrics["judge.accuracy_forgiven"])

    def test_rationale_is_none_for_all_feedbacks(self):
        """The free-text rationale is DROPPED (None) on every Feedback —
        it is Opus free-text that may echo cardholder names / transaction
        descriptions from the PDF (the one remaining PII vector)."""
        from judge.evaluator import build_field_feedbacks

        feedbacks = build_field_feedbacks(self.verdict, self.metrics)
        for f in feedbacks:
            self.assertIsNone(f.rationale)

    def test_pii_card_display_name_omitted_without_hmac_key(self):
        """Without an HMAC key (the default), cardDisplayName expected/actual
        are OMITTED (None) in the per-field Feedback metadata — never the
        cleartext 'Platinum Card'."""
        from judge.evaluator import build_field_feedbacks

        feedbacks = build_field_feedbacks(self.verdict, self.metrics)
        card_fb = next(f for f in feedbacks if f.name == "judge_cardDisplayName")
        comps = _comparisons(card_fb)
        self.assertEqual(len(comps), 1)
        # PII field omitted (None) — NOT the cleartext value.
        self.assertIsNone(comps[0]["expected"])
        self.assertIsNone(comps[0]["actual"])
        self.assertNotIn("Platinum", json.dumps(comps))

    def test_pii_description_omitted_without_hmac_key(self):
        """Without an HMAC key, transaction description expected/actual are
        OMITTED (None) — never the cleartext 'UPI-Amazon Pay'."""
        from judge.evaluator import build_field_feedbacks

        feedbacks = build_field_feedbacks(self.verdict, self.metrics)
        desc_fb = next(f for f in feedbacks if f.name == "judge_transactions_description")
        comps = _comparisons(desc_fb)
        self.assertEqual(len(comps), 1)
        self.assertIsNone(comps[0]["expected"])
        self.assertIsNone(comps[0]["actual"])
        self.assertNotIn("Amazon", json.dumps(comps))

    def test_non_pii_fields_retained_raw(self):
        """Non-PII fields (lastFourDigit, amount, date, points) are retained
        raw in the per-field Feedback metadata — documented trade-off (not
        individually identifying; hashing destroys analytics value)."""
        from judge.evaluator import build_field_feedbacks

        feedbacks = build_field_feedbacks(self.verdict, self.metrics)
        by_name = {f.name: f for f in feedbacks[:7]}

        # lastFourDigit retained raw.
        last4_comps = _comparisons(by_name["judge_lastFourDigit"])
        self.assertEqual(last4_comps[0]["expected"], "1234")

        # amount retained raw.
        amt_comps = _comparisons(by_name["judge_transactions_amount"])
        self.assertEqual(amt_comps[0]["expected"], 150.0)

        # date retained raw.
        date_comps = _comparisons(by_name["judge_transactions_date"])
        self.assertEqual(date_comps[0]["expected"], "2026-01-01")

        # points retained raw.
        pts_comps = _comparisons(by_name["judge_pointsEarnedThisCycle"])
        self.assertEqual(pts_comps[0]["expected"], 100)

    def test_pii_fields_hmac_with_key_configured(self):
        """When an HMAC key IS configured, PII fields become keyed HMAC
        (not None, not cleartext) — consistent with the redaction policy."""
        from judge.evaluator import build_field_feedbacks

        with patch("judge.scorer._resolve_feedback_hmac_key",
                   return_value=b"test-hmac-key"):
            feedbacks = build_field_feedbacks(self.verdict, self.metrics)

        card_fb = next(f for f in feedbacks if f.name == "judge_cardDisplayName")
        comps = _comparisons(card_fb)
        # HMAC'd — a non-empty string, NOT the cleartext, NOT None.
        self.assertIsNotNone(comps[0]["expected"])
        self.assertNotEqual(comps[0]["expected"], "Platinum Card")
        self.assertNotIn("Platinum", str(comps[0]["expected"]))

    def test_metadata_carries_field_path_and_comparison_count(self):
        """Each per-field Feedback metadata carries the field_path and the
        count of comparisons for that field."""
        from judge.evaluator import build_field_feedbacks

        feedbacks = build_field_feedbacks(self.verdict, self.metrics)
        card_fb = next(f for f in feedbacks if f.name == "judge_cardDisplayName")
        self.assertEqual(card_fb.metadata["field_path"],
                         "cards[].cardMeta.cardDisplayName")
        self.assertEqual(card_fb.metadata["n_comparisons"], "1")

    def test_source_is_llm_judge_with_model_id(self):
        """Each Feedback source is AssessmentSource(LLM_JUDGE, judge_model_id)."""
        from judge.evaluator import build_field_feedbacks
        from harness.tracing_keys import ASSESSMENT_LLM_JUDGE

        feedbacks = build_field_feedbacks(self.verdict, self.metrics)
        for f in feedbacks:
            self.assertEqual(f.source.source_type, ASSESSMENT_LLM_JUDGE)
            self.assertEqual(f.source.source_id, "databricks-claude-opus-5")

    def test_missing_field_produces_empty_comparisons(self):
        """A field with no comparisons (e.g. transactions absent) still
        produces a Feedback with value=None and empty comparisons list —
        7 assessments per trace regardless of which fields are present."""
        from judge.evaluator import build_field_feedbacks

        # A verdict with only scalar fields (no transactions).
        verdict = JudgeVerdict(
            request_id="req-test",
            judge_model_id="databricks-claude-opus-5",
            comparisons=(
                FieldComparison(
                    "cards[].cardMeta.cardDisplayName", "Platinum", "Platinum",
                    ComparisonOutcome.AGREE, FieldScope.SCALAR, card_index=0,
                ),
            ),
            latency_ms=50.0,
            summary=json.dumps({"status": "OK"}),
        )
        metrics = verdict_to_metrics(verdict)
        feedbacks = build_field_feedbacks(verdict, metrics)
        # Still 9 Feedbacks (7 per-field + 2 overall).
        self.assertEqual(len(feedbacks), 9)
        # The transaction fields have empty comparisons + "not_scored" value
        # (Feedback rejects None; the sentinel preserves 7 rows per trace
        # while genai.evaluate's aggregation skips it).
        txn_date = next(f for f in feedbacks if f.name == "judge_transactions_date")
        self.assertEqual(txn_date.value, "not_scored")
        self.assertEqual(txn_date.metadata["n_comparisons"], "0")
        # The comparisons metadata is a JSON string (Databricks-flat form);
        # an empty field serialises to "[]".
        self.assertEqual(_comparisons(txn_date), [])


def _mixed_outcome_verdict(request_id: str = "req-abc123def456") -> JudgeVerdict:
    """A verdict mirroring what judge/comparison.py produces on a REAL parse:
    every non-AGREE outcome (UNMATCHED_ROW, ABSENT_IN_PDF, DISAGREE) is
    present, including the None leaves those outcomes produce in practice.

    Specifically (see judge/comparison.py build_comparisons):

    * UNMATCHED_ROW for a PDF row with no extraction match sets
      ``actual=None``, ``actual_row_index=None``, ``similarity=None``.
    * UNMATCHED_ROW for an extraction row with no PDF match sets
      ``expected=None``, ``expected_row_index=None``, ``similarity=None``.
    * ABSENT_IN_PDF sets ``expected=None`` (PDF has no value for that field).
    * DISAGREE keeps both expected/actual non-null but unequal.

    Every judged field has at least one SCORED comparison (AGREE/UNMATCHED_ROW/
    DISAGREE — ABSENT_IN_PDF is excluded from the denominator), so every
    per-field accuracy is a non-None float (the requirement for the assessment
    to carry a value mlflow.genai.evaluate aggregates into a ``/mean`` metric).
    """
    return JudgeVerdict(
        request_id=request_id,
        judge_model_id="databricks-claude-opus-5",
        comparisons=(
            # cardDisplayName: AGREE (scored).
            FieldComparison(
                "cards[].cardMeta.cardDisplayName", "Platinum", "Platinum",
                ComparisonOutcome.AGREE, FieldScope.SCALAR, card_index=0,
            ),
            # lastFourDigit: DISAGREE (scored; both non-null, unequal).
            FieldComparison(
                "cards[].cardMeta.lastFourDigit", "1234", "5678",
                ComparisonOutcome.DISAGREE, FieldScope.SCALAR, MatchMethod.DIRECT,
                card_index=0,
            ),
            # pointsEarnedThisCycle: AGREE.
            FieldComparison(
                "rewards.pointsEarnedThisCycle", 100, 100,
                ComparisonOutcome.AGREE, FieldScope.SCALAR,
            ),
            # closingPoints: AGREE (scored — keeps closingPoints off "not_scored").
            FieldComparison(
                "rewards.closingPoints", 500, 500,
                ComparisonOutcome.AGREE, FieldScope.SCALAR,
            ),
            # transactions[].date: row0 AGREE (scored) +
            # row1 UNMATCHED_ROW (PDF row with no extraction match: actual=None,
            # actual_row_index=None, similarity=None).
            FieldComparison(
                "transactions[].date", "2026-01-01", "2026-01-01",
                ComparisonOutcome.AGREE, FieldScope.TRANSACTION_ROW,
                MatchMethod.DESCRIPTION_SIMILARITY_1TO1,
                expected_row_index=0, actual_row_index=0, similarity=1.0,
            ),
            FieldComparison(
                "transactions[].date", "2026-01-02", None,
                ComparisonOutcome.UNMATCHED_ROW, FieldScope.TRANSACTION_ROW,
                MatchMethod.DESCRIPTION_SIMILARITY_1TO1, expected_row_index=1,
            ),
            # transactions[].description: row0 AGREE +
            # row1 ABSENT_IN_PDF (matched row whose PDF description is null:
            # expected=None).
            FieldComparison(
                "transactions[].description", "UPI-Amazon Pay", "UPI-Amazon Pay",
                ComparisonOutcome.AGREE, FieldScope.TRANSACTION_ROW,
                MatchMethod.DESCRIPTION_SIMILARITY_1TO1,
                expected_row_index=0, actual_row_index=0, similarity=1.0,
            ),
            FieldComparison(
                "transactions[].description", None, "BigBazaar Groceries",
                ComparisonOutcome.ABSENT_IN_PDF, FieldScope.TRANSACTION_ROW,
                MatchMethod.DESCRIPTION_SIMILARITY_1TO1,
                expected_row_index=1, actual_row_index=1, similarity=0.9,
            ),
            # transactions[].amount: row0 AGREE +
            # row1 UNMATCHED_ROW (extraction row with no PDF match: expected=None,
            # expected_row_index=None, similarity=None).
            FieldComparison(
                "transactions[].amount", 150.0, 150.0,
                ComparisonOutcome.AGREE, FieldScope.TRANSACTION_ROW,
                MatchMethod.DESCRIPTION_SIMILARITY_1TO1,
                expected_row_index=0, actual_row_index=0, similarity=1.0,
            ),
            FieldComparison(
                "transactions[].amount", None, 99.0,
                ComparisonOutcome.UNMATCHED_ROW, FieldScope.TRANSACTION_ROW,
                MatchMethod.DESCRIPTION_SIMILARITY_1TO1, actual_row_index=3,
            ),
        ),
        latency_ms=50.0,
        summary=json.dumps({"status": "OK"}),
    )


class BuildFieldFeedbacksNoneLeafTest(unittest.TestCase):
    """Bug 1 — real verdicts carry UNMATCHED_ROW / ABSENT_IN_PDF / DISAGREE
    comparisons whose leaves (expected, actual, card_index,
    expected_row_index, actual_row_index, similarity) can be None.  The
    redaction / feedback-building path must NEVER raise on those, must still
    return one float-valued Feedback per field name (value in [0,1]) plus the
    two overall feedbacks, and must preserve the PII redaction PR #43 added
    (HMAC/omit cardDisplayName + description; drop rationale).

    These are the fixtures the scorer actually encounters in production:
    ``_full_verdict`` (all-AGREE) was the only one previously tested, so the
    None-leaf code paths in ``_redact_comparisons`` were never exercised.
    """

    def setUp(self):
        self.verdict = _mixed_outcome_verdict()
        self.metrics = verdict_to_metrics(self.verdict)

    def test_does_not_raise_on_mixed_outcomes_with_none_leaves(self):
        """REPRODUCTION: build_field_feedbacks must not raise on a verdict
        containing UNMATCHED_ROW / ABSENT_IN_PDF / DISAGREE comparisons with
        None leaves (actual=None, actual_row_index=None, similarity=None,
        expected=None).  This is the production verdict shape — the all-AGREE
        ``_full_verdict`` was the only one tested before."""
        from judge.evaluator import build_field_feedbacks

        # Must not raise — with and without an HMAC key (both redaction paths).
        build_field_feedbacks(self.verdict, self.metrics)
        with patch("judge.scorer._resolve_feedback_hmac_key",
                   return_value=b"test-hmac-key"):
            build_field_feedbacks(self.verdict, self.metrics)

    def test_returns_exactly_9_feedbacks_with_correct_names(self):
        """Returns 9 Feedbacks (7 per-field + 2 overall); the 7 per-field
        names match FIELD_ASSESSMENT_NAMES exactly (one row per field)."""
        from judge.evaluator import (
            FIELD_ASSESSMENT_NAMES,
            OVERALL_FORGIVEN_NAME,
            OVERALL_STRICT_NAME,
            build_field_feedbacks,
        )

        feedbacks = build_field_feedbacks(self.verdict, self.metrics)
        self.assertEqual(len(feedbacks), 9)
        per_field_names = [f.name for f in feedbacks[:7]]
        # Exact names, exact order (mirrors JUDGED_FIELDS order).
        expected_names = [FIELD_ASSESSMENT_NAMES[p] for p in JUDGED_FIELDS]
        self.assertEqual(per_field_names, expected_names)
        # All seven distinct.
        self.assertEqual(len(set(per_field_names)), 7)
        # Two overall.
        self.assertEqual(feedbacks[7].name, OVERALL_STRICT_NAME)
        self.assertEqual(feedbacks[8].name, OVERALL_FORGIVEN_NAME)

    def test_each_per_field_value_is_non_none_float_in_unit_interval(self):
        """Every per-field Feedback carries a non-None float value in [0,1]
        — a value mlflow.genai.evaluate aggregates into a ``/mean`` metric
        (a None or string sentinel is skipped by ``_cast_assessment_value_
        to_float``).  Every field here has >=1 scored comparison so its
        accuracy is a real float, not "not_scored"."""
        from judge.evaluator import build_field_feedbacks

        feedbacks = build_field_feedbacks(self.verdict, self.metrics)
        for f in feedbacks[:7]:
            self.assertIsInstance(f.value, float, msg=f"{f.name} value not float")
            self.assertIsNotNone(f.value, msg=f"{f.name} value is None")
            self.assertGreaterEqual(f.value, 0.0, msg=f"{f.name} < 0")
            self.assertLessEqual(f.value, 1.0, msg=f"{f.name} > 1")

    def test_per_field_values_match_metrics(self):
        """Each per-field Feedback value equals the per-field strict accuracy
        computed by verdict_to_metrics for this mixed-outcome verdict."""
        from judge.evaluator import FIELD_ASSESSMENT_NAMES, build_field_feedbacks

        feedbacks = build_field_feedbacks(self.verdict, self.metrics)
        by_name = {f.name: f for f in feedbacks[:7]}
        for field_path, name in FIELD_ASSESSMENT_NAMES.items():
            field_key = field_path.replace("[]", "").replace(".", "_")
            expected_acc = self.metrics[f"judge.{field_key}"]
            self.assertEqual(by_name[name].value, expected_acc,
                             f"value mismatch for {name}")

    def test_overall_strict_and_forgiven_are_non_none_floats(self):
        """The 2 overall Feedbacks carry non-None float strict + forgiven
        accuracy for this verdict (it has scored comparisons)."""
        from judge.evaluator import (
            OVERALL_FORGIVEN_NAME,
            OVERALL_STRICT_NAME,
            build_field_feedbacks,
        )

        feedbacks = build_field_feedbacks(self.verdict, self.metrics)
        overall = {f.name: f for f in feedbacks[7:]}
        for name in (OVERALL_STRICT_NAME, OVERALL_FORGIVEN_NAME):
            self.assertIsInstance(overall[name].value, float, msg=name)
            self.assertIsNotNone(overall[name].value, msg=name)
        self.assertEqual(overall[OVERALL_STRICT_NAME].value,
                         self.metrics["judge.accuracy"])
        self.assertEqual(overall[OVERALL_FORGIVEN_NAME].value,
                         self.metrics["judge.accuracy_forgiven"])

    def test_rationale_is_none_on_every_feedback(self):
        """The free-text rationale is DROPPED (None) on every Feedback — PII
        vector (Opus free-text).  Preserved from PR #43; not regressed by
        the None-leaf hardening."""
        from judge.evaluator import build_field_feedbacks

        feedbacks = build_field_feedbacks(self.verdict, self.metrics)
        for f in feedbacks:
            self.assertIsNone(f.rationale)

    def test_pii_fields_omitted_without_hmac_key(self):
        """Without an HMAC key, cardDisplayName + description expected/actual
        are OMITTED (None) in the per-field Feedback metadata — never the
        cleartext.  The None-leaf hardening must not regress this."""
        from judge.evaluator import build_field_feedbacks

        feedbacks = build_field_feedbacks(self.verdict, self.metrics)
        card_fb = next(f for f in feedbacks if f.name == "judge_cardDisplayName")
        for c in _comparisons(card_fb):
            self.assertIsNone(c["expected"])
            self.assertIsNone(c["actual"])
        desc_fb = next(
            f for f in feedbacks if f.name == "judge_transactions_description"
        )
        for c in _comparisons(desc_fb):
            self.assertIsNone(c["expected"])
            self.assertIsNone(c["actual"])
        # No cleartext PII anywhere in the per-field metadata.
        self.assertNotIn("Platinum", json.dumps(card_fb.metadata))
        self.assertNotIn("UPI-Amazon", json.dumps(desc_fb.metadata))
        self.assertNotIn("BigBazaar", json.dumps(desc_fb.metadata))

    def test_pii_fields_hmac_with_key_configured(self):
        """With an HMAC key, PII fields become keyed HMAC (not None, not
        cleartext) — including on the UNMATCHED_ROW / ABSENT_IN_PDF rows
        whose other leaf is None."""
        from judge.evaluator import build_field_feedbacks

        with patch("judge.scorer._resolve_feedback_hmac_key",
                   return_value=b"test-hmac-key"):
            feedbacks = build_field_feedbacks(self.verdict, self.metrics)

        card_fb = next(f for f in feedbacks if f.name == "judge_cardDisplayName")
        for c in _comparisons(card_fb):
            self.assertIsNotNone(c["expected"])
            self.assertIsInstance(c["expected"], str)
            self.assertTrue(c["expected"].startswith("hmac:"))
            self.assertNotIn("Platinum", str(c["expected"]))

    def test_non_pii_fields_retained_raw_including_none_leaves(self):
        """Non-PII fields (lastFourDigit, amount, date, points) are retained
        raw — including the None leaf on the UNMATCHED_ROW / ABSENT rows
        (None is a valid raw value for those leaves; redaction passes it
        through).  Documented trade-off (not individually identifying)."""
        from judge.evaluator import build_field_feedbacks

        feedbacks = build_field_feedbacks(self.verdict, self.metrics)
        by_name = {f.name: f for f in feedbacks[:7]}

        # lastFourDigit: DISAGREE, both non-null, retained raw.
        last4 = _comparisons(by_name["judge_lastFourDigit"])[0]
        self.assertEqual(last4["expected"], "1234")
        self.assertEqual(last4["actual"], "5678")

        # transactions[].amount: row0 non-null retained; row1 UNMATCHED_ROW
        # expected=None retained raw (not omitted — amount is a KEEP leaf).
        amt_comps = _comparisons(by_name["judge_transactions_amount"])
        self.assertEqual(amt_comps[0]["expected"], 150.0)
        self.assertIsNone(amt_comps[1]["expected"])  # UNMATCHED_ROW leaf

        # transactions[].date: row1 UNMATCHED_ROW actual=None retained raw.
        date_comps = _comparisons(by_name["judge_transactions_date"])
        self.assertEqual(date_comps[0]["expected"], "2026-01-01")
        self.assertIsNone(date_comps[1]["actual"])  # UNMATCHED_ROW leaf

    def test_metadata_carries_field_path_and_comparison_count(self):
        """Each per-field Feedback metadata carries the field_path and the
        count of comparisons for that field (including the None-leaf rows)."""
        from judge.evaluator import build_field_feedbacks

        feedbacks = build_field_feedbacks(self.verdict, self.metrics)
        by_name = {f.name: f for f in feedbacks[:7]}
        # transactions[].date has 2 comparisons (AGREE + UNMATCHED_ROW).
        self.assertEqual(
            by_name["judge_transactions_date"].metadata["field_path"],
            "transactions[].date",
        )
        self.assertEqual(
            by_name["judge_transactions_date"].metadata["n_comparisons"], "2",
        )
        # cardDisplayName has 1.
        self.assertEqual(
            by_name["judge_cardDisplayName"].metadata["n_comparisons"], "1",
        )

    def test_metadata_is_flat_stringable_for_databricks_store(self):
        """Every assessment metadata VALUE is a STRING.  The Databricks
        tracking store persists nested-list assessment metadata fine (proven
        live), but the live probe stringified every metadata value before
        9/9 assessments persisted — so every value is stringified defensively
        (``n_comparisons`` str()'d; the redacted ``comparisons`` list
        json.dumps'd to a JSON string).  The structured list remains in the
        ``verdict_comparisons.json`` artifact (``log_dict`` accepts arbitrary
        JSON)."""
        from judge.evaluator import build_field_feedbacks, COMPARISONS_METADATA_KEY

        feedbacks = build_field_feedbacks(self.verdict, self.metrics)
        for f in feedbacks:
            meta = f.metadata or {}
            for key, value in meta.items():
                # Every metadata value must be a str (the live-probe form).
                self.assertIsInstance(
                    value, str,
                    msg=f"{f.name}.metadata[{key!r}] is {type(value).__name__}, not str",
                )
        # The redacted comparisons are still recoverable as a JSON string.
        card_fb = next(f for f in feedbacks if f.name == "judge_cardDisplayName")
        comps_json = card_fb.metadata[COMPARISONS_METADATA_KEY]
        self.assertIsInstance(comps_json, str)
        decoded = json.loads(comps_json)
        self.assertIsInstance(decoded, list)
        self.assertEqual(len(decoded), 1)
        self.assertEqual(decoded[0]["field_path"],
                         "cards[].cardMeta.cardDisplayName")

    def test_no_assessment_name_contains_a_dot(self):
        """REGRESSION GUARD FOR THE REAL ROOT CAUSE: the Databricks tracking
        store REJECTS any assessment whose name contains a '.' (RestException
        INVALID_PARAMETER_VALUE: 'assessment_name' must not contain ".").
        Proven live: the earlier dotted ``judge.<field>`` names caused 0/9
        assessments to persist (zero judge assessments ever appeared in
        production), while dot-free ``judge_<field>`` + stringified metadata
        persisted 9/9.  This test pins that NO name returned by
        build_field_feedbacks (per-field OR overall) ever contains a dot —
        checked for BOTH the mixed-outcome verdict AND the all-ABSENT
        JUDGE_ERROR verdict — so a future rename back to a dotted form
        fails the gate."""
        from judge.comparison import judge_error_comparisons
        from judge.evaluator import build_field_feedbacks

        def _assert_no_dots(feedbacks):
            self.assertEqual(len(feedbacks), 9)
            for f in feedbacks:
                self.assertNotIn(
                    ".", f.name,
                    msg=f"assessment name {f.name!r} contains a dot — "
                        "Databricks rejects dotted assessment names "
                        "(INVALID_PARAMETER_VALUE)",
                )

        # Mixed-outcome verdict (UNMATCHED_ROW / ABSENT_IN_PDF / DISAGREE +
        # None leaves).
        _assert_no_dots(build_field_feedbacks(self.verdict, self.metrics))

        # JUDGE_ERROR verdict (all ABSENT_IN_PDF with None leaves).
        error_comps = judge_error_comparisons("opus returned unusable json")
        error_verdict = JudgeVerdict(
            request_id="req-test",
            judge_model_id="databricks-claude-opus-5",
            comparisons=error_comps,
            latency_ms=50.0,
            summary=json.dumps({"status": "JUDGE_ERROR"}),
        )
        _assert_no_dots(
            build_field_feedbacks(error_verdict, verdict_to_metrics(error_verdict))
        )

    def test_every_metadata_value_is_a_str(self):
        """REGRESSION GUARD: every metadata VALUE on every assessment is a
        ``str``.  The Databricks tracking store persists nested-list
        assessment metadata fine (proven live), but the live probe
        stringified every metadata value before 9/9 assessments persisted
        (non-str values — int counts, nested lists — are risky).  So
        ``n_comparisons`` / ``n_scored`` / ``n_correct`` are str()'d and the
        redacted ``comparisons`` list is json.dumps'd to a JSON STRING.  This
        test pins that every metadata value is a str — checked for BOTH the
        mixed-outcome verdict AND the all-ABSENT JUDGE_ERROR verdict."""
        from judge.comparison import judge_error_comparisons
        from judge.evaluator import build_field_feedbacks

        def _assert_all_str(feedbacks):
            self.assertEqual(len(feedbacks), 9)
            for f in feedbacks:
                meta = f.metadata or {}
                for key, value in meta.items():
                    self.assertIsInstance(
                        value, str,
                        msg=f"{f.name}.metadata[{key!r}] is "
                            f"{type(value).__name__}, not str",
                    )

        # Mixed-outcome verdict.
        _assert_all_str(build_field_feedbacks(self.verdict, self.metrics))

        # JUDGE_ERROR verdict.
        error_comps = judge_error_comparisons("opus returned unusable json")
        error_verdict = JudgeVerdict(
            request_id="req-test",
            judge_model_id="databricks-claude-opus-5",
            comparisons=error_comps,
            latency_ms=50.0,
            summary=json.dumps({"status": "JUDGE_ERROR"}),
        )
        _assert_all_str(
            build_field_feedbacks(error_verdict, verdict_to_metrics(error_verdict))
        )

    def test_judge_error_verdict_does_not_raise(self):
        """A JUDGE_ERROR verdict (Opus returned an unusable response) is all
        ABSENT_IN_PDF sentinels with expected=None/actual=None.  The builder
        must not raise on it either, and must still return 9 Feedbacks."""
        from judge.comparison import judge_error_comparisons
        from judge.evaluator import build_field_feedbacks

        comps = judge_error_comparisons("opus returned unusable json")
        verdict = JudgeVerdict(
            request_id="req-test",
            judge_model_id="databricks-claude-opus-5",
            comparisons=comps,
            latency_ms=50.0,
            summary=json.dumps({"status": "JUDGE_ERROR"}),
        )
        metrics = verdict_to_metrics(verdict)
        feedbacks = build_field_feedbacks(verdict, metrics)
        self.assertEqual(len(feedbacks), 9)
        # Every field is all-ABSENT_IN_PDF → no scored comparisons → accuracy
        # None → the builder emits the "not_scored" sentinel (Feedback rejects
        # value=None; the sentinel preserves 7 rows while genai.evaluate's
        # aggregation skips it via _cast_assessment_value_to_float).
        for f in feedbacks[:7]:
            self.assertEqual(f.value, "not_scored")


# ---------------------------------------------------------------------------
# Real-mlflow end-to-end tests (temp file store)
# ---------------------------------------------------------------------------

def _mlflow_available() -> bool:
    try:
        import mlflow  # noqa: F401
        import pandas  # noqa: F401
        return True
    except Exception:
        return False


@unittest.skipUnless(_mlflow_available(), "mlflow/pandas not importable")
class RunGenaiEvaluationRealTest(unittest.TestCase):
    """End-to-end against a real local mlflow file store (temp dir).

    Creates a parse run with statement.pdf + extraction.json artifacts, a
    trace linked to that run (carrying mlflow.sourceRun), then calls
    ``run_genai_evaluation`` with the trace.  Verifies that genai.evaluate
    drives the scorer once per trace, Opus is called exactly once, per-field
    assessments land on the trace, and the verdict is persisted to a fake
    Lakebase store.
    """

    def setUp(self):
        import mlflow
        import judge.scorer as scorer_mod

        self._saved_configured = scorer_mod._mlflow_configured
        scorer_mod._mlflow_configured = True  # skip the databricks URI override
        self._saved_uri = mlflow.get_tracking_uri()
        self._saved_exp_env = os.environ.get("MLFLOW_EXPERIMENT_ID")
        self._tmp = tempfile.mkdtemp(prefix="mlflow-genai-eval-test-")
        mlflow.set_tracking_uri(f"file://{self._tmp}")
        self._mlflow = mlflow
        self._exp_id = mlflow.create_experiment("genai-eval-test")
        # Set MLFLOW_EXPERIMENT_ID so @mlflow.trace sends traces to our temp
        # experiment (not the default experiment 0 which doesn't exist in
        # the temp file store).
        os.environ["MLFLOW_EXPERIMENT_ID"] = self._exp_id

    def tearDown(self):
        import mlflow
        import judge.scorer as scorer_mod

        scorer_mod._mlflow_configured = self._saved_configured
        if self._saved_uri:
            mlflow.set_tracking_uri(self._saved_uri)
        if self._saved_exp_env is None:
            os.environ.pop("MLFLOW_EXPERIMENT_ID", None)
        else:
            os.environ["MLFLOW_EXPERIMENT_ID"] = self._saved_exp_env
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _create_parse_run_with_artifacts(self, run_id_suffix: str = "1"):
        """Create a parse run with statement.pdf + extraction.json artifacts.
        Returns the run_id."""
        import mlflow

        meta = {
            "request_id": f"req-{run_id_suffix:>012s}"[:16],
            "bank": "HDFC",
            "payload": {
                "cards": [{"cardMeta": {"cardDisplayName": "Platinum",
                                        "lastFourDigit": "1234"}}],
                "rewards": {"pointsEarnedThisCycle": 100, "closingPoints": 500},
                "transactions": [],
            },
            "model_id": "fake-luna",
            "schema_valid": True,
        }
        with mlflow.start_run(experiment_id=self._exp_id,
                              run_name=f"parse-{run_id_suffix}") as run:
            run_id = run.info.run_id
            # Log the PDF + extraction artifacts.
            mlflow.log_dict(meta, "extraction.json")
            # statement.pdf — log as a FILE named "statement.pdf" at the
            # artifact root (mirrors harness/tracing.py log_artifact which
            # writes to a temp dir named statement.pdf, artifact_path=None).
            # Using artifact_path="statement.pdf" would create a DIRECTORY
            # containing the file, and download_artifacts(artifact_path=
            # "statement.pdf") would return the dir path — read_bytes() fails.
            import tempfile as _tf
            with _tf.TemporaryDirectory() as tmpdir:
                pdf_path = os.path.join(tmpdir, "statement.pdf")
                with open(pdf_path, "wb") as f:
                    f.write(b"%PDF-1.4 fake pdf")
                mlflow.log_artifact(pdf_path)  # artifact_path=None → root
        return run_id

    def _create_trace_for_run(self, run_id: str, request_id: str):
        """Create a trace linked to ``run_id`` with mlflow.sourceRun metadata.
        Returns the Trace object."""
        import mlflow

        # Use mlflow.trace to create a trace, then set the sourceRun metadata.
        @mlflow.trace(name="parse")
        def _make_trace():
            mlflow.log_artifact  # noop to keep the span alive
            return {"request_id": request_id, "bank": "HDFC"}

        _make_trace()
        # Fetch the trace we just created and annotate it with sourceRun.
        traces = mlflow.search_traces(
            experiment_ids=[self._exp_id], max_results=10, return_type="list"
        )
        if not traces:
            self.skipTest("could not create a trace in the temp file store")
        trace = traces[0]
        # Set the sourceRun metadata so the scorer can resolve run_id.
        trace.info.request_metadata["mlflow.sourceRun"] = run_id
        trace.info.request_metadata["mlflow.traceInputs"] = json.dumps(
            {"request_id": request_id, "bank": "HDFC"}
        )
        return trace

    def test_genai_evaluation_logs_assessments_and_calls_opus_once(self):
        """genai.evaluate drives the scorer once per trace, Opus is called
        exactly once (not 7×), per-field assessments land on the trace, and
        the verdict is persisted to Lakebase."""
        from judge.evaluator import (
            FIELD_ASSESSMENT_NAMES,
            OVERALL_FORGIVEN_NAME,
            OVERALL_STRICT_NAME,
            run_genai_evaluation,
        )

        run_id = self._create_parse_run_with_artifacts("1")
        request_id = f"req-{'1':>012s}"[:16]
        trace = self._create_trace_for_run(run_id, request_id)
        trace_id = trace.info.trace_id

        # Fake Lakebase store to capture save_verdict.
        saved_verdicts: list = []

        class _FakeStore:
            def save_verdict(self, verdict) -> None:
                saved_verdicts.append(verdict)

        verdict = _full_verdict(request_id)
        opus_call_count = [0]

        def _fake_judge(request, extraction):
            opus_call_count[0] += 1
            return verdict

        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.side_effect = _fake_judge
            eval_info = run_genai_evaluation(
                [trace], _FakeStore(), experiment_id=self._exp_id,
            )

        # genai.evaluate succeeded.
        self.assertIsNotNone(eval_info)
        self.assertIn("eval_run_id", eval_info)
        self.assertIn("results", eval_info)

        # Opus called EXACTLY ONCE (not 7× — one scorer, one trace).
        self.assertEqual(opus_call_count[0], 1)

        # The verdict was persisted to Lakebase (save_verdict called).
        self.assertEqual(len(saved_verdicts), 1)
        self.assertEqual(saved_verdicts[0].request_id, request_id)

        # The side-channel collected a result dict with the right shape.
        results = eval_info["results"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "OK")
        self.assertEqual(results[0]["run_id"], run_id)
        self.assertEqual(results[0]["bank"], "HDFC")

        # The 7 per-field + 2 overall assessments were ACTUALLY attached to
        # a trace in the experiment (read back via Trace.search_assessments —
        # the local file store supports the assessment API in mlflow 3.10.1).
        # NOTE: for the local FileStore, genai.evaluate CLONES the parse trace
        # (FileStore doesn't support trace↔run linking) and logs assessments
        # to the CLONE — so we search ALL traces, not just the original.
        import mlflow as _mlflow

        traces = _mlflow.search_traces(
            experiment_ids=[self._exp_id], max_results=20, return_type="list",
        )
        all_assessments: list = []
        for t in traces:
            all_assessments.extend(t.search_assessments())
        # 7 per-field + 2 overall = 9 assessments.
        self.assertEqual(len(all_assessments), 9)
        assessment_names = {a.name for a in all_assessments}
        # All 7 per-field names present.
        self.assertEqual(
            assessment_names & set(FIELD_ASSESSMENT_NAMES.values()),
            set(FIELD_ASSESSMENT_NAMES.values()),
        )
        # Both overall names present.
        self.assertIn(OVERALL_STRICT_NAME, assessment_names)
        self.assertIn(OVERALL_FORGIVEN_NAME, assessment_names)

    def test_empty_traces_returns_none(self):
        """When no traces are passed, run_genai_evaluation returns None."""
        from judge.evaluator import run_genai_evaluation

        result = run_genai_evaluation([], None, experiment_id=self._exp_id)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
