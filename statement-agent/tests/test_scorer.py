"""Stdlib-only tests for the post-hoc judge scorer (judge/scorer.py).

Mocks mlflow (which cannot be installed locally) by injecting a fake module
into ``sys.modules`` and patches ``OpusJudgeAdapter.judge`` (which calls Opus
via HTTP) so the full score_trace flow can be exercised without network access.

Covers:
* ``score_trace``: artifact download → opus → compare → log metrics → tag.
* ``run_judge_evaluation``: sampling unjudged traces, tagging, error handling.
* ``_aggregate_results``: aggregate summary structure.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from contracts.models import (
    Bank,
    ComparisonOutcome,
    FieldComparison,
    FieldScope,
    JudgeVerdict,
    MatchMethod,
    ParseRequest,
)


# ---------------------------------------------------------------------------
# Fake mlflow module (injected into sys.modules for score_trace tests)
# ---------------------------------------------------------------------------

class _FakeArtifacts:
    """Fake mlflow.artifacts — download_artifacts returns temp file paths."""

    def __init__(self):
        self._files: dict[str, str] = {}  # artifact_path → temp file path

    def register(self, artifact_path: str, content: bytes) -> None:
        """Register content for an artifact path; download_artifacts returns it."""
        suffix = Path(artifact_path).suffix
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        self._files[artifact_path] = tmp_path

    def download_artifacts(self, run_id=None, artifact_path=None, **kw):
        path = self._files.get(artifact_path)
        if path is None:
            raise FileNotFoundError(f"artifact not found: {artifact_path}")
        return path


class _FakeExperiment:
    def __init__(self, exp_id="exp-1"):
        self.experiment_id = exp_id


class _FakeSeries:
    """Fake pandas Series — supports .tolist() and truthiness."""

    def __init__(self, values):
        self._values = values

    def tolist(self):
        return list(self._values)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __bool__(self):
        return bool(self._values)


class _FakeRunsFrame:
    """Fake pandas-like DataFrame for search_runs results."""

    def __init__(self, run_ids: list[str]):
        self._run_ids = run_ids
        self.empty = len(run_ids) == 0

    def __getitem__(self, key):
        if key == "run_id":
            return _FakeSeries(self._run_ids)
        return _FakeSeries([])


class _FakeMLflowModule:
    """Fake mlflow module for score_trace / run_judge_evaluation tests."""

    def __init__(self):
        self.artifacts = _FakeArtifacts()
        self.logged_metrics: list[tuple] = []  # (key, value, run_id)
        self.set_tags: list[tuple] = []  # (key, value, run_id)
        self._experiment = _FakeExperiment()
        self._search_runs_result = _FakeRunsFrame([])

    def log_metric(self, key, value, run_id=None):
        self.logged_metrics.append((key, value, run_id))

    def set_tag(self, key, value, run_id=None):
        self.set_tags.append((key, value, run_id))

    def get_experiment_by_name(self, name):
        return self._experiment

    def search_runs(self, experiment_ids=None, filter_string=None, max_results=100):
        return self._search_runs_result

    def set_search_runs_result(self, run_ids: list[str]):
        self._search_runs_result = _FakeRunsFrame(run_ids)


def _install_fake_mlflow():
    """Insert a fake mlflow module into sys.modules; return it."""
    fake = _FakeMLflowModule()
    sys.modules["mlflow"] = fake
    return fake


def _uninstall_fake_mlflow():
    sys.modules.pop("mlflow", None)


# ---------------------------------------------------------------------------
# Helpers for building a fake verdict
# ---------------------------------------------------------------------------

def _make_verdict(request_id="req-test") -> JudgeVerdict:
    return JudgeVerdict(
        request_id=request_id,
        judge_model_id="databricks-claude-opus-5",
        comparisons=(
            FieldComparison(
                "cards[].cardMeta.cardDisplayName", "Platinum", "Platinum",
                ComparisonOutcome.AGREE, FieldScope.SCALAR, card_index=0,
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
        ),
        latency_ms=50.0,
        summary=json.dumps({"status": "OK", "strict": {"correct": 4, "scored": 4, "accuracy": 1.0}}),
    )


def _make_extraction_meta(bank="HDFC", request_id="req-test"):
    return {
        "request_id": request_id,
        "bank": bank,
        "payload": {
            "cards": [{"cardMeta": {"cardDisplayName": "Platinum", "lastFourDigit": "1234"}}],
            "rewards": {"pointsEarnedThisCycle": 100, "closingPoints": 500},
            "transactions": [],
        },
        "model_id": "fake-luna",
        "schema_valid": True,
    }


# ---------------------------------------------------------------------------
# score_trace tests
# ---------------------------------------------------------------------------

class ScoreTraceTest(unittest.TestCase):
    def setUp(self):
        self.fake_mlflow = _install_fake_mlflow()

    def tearDown(self):
        _uninstall_fake_mlflow()

    def test_score_trace_full_flow(self):
        """Full score_trace: download PDF + extraction → opus → metrics → tag."""
        from judge.scorer import score_trace

        # Register artifacts on the fake mlflow.
        meta = _make_extraction_meta()
        self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4 fake pdf")
        self.fake_mlflow.artifacts.register("extraction.json", json.dumps(meta).encode())

        # Mock OpusJudgeAdapter.judge to return a known verdict.
        verdict = _make_verdict()
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = verdict
            result = score_trace("run-123")

        self.assertEqual(result["run_id"], "run-123")
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["request_id"], "req-test")
        self.assertEqual(result["bank"], "HDFC")
        self.assertEqual(result["strict_accuracy"], 1.0)
        self.assertEqual(result["narration_forgiven_accuracy"], 1.0)
        self.assertEqual(result["comparisons"], 4)
        self.assertEqual(result["scored"], 4)
        self.assertEqual(result["correct"], 4)

        # Metrics logged to the same run.
        metric_keys = [(k, v, rid) for k, v, rid in self.fake_mlflow.logged_metrics]
        run_metrics = [(k, v) for k, v, rid in metric_keys if rid == "run-123"]
        self.assertIn(("judge.accuracy", 1.0), run_metrics)
        self.assertIn(("judge.comparisons", 4.0), run_metrics)

        # Tagged as judged=true.
        self.assertIn(("judged", "true", "run-123"), self.fake_mlflow.set_tags)

    def test_score_trace_missing_pdf_returns_error(self):
        """If the PDF artifact is missing, score_trace returns an error dict."""
        from judge.scorer import score_trace

        # Only register extraction.json, not statement.pdf.
        self.fake_mlflow.artifacts.register(
            "extraction.json", json.dumps(_make_extraction_meta()).encode()
        )

        with patch("harness.judge_adapter.OpusJudgeAdapter"):
            result = score_trace("run-missing-pdf")

        self.assertEqual(result["status"], "ERROR")
        self.assertIn("run_id", result)
        self.assertIn("error", result)

    def test_score_trace_opus_failure_returns_error(self):
        """If OpusJudgeAdapter.judge raises, score_trace captures the error."""
        from judge.scorer import score_trace

        self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4 fake")
        self.fake_mlflow.artifacts.register(
            "extraction.json", json.dumps(_make_extraction_meta()).encode()
        )

        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.side_effect = RuntimeError("opus 500")
            result = score_trace("run-opus-fail")

        self.assertEqual(result["status"], "ERROR")
        self.assertIn("opus 500", result["error"])

    def test_score_trace_per_field_metrics(self):
        """Per-field metrics are returned in the result."""
        from judge.scorer import score_trace

        self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4")
        self.fake_mlflow.artifacts.register(
            "extraction.json", json.dumps(_make_extraction_meta()).encode()
        )

        verdict = _make_verdict()
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = verdict
            result = score_trace("run-fields")

        per_field = result["per_field"]
        # The 4 scalar fields should have accuracy=1.0
        self.assertEqual(per_field["cards_cardMeta_cardDisplayName"], 1.0)
        self.assertEqual(per_field["cards_cardMeta_lastFourDigit"], 1.0)
        self.assertEqual(per_field["rewards_pointsEarnedThisCycle"], 1.0)
        self.assertEqual(per_field["rewards_closingPoints"], 1.0)


# ---------------------------------------------------------------------------
# run_judge_evaluation tests
# ---------------------------------------------------------------------------

class RunJudgeEvaluationTest(unittest.TestCase):
    def setUp(self):
        self.fake_mlflow = _install_fake_mlflow()

    def tearDown(self):
        _uninstall_fake_mlflow()

    def test_no_unjudged_traces_returns_empty_summary(self):
        """When no unjudged traces exist, returns a zero-count summary."""
        from judge.scorer import run_judge_evaluation

        self.fake_mlflow.set_search_runs_result([])
        result = run_judge_evaluation(sample_size=10)

        self.assertEqual(result["count_judged"], 0)
        self.assertEqual(result["errors"], [])
        self.assertIsNone(result["overall_strict"])

    def test_experiment_not_found_returns_error(self):
        """When the experiment doesn't exist, returns an error summary."""
        from judge.scorer import run_judge_evaluation

        self.fake_mlflow._experiment = None
        result = run_judge_evaluation(sample_size=5)

        self.assertEqual(result["count_judged"], 0)
        self.assertGreater(len(result["errors"]), 0)

    def test_samples_and_scores_traces(self):
        """run_judge_evaluation samples N traces, scores each, aggregates."""
        from judge.scorer import run_judge_evaluation

        # 3 unjudged traces.
        self.fake_mlflow.set_search_runs_result(["run-1", "run-2", "run-3"])

        # Register artifacts for all 3.
        for i in range(1, 4):
            meta = _make_extraction_meta(request_id=f"req-{i}")
            self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4 fake")
            self.fake_mlflow.artifacts.register(
                "extraction.json", json.dumps(meta).encode()
            )

        verdict = _make_verdict()
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = verdict
            result = run_judge_evaluation(sample_size=3)

        self.assertEqual(result["count_judged"], 3)
        self.assertEqual(result["count_errors"], 0)
        self.assertEqual(result["overall_strict"], 1.0)
        self.assertEqual(result["overall_narration_forgiven"], 1.0)

        # Per-field breakdown has all 7 fields.
        self.assertEqual(len(result["per_field"]), 7)

        # Per-bank breakdown has HDFC.
        self.assertIn("HDFC", result["per_bank"])
        self.assertEqual(result["per_bank"]["HDFC"]["count"], 3)

        # All 3 runs tagged as judged.
        judged_runs = {rid for k, v, rid in self.fake_mlflow.set_tags if k == "judged"}
        self.assertEqual(judged_runs, {"run-1", "run-2", "run-3"})

    def test_handles_errors_gracefully(self):
        """A failing score_trace is captured as an error, not a crash."""
        from judge.scorer import run_judge_evaluation

        self.fake_mlflow.set_search_runs_result(["run-ok", "run-bad"])

        # Register artifacts (shared across runs since the fake is global).
        self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4 fake")
        self.fake_mlflow.artifacts.register(
            "extraction.json",
            json.dumps(_make_extraction_meta(request_id="req-ok")).encode(),
        )

        # Use a call counter: the second judge call fails.
        call_count = [0]

        def fake_judge(request, extraction):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("opus failure")
            return _make_verdict(request_id="req-ok")

        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.side_effect = fake_judge
            result = run_judge_evaluation(sample_size=2)

        # One scored, one error.
        self.assertEqual(result["count_judged"], 1)
        self.assertEqual(result["count_errors"], 1)
        self.assertEqual(len(result["errors"]), 1)


# ---------------------------------------------------------------------------
# _aggregate_results tests
# ---------------------------------------------------------------------------

class AggregateResultsTest(unittest.TestCase):
    def test_empty_results(self):
        from judge.scorer import _aggregate_results
        result = _aggregate_results([])
        self.assertEqual(result["count_judged"], 0)
        self.assertIsNone(result["overall_strict"])

    def test_mixed_ok_and_error(self):
        from judge.scorer import _aggregate_results
        results = [
            {"run_id": "r1", "status": "OK", "bank": "HDFC",
             "strict_accuracy": 0.8, "narration_forgiven_accuracy": 0.9,
             "per_field": {"rewards_closingPoints": 1.0}},
            {"run_id": "r2", "status": "ERROR", "error": "boom"},
        ]
        result = _aggregate_results(results)
        self.assertEqual(result["count_judged"], 1)
        self.assertEqual(result["count_errors"], 1)
        self.assertEqual(result["overall_strict"], 0.8)
        self.assertEqual(result["overall_narration_forgiven"], 0.9)
        self.assertEqual(result["per_bank"]["HDFC"]["count"], 1)
        self.assertEqual(result["per_field"]["rewards.closingPoints"]["accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
