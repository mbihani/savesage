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
from unittest.mock import Mock, patch

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
    """Fake pandas-like DataFrame for search_runs results.

    Supports the ``run_id`` column and an optional ``tags.judged`` column
    so the Python-side tag filtering in ``run_judge_evaluation`` can be tested.
    """

    def __init__(self, run_ids: list[str], judged_tags: dict[str, str] | None = None):
        self._run_ids = run_ids
        self._judged_tags = judged_tags or {}
        self.empty = len(run_ids) == 0
        # Expose columns so the scorer's ``if col in runs_df.columns`` check works.
        self.columns = ["run_id"]
        if self._judged_tags:
            self.columns.append("tags.judged")

    def __getitem__(self, key):
        if key == "run_id":
            return _FakeSeries(self._run_ids)
        if key == "tags.judged":
            return _FakeSeries([self._judged_tags.get(rid) for rid in self._run_ids])
        return _FakeSeries([])


class _FakeMlflowClient:
    """Fake MlflowClient — delegates to the parent fake module's bookkeeping.

    The real MlflowClient.log_metric / .set_tag take ``run_id`` as the first
    positional argument (unlike the module-level mlflow.log_metric which takes
    it as a keyword).  This fake translates the client API to the same
    (key, value, run_id) tuples the test assertions already check.
    """

    def __init__(self, parent: "_FakeMLflowModule"):
        self._parent = parent

    def log_metric(self, run_id, key, value):
        self._parent.logged_metrics.append((key, value, run_id))

    def set_tag(self, run_id, key, value):
        self._parent.set_tags.append((key, value, run_id))

    def log_dict(self, run_id, dictionary, artifact_file_path=None):
        self._parent.logged_dicts.append((run_id, dictionary, artifact_file_path))


class _FakeTrackingModule:
    """Fake mlflow.tracking submodule so 'from mlflow.tracking import MlflowClient' works."""

    def __init__(self, parent: "_FakeMLflowModule"):
        self.MlflowClient = lambda: _FakeMlflowClient(parent)


class _FakeMLflowModule:
    """Fake mlflow module for score_trace / run_judge_evaluation tests."""

    def __init__(self):
        self.artifacts = _FakeArtifacts()
        self.logged_metrics: list[tuple] = []  # (key, value, run_id)
        self.set_tags: list[tuple] = []  # (key, value, run_id)
        self.logged_dicts: list[tuple] = []  # (run_id, dictionary, artifact_file_path)
        self._experiment = _FakeExperiment()
        self._search_runs_result = _FakeRunsFrame([])
        self.tracking = _FakeTrackingModule(self)

    def set_tracking_uri(self, uri):
        """No-op — the scorer calls this to ensure databricks tracking."""
        pass

    def log_metric(self, key, value, run_id=None):
        self.logged_metrics.append((key, value, run_id))

    def set_tag(self, key, value, run_id=None):
        self.set_tags.append((key, value, run_id))

    def get_experiment_by_name(self, name):
        return self._experiment

    def search_runs(self, experiment_ids=None, filter_string=None,
                    max_results=100, order_by=None, **kwargs):
        return self._search_runs_result

    def set_search_runs_result(self, run_ids: list[str],
                               judged_tags: dict[str, str] | None = None):
        self._search_runs_result = _FakeRunsFrame(run_ids, judged_tags)


def _install_fake_mlflow():
    """Insert a fake mlflow module into sys.modules; return it."""
    fake = _FakeMLflowModule()
    sys.modules["mlflow"] = fake
    sys.modules["mlflow.tracking"] = fake.tracking
    return fake


def _uninstall_fake_mlflow():
    sys.modules.pop("mlflow", None)
    sys.modules.pop("mlflow.tracking", None)


# ---------------------------------------------------------------------------
# Fake ResultStore — records save_verdict calls; optionally raises to test
# the best-effort guarantee that a Lakebase write failure never aborts the run.
# ---------------------------------------------------------------------------

class _FakeResultStore:
    def __init__(self, raise_on_save: bool = False):
        self.saved: list = []
        self.raise_on_save = raise_on_save

    def save_verdict(self, verdict) -> None:
        if self.raise_on_save:
            raise RuntimeError("lakebase write failed")
        self.saved.append(verdict)


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
        self.assertEqual(result["error"], "RuntimeError")
        self.assertNotIn("opus 500", result["error"])

    def test_score_trace_sanitizes_known_error_categories(self):
        """Known failures expose only safe categories, never exception details."""
        from judge.scorer import _sanitize_error

        self.assertEqual(_sanitize_error(TimeoutError("secret host")), "network error")
        self.assertEqual(
            _sanitize_error(RuntimeError("permission denied for account 123")),
            "authentication error",
        )
        self.assertEqual(
            _sanitize_error(RuntimeError("private resource not found")),
            "resource not found",
        )

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

    def test_score_trace_judge_error_tags_error_not_true(self):
        """A JUDGE_ERROR verdict is tagged judged=error (not judged=true) so it can be retried."""
        from judge.scorer import score_trace

        self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4")
        self.fake_mlflow.artifacts.register(
            "extraction.json", json.dumps(_make_extraction_meta()).encode()
        )

        # A JUDGE_ERROR verdict — Opus returned an unusable response.
        judge_error_verdict = JudgeVerdict(
            request_id="req-test",
            judge_model_id="databricks-claude-opus-5",
            comparisons=(
                FieldComparison(
                    "cards[].cardMeta.cardDisplayName", "Platinum", "???",
                    ComparisonOutcome.DISAGREE, FieldScope.SCALAR, card_index=0,
                ),
            ),
            latency_ms=50.0,
            summary=json.dumps({"status": "JUDGE_ERROR"}),
        )

        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = judge_error_verdict
            result = score_trace("run-judge-err")

        self.assertEqual(result["status"], "JUDGE_ERROR")
        # Tagged judged=error, NOT judged=true — so it stays retriable.
        self.assertIn(("judged", "error", "run-judge-err"), self.fake_mlflow.set_tags)
        self.assertNotIn(("judged", "true", "run-judge-err"), self.fake_mlflow.set_tags)


# ---------------------------------------------------------------------------
# run_judge_evaluation tests
# ---------------------------------------------------------------------------

class RunJudgeEvaluationTest(unittest.TestCase):
    def setUp(self):
        self.fake_mlflow = _install_fake_mlflow()
        # Reset the module-level config flag so _ensure_mlflow_configured runs
        # fresh for each test (the env-var handling should be exercised every time).
        import judge.scorer as scorer_mod
        scorer_mod._mlflow_configured = False

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
        # Empty-result early return still carries the eval_run_id key.
        self.assertIsNone(result["eval_run_id"])

    def test_experiment_not_found_returns_error(self):
        """When the experiment doesn't exist, returns an error summary."""
        from judge.scorer import run_judge_evaluation

        self.fake_mlflow._experiment = None
        result = run_judge_evaluation(sample_size=5)

        self.assertEqual(result["count_judged"], 0)
        self.assertGreater(len(result["errors"]), 0)

    def test_experiment_not_found_reports_custom_path(self):
        """The error identifies the configured path that was actually searched."""
        from judge.scorer import run_judge_evaluation

        self.fake_mlflow._experiment = None
        with patch.dict(os.environ, {"MLFLOW_EXPERIMENT_PATH": "/custom/path"}, clear=False):
            result = run_judge_evaluation(sample_size=5)

        self.assertEqual(
            result["errors"], [{"error": "experiment not found: /custom/path"}]
        )

    def test_tracking_uri_failure_is_retried(self):
        """A failed tracking URI setup must not permanently mark configuration done."""
        import judge.scorer as scorer_mod

        self.fake_mlflow.set_tracking_uri = Mock(
            side_effect=[RuntimeError("temporary failure"), None]
        )
        scorer_mod._ensure_mlflow_configured(self.fake_mlflow)
        self.assertFalse(scorer_mod._mlflow_configured)

        scorer_mod._ensure_mlflow_configured(self.fake_mlflow)
        self.assertTrue(scorer_mod._mlflow_configured)
        self.assertEqual(self.fake_mlflow.set_tracking_uri.call_count, 2)

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

        # The summary carries an eval_run_id key.  With the fake mlflow (no
        # mlflow.models.evaluate), run_mlflow_evaluation degrades gracefully
        # to None — the per-trace results still return.  The real-mlflow
        # end-to-end behaviour is covered by tests/test_evaluator.py.
        self.assertIn("eval_run_id", result)
        self.assertIsNone(result["eval_run_id"])

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

    def test_tag_filtering_excludes_judged_true_includes_others(self):
        """run_judge_evaluation searches all runs, then filters in Python:
        includes runs with no tag and judged=error, excludes judged=true.
        """
        from judge.scorer import run_judge_evaluation

        # 4 runs: no-tag, judged=true, judged=error, no-tag.
        judged_tags = {
            "run-notag-1": None,
            "run-judged-true": "true",
            "run-judged-err": "error",
            "run-notag-2": None,
        }
        self.fake_mlflow.set_search_runs_result(
            list(judged_tags.keys()), judged_tags
        )

        # Register artifacts (shared globally since the fake is global).
        self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4 fake")
        self.fake_mlflow.artifacts.register(
            "extraction.json",
            json.dumps(_make_extraction_meta(request_id="req-test")).encode(),
        )

        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = _make_verdict()
            # sample_size=10 should cover all 3 unjudged runs (not the judged=true one).
            result = run_judge_evaluation(sample_size=10)

        # 3 unjudged runs scored (the judged=true run was excluded).
        self.assertEqual(result["count_judged"], 3)

        # The judged=true run was NOT re-scored (not in the tags).
        re_tagged_true = [
            (k, v, rid) for k, v, rid in self.fake_mlflow.set_tags
            if rid == "run-judged-true"
        ]
        self.assertEqual(re_tagged_true, [])


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

    def test_judge_error_counted_as_error(self):
        """JUDGE_ERROR (Opus returned unusable response) is counted as an error."""
        from judge.scorer import _aggregate_results
        results = [
            {"run_id": "r1", "status": "OK", "bank": "HDFC",
             "strict_accuracy": 1.0, "narration_forgiven_accuracy": 1.0,
             "per_field": {}},
            {"run_id": "r2", "status": "JUDGE_ERROR"},
        ]
        result = _aggregate_results(results)
        self.assertEqual(result["count_judged"], 1)
        self.assertEqual(result["count_errors"], 1)
        # The JUDGE_ERROR entry is in the errors list with its status.
        self.assertEqual(result["errors"][0]["status"], "JUDGE_ERROR")
        self.assertIn("JUDGE_ERROR", result["errors"][0]["error"])


# ---------------------------------------------------------------------------
# Verdict persistence to Lakebase (inline-on-Results-view wiring)
# ---------------------------------------------------------------------------

class VerdictPersistTest(unittest.TestCase):
    """The scorer persists each OK verdict to Lakebase keyed by request_id so
    ``GET /api/results`` can surface expected/actual/outcome inline.

    (a) save_verdict is called on OK (request_id-keyed) and NOT on JUDGE_ERROR.
    (b) a save_verdict that raises is caught — the judge run and the aggregate
        still complete (best-effort guarantee).
    """

    def setUp(self):
        self.fake_mlflow = _install_fake_mlflow()
        # Reset the module-level result-store cache so a prior test's lazy
        # build (which returns None without env vars) doesn't leak in.
        import judge.scorer as scorer_mod
        scorer_mod._result_store_init_done = False
        scorer_mod._result_store = None

    def tearDown(self):
        _uninstall_fake_mlflow()

    def _register_artifacts(self, meta=None):
        meta = meta or _make_extraction_meta()
        self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4 fake")
        self.fake_mlflow.artifacts.register("extraction.json", json.dumps(meta).encode())
        return meta

    def test_save_verdict_called_on_ok_keyed_by_request_id(self):
        """On an OK verdict, save_verdict receives a verdict whose request_id
        matches the extraction meta — so /api/results keyed by the same
        request_id finds it."""
        from judge.scorer import score_trace
        meta = self._register_artifacts()
        store = _FakeResultStore()
        verdict = _make_verdict(request_id=meta["request_id"])
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = verdict
            result = score_trace("run-123", result_store=store)

        self.assertEqual(result["status"], "OK")
        self.assertEqual(len(store.saved), 1)
        # The persisted verdict is keyed by request_id (the store upserts on
        # verdict.request_id); it must equal the extraction's request_id so
        # /api/results/{request_id} reads it from the same row.
        self.assertEqual(store.saved[0].request_id, meta["request_id"])

    def test_save_verdict_not_called_on_judge_error(self):
        """A JUDGE_ERROR verdict is not persisted — it carries no usable
        ground truth (Opus failed to read the PDF)."""
        from judge.scorer import score_trace
        self._register_artifacts()
        store = _FakeResultStore()
        judge_error_verdict = JudgeVerdict(
            request_id="req-test", judge_model_id="databricks-claude-opus-5",
            comparisons=(FieldComparison(
                "cards[].cardMeta.cardDisplayName", "Platinum", "???",
                ComparisonOutcome.DISAGREE, FieldScope.SCALAR, card_index=0,
            ),),
            latency_ms=50.0, summary=json.dumps({"status": "JUDGE_ERROR"}),
        )
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = judge_error_verdict
            result = score_trace("run-err", result_store=store)

        self.assertEqual(result["status"], "JUDGE_ERROR")
        self.assertEqual(store.saved, [])

    def test_save_verdict_failure_does_not_crash_score_trace(self):
        """A save_verdict that raises is caught; the trace still completes OK
        and its metrics are returned (best-effort guarantee)."""
        from judge.scorer import score_trace
        self._register_artifacts()
        store = _FakeResultStore(raise_on_save=True)
        verdict = _make_verdict(request_id="req-test")
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = verdict
            result = score_trace("run-123", result_store=store)

        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["strict_accuracy"], 1.0)
        # Metrics were still logged to MLflow despite the Lakebase failure.
        run_metrics = [(k, v) for k, v, rid in self.fake_mlflow.logged_metrics
                       if rid == "run-123"]
        self.assertIn(("judge.accuracy", 1.0), run_metrics)

    def test_save_verdict_failure_does_not_crash_aggregate(self):
        """A raising save_verdict must not abort run_judge_evaluation; the
        aggregate still returns with the trace counted as judged."""
        from judge.scorer import run_judge_evaluation
        self.fake_mlflow.set_search_runs_result(["run-1"])
        meta = _make_extraction_meta(request_id="req-1")
        self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4 fake")
        self.fake_mlflow.artifacts.register("extraction.json", json.dumps(meta).encode())
        store = _FakeResultStore(raise_on_save=True)
        verdict = _make_verdict(request_id="req-1")
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = verdict
            result = run_judge_evaluation(sample_size=1, result_store=store)

        self.assertEqual(result["count_judged"], 1)
        self.assertEqual(result["count_errors"], 0)

    def test_comparisons_logged_to_mlflow_on_ok(self):
        """The per-field expected/actual/outcome comparisons are logged as a
        JSON artifact on the run (secondary, best-effort trace visibility)."""
        from judge.scorer import score_trace
        self._register_artifacts()
        store = _FakeResultStore()
        verdict = _make_verdict(request_id="req-test")
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = verdict
            score_trace("run-123", result_store=store)

        # log_dict captured: (run_id, dictionary, artifact_file_path)
        logged = [(rid, d, p) for rid, d, p in self.fake_mlflow.logged_dicts
                  if rid == "run-123"]
        self.assertEqual(len(logged), 1)
        _, body, path = logged[0]
        self.assertEqual(path, "verdict_comparisons.json")
        self.assertEqual(len(body), len(verdict.comparisons))
        self.assertEqual(body[0]["field_path"], "cards[].cardMeta.cardDisplayName")
        self.assertEqual(body[0]["outcome"], "AGREE")


# ---------------------------------------------------------------------------
# resolve_run_id — request_id → MLflow run_id via the request_id tag
# ---------------------------------------------------------------------------

class ResolveRunIdTest(unittest.TestCase):
    """The on-demand single-trace judge resolves request_id → run_id via an
    MLflow tag-equality filter on the ``request_id`` tag the tracing sink sets.
    Returns None cleanly when no run is found (the endpoint maps that to 404).
    """

    def setUp(self):
        self.fake_mlflow = _install_fake_mlflow()
        import judge.scorer as scorer_mod
        scorer_mod._mlflow_configured = False

    def tearDown(self):
        _uninstall_fake_mlflow()

    def test_resolves_request_id_to_run_id(self):
        """When a run tagged with request_id exists, returns its run_id."""
        from judge.scorer import resolve_run_id
        self.fake_mlflow.set_search_runs_result(["run-abc"])
        run_id = resolve_run_id("req-123")
        self.assertEqual(run_id, "run-abc")

    def test_returns_none_when_no_run_found(self):
        """When no run matches the request_id tag, returns None (→ endpoint 404)."""
        from judge.scorer import resolve_run_id
        self.fake_mlflow.set_search_runs_result([])
        run_id = resolve_run_id("req-missing")
        self.assertIsNone(run_id)

    def test_returns_none_when_experiment_not_found(self):
        """When the experiment is unreachable, returns None (→ 404, never 500)."""
        from judge.scorer import resolve_run_id
        self.fake_mlflow._experiment = None
        run_id = resolve_run_id("req-orphan")
        self.assertIsNone(run_id)

    def test_search_failure_returns_none_not_raise(self):
        """An MLflow search exception is caught; returns None (→ 404, never 500)."""
        from judge.scorer import resolve_run_id
        self.fake_mlflow.search_runs = Mock(
            side_effect=RuntimeError("mlflow internal error")
        )
        run_id = resolve_run_id("req-boom")
        self.assertIsNone(run_id)


if __name__ == "__main__":
    unittest.main()
