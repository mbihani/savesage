"""Tests for the MLflow Evaluate-based judge scorer (judge/evaluator.py).

Two layers:

* ``BuildEvalRowsTest`` — pure-stdlib unit tests for ``build_eval_rows`` (the
  eval-table row construction).  No mlflow/pandas required.
* ``RunMlflowEvaluationRealTest`` — end-to-end against a REAL local mlflow
  file store (temp dir), verifying the evaluation run, its metrics, tags, and
  the ``eval_results_table`` artifact.  Skipped if ``mlflow``/``pandas`` are
  not importable.  This is the strongest signal that the
  ``mlflow.models.evaluate`` integration works on the runtime the App uses
  (``mlflow[databricks]==3.2.0``); the spike in the worktree confirmed the
  same API surface against mlflow 3.10.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from judge.scorer import JUDGED_FIELDS


# ---------------------------------------------------------------------------
# Sample results / summary builders (mirror score_trace + _aggregate_results)
# ---------------------------------------------------------------------------

def _ok_result(run_id="r1", bank="HDFC", strict=1.0, forgiven=1.0, request_id="req-1"):
    """Build a score_trace-style OK result dict with all 7 per-field keys."""
    per_field = {
        "cards_cardMeta_cardDisplayName": strict,
        "cards_cardMeta_lastFourDigit": strict,
        "rewards_pointsEarnedThisCycle": strict,
        "rewards_closingPoints": strict,
        "transactions_date": None,
        "transactions_description": None,
        "transactions_amount": None,
    }
    return {
        "run_id": run_id, "status": "OK", "bank": bank, "request_id": request_id,
        "strict_accuracy": strict, "narration_forgiven_accuracy": forgiven,
        "comparisons": 4, "scored": 4, "correct": 4,
        "per_field": per_field,
    }


def _summary(count_judged=2, count_errors=0):
    """Build an _aggregate_results-style summary with per_field/per_bank."""
    return {
        "count_judged": count_judged,
        "count_errors": count_errors,
        "overall_strict": 0.75,
        "overall_narration_forgiven": 0.875,
        "per_field": {
            "cards[].cardMeta.cardDisplayName": {"accuracy": 0.5, "count": 2},
            "cards[].cardMeta.lastFourDigit": {"accuracy": 1.0, "count": 2},
            "rewards.pointsEarnedThisCycle": {"accuracy": 0.5, "count": 2},
            "rewards.closingPoints": {"accuracy": 1.0, "count": 2},
            "transactions[].date": {"accuracy": None, "count": 0},
            "transactions[].description": {"accuracy": None, "count": 0},
            "transactions[].amount": {"accuracy": None, "count": 0},
        },
        "per_bank": {
            "HDFC": {"count": 1, "strict_accuracy": 1.0,
                     "narration_forgiven_accuracy": 1.0},
            "ICICI": {"count": 1, "strict_accuracy": 0.5,
                      "narration_forgiven_accuracy": 0.75},
        },
    }


# ---------------------------------------------------------------------------
# Pure-logic tests for build_eval_rows
# ---------------------------------------------------------------------------

class BuildEvalRowsTest(unittest.TestCase):
    def test_one_row_per_ok_trace(self):
        """Each OK trace becomes exactly one row."""
        from judge.evaluator import build_eval_rows
        results = [
            _ok_result("r1", "HDFC", 1.0, 1.0),
            _ok_result("r2", "ICICI", 0.5, 0.75),
            {"run_id": "r3", "status": "ERROR", "error": "boom"},
            {"run_id": "r4", "status": "JUDGE_ERROR", "error": "truncated"},
        ]
        rows = build_eval_rows(results)
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["run_id"] for r in rows}, {"r1", "r2"})

    def test_row_has_strict_forgiven_bank_and_all_fields(self):
        """Each row carries run_id, bank, strict/forgiven, and 7 per-field keys."""
        from judge.evaluator import build_eval_rows
        rows = build_eval_rows([_ok_result("r1", "HDFC", 1.0, 1.0)])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["run_id"], "r1")
        self.assertEqual(row["bank"], "HDFC")
        self.assertEqual(row["strict_accuracy"], 1.0)
        self.assertEqual(row["narration_forgiven_accuracy"], 1.0)
        # All 7 judged fields are present as flat keys.
        for field in JUDGED_FIELDS:
            key = field.replace("[]", "").replace(".", "_")
            self.assertIn(key, row)

    def test_error_and_judge_error_excluded(self):
        """ERROR and JUDGE_ERROR traces produce no rows (counted in summary)."""
        from judge.evaluator import build_eval_rows
        rows = build_eval_rows([
            {"run_id": "r1", "status": "ERROR"},
            {"run_id": "r2", "status": "JUDGE_ERROR"},
        ])
        self.assertEqual(rows, [])

    def test_empty_results(self):
        from judge.evaluator import build_eval_rows
        self.assertEqual(build_eval_rows([]), [])

    def test_none_accuracy_preserved_in_row(self):
        """A None per-field accuracy is kept (renders as null in the table)."""
        from judge.evaluator import build_eval_rows
        rows = build_eval_rows([_ok_result("r1", "HDFC", 1.0, 1.0)])
        # The transaction fields are None in _ok_result.
        self.assertIsNone(rows[0]["transactions_date"])
        self.assertIsNone(rows[0]["transactions_amount"])

    def test_per_field_accuracy_values_transferred(self):
        """Every per-field accuracy VALUE is transferred from the result dict
        to the eval row (not just the key existing).  Set a distinct value for
        each of the 7 judged fields and assert each round-trips exactly."""
        from judge.evaluator import build_eval_rows

        # A distinct accuracy value for each of the 7 judged fields, keyed by
        # the flat metric/row key (mirrors _field_key in evaluator.py).
        distinct = {
            "cards_cardMeta_cardDisplayName": 0.10,
            "cards_cardMeta_lastFourDigit": 0.20,
            "rewards_pointsEarnedThisCycle": 0.30,
            "rewards_closingPoints": 0.40,
            "transactions_date": 0.50,
            "transactions_description": 0.60,
            "transactions_amount": 0.70,
        }
        result = {
            "run_id": "r1", "status": "OK", "bank": "HDFC", "request_id": "req-1",
            "strict_accuracy": 1.0, "narration_forgiven_accuracy": 1.0,
            "comparisons": 7, "scored": 7, "correct": 4,
            "per_field": distinct,
        }
        row = build_eval_rows([result])[0]

        # Every per-field value is transferred exactly — not merely present.
        for field in JUDGED_FIELDS:
            key = field.replace("[]", "").replace(".", "_")
            self.assertEqual(row[key], distinct[key])

        # The carried scalar values round-trip too.
        self.assertEqual(row["run_id"], "r1")
        self.assertEqual(row["bank"], "HDFC")
        self.assertEqual(row["strict_accuracy"], 1.0)
        self.assertEqual(row["narration_forgiven_accuracy"], 1.0)


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
class RunMlflowEvaluationRealTest(unittest.TestCase):
    """End-to-end against a real local mlflow file store (temp dir).

    Pins ``judge.scorer._mlflow_configured = True`` so
    ``_ensure_mlflow_configured`` is a no-op (it otherwise forces the
    tracking URI to ``databricks``, which is unreachable locally).  The temp
    file store is set explicitly in ``setUp``.
    """

    def setUp(self):
        import judge.scorer as scorer_mod
        import mlflow
        self._saved_configured = scorer_mod._mlflow_configured
        scorer_mod._mlflow_configured = True  # skip the databricks URI override
        self._saved_uri = mlflow.get_tracking_uri()
        # Preserve MLFLOW_EXPERIMENT_ID — do NOT use mlflow.set_experiment
        # here: it sets that env var process-wide, which would leak into
        # judge.scorer._get_experiment_id and poison other tests.  We create
        # the experiment and pass its id explicitly instead.
        self._saved_exp_env = os.environ.get("MLFLOW_EXPERIMENT_ID")
        self._tmp = tempfile.mkdtemp(prefix="mlflow-eval-test-")
        mlflow.set_tracking_uri(f"file://{self._tmp}")
        self._exp_id = mlflow.create_experiment("evaluator-test")

    def tearDown(self):
        import judge.scorer as scorer_mod
        import mlflow
        scorer_mod._mlflow_configured = self._saved_configured
        if self._saved_uri:
            mlflow.set_tracking_uri(self._saved_uri)
        # Restore MLFLOW_EXPERIMENT_ID in case any mlflow call set it.
        if self._saved_exp_env is None:
            os.environ.pop("MLFLOW_EXPERIMENT_ID", None)
        else:
            os.environ["MLFLOW_EXPERIMENT_ID"] = self._saved_exp_env
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_creates_eval_run_with_metrics_tags_and_table(self):
        """Happy path: an evaluation run is created with the two custom-scorer
        aggregate metrics, supplementary metrics, eval_run tag, and an
        eval_results_table artifact."""
        import mlflow
        from judge.evaluator import run_mlflow_evaluation

        results = [
            _ok_result("r1", "HDFC", 1.0, 1.0, "req-1"),
            _ok_result("r2", "ICICI", 0.5, 0.75, "req-2"),
            {"run_id": "r3", "status": "ERROR", "error": "boom"},
        ]
        info = run_mlflow_evaluation(
            results, _summary(count_judged=2, count_errors=1),
            experiment_id=self._exp_id,
        )
        self.assertIsNotNone(info)
        eval_run_id = info["eval_run_id"]
        self.assertEqual(info["count_rows"], 2)

        client = mlflow.tracking.MlflowClient()
        run = client.get_run(eval_run_id)
        metrics = dict(run.data.metrics)
        tags = dict(run.data.tags)

        # The two custom scorers logged their aggregate means.
        self.assertIn("judge.mean_strict_accuracy", metrics)
        self.assertAlmostEqual(metrics["judge.mean_strict_accuracy"], 0.75)
        self.assertIn("judge.mean_narration_forgiven", metrics)
        self.assertAlmostEqual(metrics["judge.mean_narration_forgiven"], 0.875)

        # Supplementary metrics (counts, per-field, per-bank) are present.
        self.assertEqual(metrics["judge.count_judged"], 2.0)
        self.assertEqual(metrics["judge.count_errors"], 1.0)
        self.assertIn("judge.mean_cards_cardMeta_cardDisplayName", metrics)
        self.assertIn("judge.bank.HDFC.strict", metrics)

        # The run is tagged as an evaluation run.
        self.assertEqual(tags.get("eval_run"), "true")
        self.assertEqual(tags.get("judge_evaluation"), "true")

        # The eval_results_table artifact exists and has one row per OK trace.
        arts = client.list_artifacts(eval_run_id)
        self.assertTrue(any("eval_results_table" in a.path for a in arts))
        table_art = next(a for a in arts if "eval_results_table" in a.path)
        path = mlflow.artifacts.download_artifacts(
            run_id=eval_run_id, artifact_path=table_art.path
        )
        with open(path) as f:
            table = json.load(f)
        # Two OK traces → two data rows.
        self.assertEqual(len(table["data"]), 2)
        # The per-row table carries run_id, bank, the per-field columns,
        # and the outputs (strict_accuracy) column.
        self.assertIn("run_id", table["columns"])
        self.assertIn("bank", table["columns"])
        self.assertIn("outputs", table["columns"])
        self.assertIn("judge.mean_strict_accuracy/score", table["columns"])

    def test_empty_results_returns_none_no_run(self):
        """When there are no OK traces, no evaluation run is created."""
        import mlflow
        from judge.evaluator import run_mlflow_evaluation

        before = len(mlflow.search_runs([self._exp_id]))
        info = run_mlflow_evaluation(
            [{"run_id": "r1", "status": "ERROR", "error": "boom"}],
            _summary(count_judged=0, count_errors=1),
            experiment_id=self._exp_id,
        )
        self.assertIsNone(info)
        after = len(mlflow.search_runs([self._exp_id]))
        self.assertEqual(before, after, "no run should have been created")

    def test_mlflow_failure_returns_none_not_raise(self):
        """If the mlflow call raises, run_mlflow_evaluation returns None
        (best-effort) rather than propagating — so run_judge_evaluation's
        per-trace results still return."""
        import mlflow
        from judge.evaluator import run_mlflow_evaluation

        results = [_ok_result("r1", "HDFC", 1.0, 1.0)]
        with patch("mlflow.start_run", side_effect=RuntimeError("boom")):
            info = run_mlflow_evaluation(
                results, _summary(), experiment_id=self._exp_id,
            )
        self.assertIsNone(info)


if __name__ == "__main__":
    unittest.main()
