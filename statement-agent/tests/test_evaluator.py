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
        card_fb = next(f for f in feedbacks if f.name == "judge.cardDisplayName")
        comps = card_fb.metadata["comparisons"]
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
        desc_fb = next(f for f in feedbacks if f.name == "judge.transactions.description")
        comps = desc_fb.metadata["comparisons"]
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
        last4_comps = by_name["judge.lastFourDigit"].metadata["comparisons"]
        self.assertEqual(last4_comps[0]["expected"], "1234")

        # amount retained raw.
        amt_comps = by_name["judge.transactions.amount"].metadata["comparisons"]
        self.assertEqual(amt_comps[0]["expected"], 150.0)

        # date retained raw.
        date_comps = by_name["judge.transactions.date"].metadata["comparisons"]
        self.assertEqual(date_comps[0]["expected"], "2026-01-01")

        # points retained raw.
        pts_comps = by_name["judge.pointsEarnedThisCycle"].metadata["comparisons"]
        self.assertEqual(pts_comps[0]["expected"], 100)

    def test_pii_fields_hmac_with_key_configured(self):
        """When an HMAC key IS configured, PII fields become keyed HMAC
        (not None, not cleartext) — consistent with the redaction policy."""
        from judge.evaluator import build_field_feedbacks

        with patch("judge.scorer._resolve_feedback_hmac_key",
                   return_value=b"test-hmac-key"):
            feedbacks = build_field_feedbacks(self.verdict, self.metrics)

        card_fb = next(f for f in feedbacks if f.name == "judge.cardDisplayName")
        comps = card_fb.metadata["comparisons"]
        # HMAC'd — a non-empty string, NOT the cleartext, NOT None.
        self.assertIsNotNone(comps[0]["expected"])
        self.assertNotEqual(comps[0]["expected"], "Platinum Card")
        self.assertNotIn("Platinum", str(comps[0]["expected"]))

    def test_metadata_carries_field_path_and_comparison_count(self):
        """Each per-field Feedback metadata carries the field_path and the
        count of comparisons for that field."""
        from judge.evaluator import build_field_feedbacks

        feedbacks = build_field_feedbacks(self.verdict, self.metrics)
        card_fb = next(f for f in feedbacks if f.name == "judge.cardDisplayName")
        self.assertEqual(card_fb.metadata["field_path"],
                         "cards[].cardMeta.cardDisplayName")
        self.assertEqual(card_fb.metadata["n_comparisons"], 1)

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
        txn_date = next(f for f in feedbacks if f.name == "judge.transactions.date")
        self.assertEqual(txn_date.value, "not_scored")
        self.assertEqual(txn_date.metadata["n_comparisons"], 0)
        self.assertEqual(txn_date.metadata["comparisons"], [])


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
