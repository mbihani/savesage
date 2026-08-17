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
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
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


class _FakePagedList(list):
    """Fake ``PagedList`` — a list subclass with a ``.token`` continuation.

    Models ``MlflowClient.search_traces`` which returns a
    ``PagedList[Trace]`` whose ``.token`` is the next-page token (falsy/None
    when exhausted).  The production code reads it via ``getattr(traces,
    "token", None)`` and breaks the loop when it is falsy.
    """

    def __init__(self, items=(), token=None):
        super().__init__(items)
        self.token = token


class _FakeDataFrameRow:
    """Fake pandas ``Series`` — supports ``.get('trace_metadata')``.

    Models the row shape returned by ``DataFrame.iterrows()`` in the pandas
    path of ``_iter_trace_metadata`` (production ``mlflow.search_traces``
    returns a DataFrame whose ``trace_metadata`` column holds the
    ``info.request_metadata`` dict per row).
    """

    def __init__(self, trace_metadata: dict | None):
        self._tm = trace_metadata

    def get(self, key, default=None):
        if key == "trace_metadata":
            return self._tm
        return default


class _FakeDataFrame:
    """Fake pandas ``DataFrame`` — supports ``iterrows()`` yielding rows with
    a ``trace_metadata`` column.  Models the production return shape of the
    module-level ``mlflow.search_traces`` (used to exercise the DataFrame
    branch of ``_iter_trace_metadata`` in a regression test)."""

    def __init__(self, rows: list[dict | None]):
        self._rows = [_FakeDataFrameRow(tm) for tm in rows]

    def iterrows(self):
        for i, row in enumerate(self._rows):
            yield i, row


# The run-tag fast-path filter resolve_run_id builds; the fake search_runs
# honours it so the resolution is actually exercised (the old fake ignored
# filter_string, making the resolve test vacuous).
_REQUEST_ID_FILTER_RE = re.compile(r"^tags\.request_id = '([^']*)'$")


def _make_fake_trace(request_id: str, source_run: str, *, name: str = "parse") -> Any:
    """Build a Trace-like object modelling a real parse trace.

    The real ``mlflow.search_traces`` returns a DataFrame whose
    ``trace_metadata`` column maps to ``trace.info.request_metadata``. This
    fake returns a Trace-like object (``SimpleNamespace``) whose
    ``.info.request_metadata`` carries the two keys resolve_run_id reads:

    * ``mlflow.traceInputs`` — JSON of the root-span inputs
      (``{"request_id": "req-…", "bank": …}``); present on EVERY parse trace.
    * ``mlflow.sourceRun`` — the backing run_id (the run score_trace
      downloads statement.pdf / extraction.json from).
    """
    return SimpleNamespace(
        info=SimpleNamespace(
            request_metadata={
                "mlflow.traceInputs": json.dumps({
                    "request_id": request_id,
                    "bank": "HDFC",
                    "filename": "[REDACTED]",
                }),
                "mlflow.sourceRun": source_run,
                "mlflow.traceName": name,
            },
            trace_id=f"tr-{request_id}",
        ),
    )


class _FakeMlflowClient:
    """Fake MlflowClient — delegates to the parent fake module's bookkeeping.

    The real MlflowClient.log_metric / .set_tag take ``run_id`` as the first
    positional argument (unlike the module-level mlflow.log_metric which takes
    it as a keyword).  This fake translates the client API to the same
    (key, value, run_id) tuples the test assertions already check.

    ``search_traces`` models the paginated ``MlflowClient.search_traces``
    (returns a ``PagedList[Trace]`` with a ``.token`` continuation).  The
    parent's ``_traces`` list is sliced into pages of ``max_results`` using
    the ``page_token`` as a stringified start index, so multi-page scans are
    genuinely exercised (not returned in one shot).
    """

    def __init__(self, parent: "_FakeMLflowModule"):
        self._parent = parent

    def log_metric(self, run_id, key, value):
        self._parent.logged_metrics.append((key, value, run_id))

    def set_tag(self, run_id, key, value):
        self._parent.set_tags.append((key, value, run_id))

    def log_dict(self, run_id, dictionary, artifact_file_path=None):
        self._parent.logged_dicts.append((run_id, dictionary, artifact_file_path))

    def search_traces(self, experiment_ids=None, filter_string=None,
                      max_results=100, order_by=None, page_token=None,
                      run_id=None, include_spans=True, model_id=None,
                      **kwargs):
        self._parent._trace_search_calls += 1
        if self._parent._trace_search_raises:
            raise RuntimeError("trace search error")
        traces = list(self._parent._traces)
        start = int(page_token) if page_token else 0
        page = traces[start:start + max_results]
        next_start = start + len(page)
        token = str(next_start) if next_start < len(traces) else None
        return _FakePagedList(page, token)


class _FakeTrackingModule:
    """Fake mlflow.tracking submodule so 'from mlflow.tracking import MlflowClient' works."""

    def __init__(self, parent: "_FakeMLflowModule"):
        self.MlflowClient = lambda: _FakeMlflowClient(parent)


class _FakeScorer:
    """Fake ``@mlflow.genai.scorer`` — wraps the scorer function so the fake
    ``genai.evaluate`` can drive it once per row via ``.run(trace=...)``,
    mirroring ``mlflow.genai.scorers.base.Scorer.run``."""

    def __init__(self, func, name=None, description=None, aggregations=None):
        self._func = func
        self.name = name or func.__name__
        self.description = description
        self.aggregations = aggregations

    def __call__(self, **kwargs):
        return self._func(**kwargs)

    def run(self, *, inputs=None, outputs=None, expectations=None,
            trace=None, session=None):
        import inspect as _inspect

        sig = _inspect.signature(self._func)
        merged = {"inputs": inputs, "outputs": outputs,
                  "expectations": expectations, "trace": trace, "session": session}
        filtered = {k: v for k, v in merged.items() if k in sig.parameters}
        return self._func(**filtered)


def _fake_scorer_decorator(func=None, *, name=None, description=None,
                           aggregations=None):
    """Fake ``mlflow.genai.scorers.scorer`` decorator — returns a _FakeScorer."""
    if func is None:
        def _decorator(f):
            return _FakeScorer(f, name=name, description=description,
                               aggregations=aggregations)
        return _decorator
    return _FakeScorer(func, name=name, description=description,
                       aggregations=aggregations)


class _FakeEvaluationResult:
    """Fake ``mlflow.genai.evaluation.entities.EvaluationResult``."""

    def __init__(self, run_id, metrics=None, result_df=None):
        self.run_id = run_id
        self.metrics = metrics or {}
        self.result_df = result_df


class _FakeGenaiScorersModule:
    """Fake ``mlflow.genai.scorers`` submodule — exposes ``scorer``."""

    def __init__(self):
        self.scorer = _fake_scorer_decorator


class _FakeGenaiModule:
    """Fake ``mlflow.genai`` module — exposes ``evaluate`` + ``scorers``.

    ``evaluate`` ASSERTS scorers were passed (non-empty), records the call,
    drives the scorer once per row (calling ``scorer.run(trace=row["trace"])``
    so the full ``_judge_and_persist`` pipeline runs through the fake mlflow),
    and returns a ``_FakeEvaluationResult``.  This mirrors the production
    ``genai.evaluate`` mode-1 (trace-column dataset, ``predict_fn=None``).
    """

    def __init__(self, parent: "_FakeMLflowModule"):
        self._parent = parent
        self.scorers = _FakeGenaiScorersModule()
        self.evaluate_calls: int = 0
        self.scorers_passed: list = []
        self.assessments: list = []  # all Feedback objects returned
        # When set, raise RuntimeError AFTER processing this many rows —
        # simulates a partial genai.evaluate failure (some scorers
        # complete, then the run raises).  None = no mid-run failure.
        self.fail_after_n_rows: int | None = None

    def evaluate(self, data, scorers, predict_fn=None, model_id=None, **kw):
        # ASSERT scorers were passed (the key contract — one scorer, not zero).
        assert scorers, "genai.evaluate requires at least one scorer"
        self.evaluate_calls += 1
        self.scorers_passed = list(scorers)
        # Drive the scorer once per row, collecting returned Feedbacks.
        for i, row in enumerate(data):
            # Simulate a partial failure: after fail_after_n_rows scorers
            # complete, raise mid-run.  The completed results are already in
            # the scorer's results_collector (the side-channel); the caller
            # must skip those run_ids in the fallback.
            if (self.fail_after_n_rows is not None
                    and i >= self.fail_after_n_rows):
                raise RuntimeError(
                    f"genai.evaluate failed mid-run after {i} scorer(s)"
                )
            trace = row.get("trace")
            for scorer in scorers:
                result = scorer.run(trace=trace)
                if isinstance(result, list):
                    self.assessments.extend(result)
        return _FakeEvaluationResult(run_id="fake-eval-run-1")


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
        # request_id -> run_id for runs that carry the request_id RUN tag
        # (the fast path). Empty by default — most real parse runs do NOT
        # carry the tag (the live root cause), so the trace fallback is the
        # path that actually resolves them.
        self._request_id_runs: dict[str, str] = {}
        # Trace-like objects (see _make_fake_trace) for the trace-based fallback.
        self._traces: list = []
        # How many times MlflowClient.search_traces was called (paginated
        # scan).  Tests assert on this to prove pagination is followed and
        # the global bound terminates the loop.
        self._trace_search_calls: int = 0
        # When True, _FakeMlflowClient.search_traces raises (failure test).
        self._trace_search_raises: bool = False
        # Run IDs yielded by start_run (the genai.evaluate run context).
        self._started_runs: list[str] = []
        # Fake genai module (evaluate + scorer) for the genai.evaluate path.
        self.genai = _FakeGenaiModule(self)
        # Assessments logged via mlflow.log_assessment (the on-demand path's
        # per-field assessment writer).  Each entry is (trace_id, assessment).
        self.logged_assessments: list[tuple] = []
        # When True, mlflow.log_assessment raises (Bug 3 invariant test: an
        # assessment-write failure must leave the run re-judgeable, not OK).
        self._log_assessment_raises: bool = False

    def set_tracking_uri(self, uri):
        """No-op — the scorer calls this to ensure databricks tracking."""
        pass

    def log_metric(self, key, value, run_id=None):
        self.logged_metrics.append((key, value, run_id))

    def set_tag(self, key, value, run_id=None):
        self.set_tags.append((key, value, run_id))

    def get_experiment_by_name(self, name):
        return self._experiment

    def start_run(self, experiment_id=None, run_name=None, **kwargs):
        """Fake ``mlflow.start_run`` — a context manager yielding a run with
        ``info.run_id``.  ``run_genai_evaluation`` wraps the ``genai.evaluate``
        call in this so genai.evaluate reuses the active run."""
        from contextlib import contextmanager

        run_id = f"fake-eval-run-{len(self._started_runs) + 1}"
        self._started_runs.append(run_id)

        @contextmanager
        def _ctx():
            try:
                yield SimpleNamespace(info=SimpleNamespace(run_id=run_id))
            finally:
                pass

        return _ctx()

    def log_assessment(self, trace_id, assessment):
        """Fake ``mlflow.log_assessment`` — capture (trace_id, assessment).

        The on-demand path (:func:`judge.scorer._log_field_assessments`) logs
        the 7 per-field + 2 overall ``judge.<field>`` Feedbacks onto the
        original parse trace via this.  Captured so tests can assert the
        assessments landed on the right trace_id.  Raises when
        ``_log_assessment_raises`` is set (Bug 3 invariant test: an
        assessment-write failure must leave the run re-judgeable).
        """
        if self._log_assessment_raises:
            raise RuntimeError("log_assessment failed (fake)")
        self.logged_assessments.append((trace_id, assessment))

    def search_runs(self, experiment_ids=None, filter_string=None,
                    max_results=100, order_by=None, **kwargs):
        # Honour the resolve_run_id fast-path filter so the resolution is
        # actually exercised. Only the ``tags.request_id = '<id>'`` equality
        # filter is modelled; the batch sampler calls with no filter_string
        # and still gets the set result (preserving existing batch tests).
        if filter_string:
            m = _REQUEST_ID_FILTER_RE.match(filter_string)
            if m:
                rid = m.group(1)
                run_id = self._request_id_runs.get(rid)
                return _FakeRunsFrame([run_id] if run_id else [])
        return self._search_runs_result

    def search_traces(self, experiment_ids=None, filter_string=None,
                      max_results=100, order_by=None, run_id=None,
                      return_type=None, **kwargs):
        # Return the registered trace-like objects. When ``run_id`` is
        # provided (the _resolve_trace_for_run path), filter traces whose
        # ``mlflow.sourceRun`` metadata matches, so only that run's trace is
        # returned — mirroring production ``mlflow.search_traces(run_id=...)``.
        traces = list(self._traces)
        if run_id is not None:
            filtered = []
            for t in traces:
                tmeta = (
                    getattr(getattr(t, "info", None), "request_metadata", None)
                    or {}
                )
                if tmeta.get("mlflow.sourceRun") == run_id:
                    filtered.append(t)
            traces = filtered
        return traces

    def set_search_runs_result(self, run_ids: list[str],
                               judged_tags: dict[str, str] | None = None):
        self._search_runs_result = _FakeRunsFrame(run_ids, judged_tags)

    def set_request_id_tag(self, run_id: str, request_id: str) -> None:
        """Register a run carrying the request_id RUN tag (fast-path hit)."""
        self._request_id_runs[request_id] = run_id

    def set_traces(self, traces: list) -> None:
        """Register trace-like objects for the trace-based fallback."""
        self._traces = list(traces)

    def set_trace_search_raises(self, raises: bool = True) -> None:
        """Make _FakeMlflowClient.search_traces raise on the next call(s)."""
        self._trace_search_raises = raises

    def set_log_assessment_raises(self, raises: bool = True) -> None:
        """Make mlflow.log_assessment raise (Bug 3 invariant test: an
        assessment-write failure must leave the run re-judgeable, not OK)."""
        self._log_assessment_raises = raises


# ---------------------------------------------------------------------------
# Fake mlflow.entities module (Feedback, AssessmentSource)
#
# build_field_feedbacks (judge/evaluator.py) does:
#   from mlflow.entities import AssessmentSource, Feedback
# When the fake mlflow is active, sys.modules["mlflow"] is a _FakeMLflowModule
# (not a real package), so the submodule import fails unless we register
# mlflow.entities ourselves.  The fake classes mirror the real constructor
# signatures (verified against mlflow 3.10.1) so the scorer can build Feedback
# objects while the tracking/genai APIs are faked.
# ---------------------------------------------------------------------------


class _FakeAssessmentSource:
    """Fake ``mlflow.entities.AssessmentSource``."""

    def __init__(self, source_type: str, source_id: str = "default") -> None:
        self.source_type = source_type
        self.source_id = source_id


class _FakeFeedback:
    """Fake ``mlflow.entities.assessment.Feedback``.

    Mirrors the real constructor signature (mlflow 3.10.1): raises if both
    ``value`` and ``error`` are ``None``.  Stores all attributes the tests
    inspect (``name``, ``value``, ``source``, ``rationale``, ``metadata``).
    """

    def __init__(
        self,
        name: str = "feedback",
        value: Any = None,
        error: Any = None,
        source: Any = None,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        span_id: str | None = None,
        create_time_ms: int | None = None,
        last_update_time_ms: int | None = None,
        rationale: str | None = None,
        overrides: str | None = None,
        valid: bool = True,
    ) -> None:
        if value is None and error is None:
            raise ValueError(
                "Either value or error must be provided for a Feedback."
            )
        self.name = name
        self.value = value
        self.error = error
        self.source = source or _FakeAssessmentSource("CODE")
        self.trace_id = trace_id
        self.metadata = metadata
        self.span_id = span_id
        self.rationale = rationale
        self.overrides = overrides
        self.valid = valid


class _FakeEntitiesModule:
    """Fake ``mlflow.entities`` submodule — exposes Feedback + AssessmentSource."""

    AssessmentSource = _FakeAssessmentSource
    Feedback = _FakeFeedback


def _install_fake_mlflow():
    """Insert a fake mlflow module into sys.modules; return it."""
    fake = _FakeMLflowModule()
    sys.modules["mlflow"] = fake
    sys.modules["mlflow.tracking"] = fake.tracking
    sys.modules["mlflow.genai"] = fake.genai
    sys.modules["mlflow.genai.scorers"] = fake.genai.scorers
    sys.modules["mlflow.entities"] = _FakeEntitiesModule()
    return fake


def _uninstall_fake_mlflow():
    sys.modules.pop("mlflow", None)
    sys.modules.pop("mlflow.tracking", None)
    sys.modules.pop("mlflow.genai", None)
    sys.modules.pop("mlflow.genai.scorers", None)
    sys.modules.pop("mlflow.entities", None)


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
        """Full score_trace: download PDF + extraction → opus → metrics →
        assessments → tag.  The on-demand path now requires a resolvable
        parse trace to log the 9 assessments onto — without one the run is
        left re-judgeable (judged=error), not judged=true (Bug 3 invariant).
        So the full-flow happy path registers a trace and asserts judged=true
        + the 9 assessments actually persisted."""
        from judge.scorer import score_trace

        # Register artifacts on the fake mlflow.
        meta = _make_extraction_meta()
        self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4 fake pdf")
        self.fake_mlflow.artifacts.register("extraction.json", json.dumps(meta).encode())
        # Register the parse trace linked to this run so the 9 assessments
        # land on its trace_id and the run reaches judged=true.
        self.fake_mlflow.set_traces([_make_fake_trace("req-test", "run-123")])

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
        # Register a trace so the run reaches judged=true (the per-field
        # metrics are independent of assessment persistence, but a trace
        # keeps the run on the happy path — see Bug 3 invariant).
        self.fake_mlflow.set_traces([_make_fake_trace("req-test", "run-fields")])

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

    def test_score_trace_logs_per_field_assessments_on_original_trace(self):
        """Bug 2 — the on-demand single-trace path (score_trace) MUST write
        the 7 per-field + 2 overall ``judge.<field>`` assessments onto the
        ORIGINAL parse trace (the one the Results view links to), so an
        on-demand-judged trace ends up with assessments — not just metrics +
        a Lakebase verdict.  Reuses build_field_feedbacks (the SAME builder
        the batch genai.evaluate scorer uses) so there is ONE assessment-
        construction code path; only the persistence differs (direct
        mlflow.log_assessment on the original trace_id here)."""
        from judge.scorer import score_trace

        self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4")
        self.fake_mlflow.artifacts.register(
            "extraction.json", json.dumps(_make_extraction_meta()).encode()
        )
        # Register the parse trace linked to this run (so _resolve_trace_for_run
        # finds it and the assessments land on its trace_id).
        self.fake_mlflow.set_traces([_make_fake_trace("req-test", "run-on-demand")])

        verdict = _make_verdict()
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = verdict
            result = score_trace("run-on-demand")

        self.assertEqual(result["status"], "OK")

        # The 9 assessments (7 per-field + 2 overall) were logged via
        # mlflow.log_assessment onto the original parse trace_id.
        logged = self.fake_mlflow.logged_assessments
        self.assertEqual(len(logged), 9)
        # Every assessment landed on the original parse trace_id (tr-req-test),
        # not a clone or the eval run.
        for trace_id, _fb in logged:
            self.assertEqual(trace_id, "tr-req-test")
        # The 7 per-field assessment names are exactly FIELD_ASSESSMENT_NAMES.
        from judge.evaluator import (
            FIELD_ASSESSMENT_NAMES,
            OVERALL_FORGIVEN_NAME,
            OVERALL_STRICT_NAME,
        )
        names = {fb.name for _tid, fb in logged}
        self.assertEqual(
            names & set(FIELD_ASSESSMENT_NAMES.values()),
            set(FIELD_ASSESSMENT_NAMES.values()),
        )
        self.assertIn(OVERALL_STRICT_NAME, names)
        self.assertIn(OVERALL_FORGIVEN_NAME, names)

    def test_score_trace_assessment_logging_calls_opus_once(self):
        """Bug 2 constraint — the on-demand assessment path does NOT call Opus
        a second time: assessments are built from the SAME verdict the single
        _judge_and_persist Opus call already produced."""
        from judge.scorer import score_trace

        self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4")
        self.fake_mlflow.artifacts.register(
            "extraction.json", json.dumps(_make_extraction_meta()).encode()
        )
        self.fake_mlflow.set_traces([_make_fake_trace("req-test", "run-once")])

        opus_calls = [0]
        verdict = _make_verdict()

        def _counting_judge(request, extraction):
            opus_calls[0] += 1
            return verdict

        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.side_effect = _counting_judge
            score_trace("run-once")

        # Exactly ONE Opus call — the assessment logging reuses its verdict.
        self.assertEqual(opus_calls[0], 1)
        # And the 9 assessments were still logged.
        self.assertEqual(len(self.fake_mlflow.logged_assessments), 9)

    def test_score_trace_still_persists_lakebase_verdict_with_assessments(self):
        """Bug 2 — the existing Lakebase verdict write is preserved alongside
        the new assessment logging (both happen on the on-demand path)."""
        from judge.scorer import score_trace

        self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4")
        self.fake_mlflow.artifacts.register(
            "extraction.json", json.dumps(_make_extraction_meta()).encode()
        )
        self.fake_mlflow.set_traces([_make_fake_trace("req-test", "run-lb")])

        saved = []

        class _FakeStore:
            def save_verdict(self, v):
                saved.append(v)

        verdict = _make_verdict()
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = verdict
            score_trace("run-lb", result_store=_FakeStore())

        # Lakebase verdict persisted (existing behaviour preserved).
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].request_id, "req-test")
        # AND the 9 assessments logged (new behaviour).
        self.assertEqual(len(self.fake_mlflow.logged_assessments), 9)

    def test_score_trace_no_trace_leaves_run_rejudgeable(self):
        """Bug 3 invariant — when the parse run has NO resolvable trace
        (aged out / search unavailable), there is no trace_id to log the 9
        assessments onto.  The run MUST NOT be tagged ``judged=true`` (that
        would permanently strand it: no assessments AND the batch sampler
        skips ``judged=true`` runs forever).  Instead the failure is
        SURFACED — status promoted to ``ASSESSMENT_ERROR``, tag set to
        ``judged=error`` (re-judgeable — the batch sampler keeps runs where
        ``judged != 'true'``), and the error string carried in the result.
        The Lakebase verdict write is still attempted (preserved
        regardless).  This test replaces the earlier wrong invariant that
        accepted ``OK`` with zero assessments when trace resolution failed.
        """
        from judge.scorer import score_trace

        self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4")
        self.fake_mlflow.artifacts.register(
            "extraction.json", json.dumps(_make_extraction_meta()).encode()
        )
        # No trace registered for this run — trace resolution fails.
        self.fake_mlflow.set_traces([])

        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = _make_verdict()
            result = score_trace("run-no-trace")

        # SURFACED, not swallowed: status is ASSESSMENT_ERROR (not OK).
        self.assertEqual(result["status"], "ASSESSMENT_ERROR")
        # The error detail is carried in the result (sanitized — no PII).
        self.assertIn("trace", result.get("error", "").lower())
        # RE-JUDGEABLE: tagged judged=error, NOT judged=true.
        self.assertIn(("judged", "error", "run-no-trace"),
                      self.fake_mlflow.set_tags)
        self.assertNotIn(("judged", "true", "run-no-trace"),
                         self.fake_mlflow.set_tags)
        # No assessments were logged (no trace_id to log onto).
        self.assertEqual(len(self.fake_mlflow.logged_assessments), 0)

    def test_score_trace_assessment_write_failure_leaves_run_rejudgeable(self):
        """Bug 3 invariant — when a ``log_assessment`` call raises (the
        Databricks store rejects an assessment, a transient network error,
        etc.), the run MUST NOT be tagged ``judged=true``.  The failure is
        SURFACED (status ``ASSESSMENT_ERROR``, error in the result) and the
        run is left RE-JUDGEABLE (``judged=error``).  The Lakebase verdict
        write is PRESERVED regardless (it succeeds independently).  Opus is
        still called exactly ONCE.  The assessment writer attempts ALL 9
        even when some fail, so the persist count is as complete as
        possible — but the final status/tag reflects that not all 9
        persisted."""
        from judge.scorer import score_trace

        self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4")
        self.fake_mlflow.artifacts.register(
            "extraction.json", json.dumps(_make_extraction_meta()).encode()
        )
        self.fake_mlflow.set_traces([_make_fake_trace("req-test", "run-fail")])
        # Make every log_assessment call raise.
        self.fake_mlflow.set_log_assessment_raises(True)

        saved = []

        class _FakeStore:
            def save_verdict(self, v):
                saved.append(v)

        opus_calls = [0]
        verdict = _make_verdict()

        def _counting_judge(request, extraction):
            opus_calls[0] += 1
            return verdict

        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.side_effect = _counting_judge
            result = score_trace("run-fail", result_store=_FakeStore())

        # SURFACED, not swallowed: status is ASSESSMENT_ERROR (not OK).
        self.assertEqual(result["status"], "ASSESSMENT_ERROR")
        # The error detail is carried and SANITIZED — the raw fake exception
        # message ("log_assessment failed (fake)") does NOT leak into the
        # surfaced error (no str(exc); only assessment names + counts).
        self.assertTrue(result.get("error"))
        self.assertNotIn("(fake)", result["error"])
        # RE-JUDGEABLE: tagged judged=error, NOT judged=true.
        self.assertIn(("judged", "error", "run-fail"), self.fake_mlflow.set_tags)
        self.assertNotIn(("judged", "true", "run-fail"), self.fake_mlflow.set_tags)
        # Lakebase verdict PRESERVED regardless of assessment failure.
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].request_id, "req-test")
        # Opus called exactly ONCE (assessments are built from the verdict,
        # no second Opus call).
        self.assertEqual(opus_calls[0], 1)
        # Zero assessments persisted (every log_assessment raised).  The
        # writer still attempted all 9 before reporting the aggregate failure.
        self.assertEqual(len(self.fake_mlflow.logged_assessments), 0)

    def test_score_trace_judge_error_logs_no_assessments(self):
        """A JUDGE_ERROR verdict does NOT log per-field assessments — they
        would all be the 'not_scored' sentinel (no usable ground truth), so
        logging them adds noise without signal.  The run is still tagged
        judged=error (retriable) and the verdict is not persisted to Lakebase."""
        from judge.scorer import score_trace

        self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4")
        self.fake_mlflow.artifacts.register(
            "extraction.json", json.dumps(_make_extraction_meta()).encode()
        )
        self.fake_mlflow.set_traces([_make_fake_trace("req-test", "run-jerr")])

        # A JUDGE_ERROR verdict (all ABSENT_IN_PDF sentinels).
        from judge.comparison import judge_error_comparisons
        verdict = JudgeVerdict(
            request_id="req-test",
            judge_model_id="databricks-claude-opus-5",
            comparisons=judge_error_comparisons("opus unusable"),
            latency_ms=50.0,
            summary=json.dumps({"status": "JUDGE_ERROR"}),
        )
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = verdict
            result = score_trace("run-jerr")

        self.assertEqual(result["status"], "JUDGE_ERROR")
        # No assessments logged for JUDGE_ERROR.
        self.assertEqual(len(self.fake_mlflow.logged_assessments), 0)

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

    def test_on_demand_judged_trace_has_both_tag_and_assessments(self):
        """Bug 3 — the intended FINAL STATE of an on-demand-judged trace:
        the run is tagged ``judged=true`` AND its parse trace carries the 9
        per-field + overall assessments.  Before this fix the on-demand path
        tagged the run ``judged=true`` but wrote NO assessments (score_trace
        only wrote metrics + Lakebase verdict), so the batch sampler — which
        skips ``judged=true`` runs — would skip it forever, leaving the trace
        in a 'tagged but assessment-less' state.  With Bug 2's fix the
        on-demand path itself writes the assessments, so the two paths are
        consistent: a ``judged=true`` run always has assessments (either
        written by on-demand directly, or it is not yet judged=true)."""
        from judge.scorer import score_trace

        self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4")
        self.fake_mlflow.artifacts.register(
            "extraction.json", json.dumps(_make_extraction_meta()).encode()
        )
        self.fake_mlflow.set_traces([_make_fake_trace("req-test", "run-final")])

        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = _make_verdict()
            score_trace("run-final")

        # FINAL STATE 1: run tagged judged=true (the on-demand path did this
        # before too — preserved).
        self.assertIn(("judged", "true", "run-final"), self.fake_mlflow.set_tags)
        # FINAL STATE 2: the parse trace carries the 9 assessments (NEW —
        # the on-demand path now writes them, so the run is not left tagged
        # but assessment-less).
        self.assertEqual(len(self.fake_mlflow.logged_assessments), 9)
        for trace_id, _fb in self.fake_mlflow.logged_assessments:
            self.assertEqual(trace_id, "tr-req-test")

    def test_batch_sampler_skips_judged_true_runs(self):
        """Bug 3 — the batch sampler filters OUT ``judged=true`` runs, so an
        on-demand-judged run (which now carries assessments) is correctly
        skipped by the batch (no duplicate Opus call / duplicate assessments).
        This is the existing sampler behaviour; this test pins it as the
        intended consistency contract between the two paths."""
        from judge.scorer import run_judge_evaluation

        # Two runs: one already judged=true (on-demand), one unjudged.
        self.fake_mlflow.set_search_runs_result(
            ["run-judged", "run-fresh"],
            judged_tags={"run-judged": "true", "run-fresh": None},
        )
        # Both runs have artifacts + a trace; the fresh one gets judged.
        self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4")
        self.fake_mlflow.artifacts.register(
            "extraction.json", json.dumps(_make_extraction_meta()).encode(),
        )
        self.fake_mlflow.set_traces([
            _make_fake_trace("req-judged", "run-judged"),
            _make_fake_trace("req-fresh", "run-fresh"),
        ])

        opus_calls = [0]

        def _counting_judge(request, extraction):
            opus_calls[0] += 1
            return _make_verdict(request.request_id)

        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.side_effect = _counting_judge
            run_judge_evaluation(sample_size=10)

        # Only the FRESH run was judged — the judged=true run was skipped
        # (exactly ONE Opus call, not two).  The artifact meta's request_id
        # is shared across runs (artifacts are registered globally in the
        # fake), so we assert the CALL COUNT, not which request_id — what
        # matters is the judged=true run did not trigger a second Opus call
        # or duplicate assessments.
        self.assertEqual(opus_calls[0], 1)


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

        # 3 unjudged runs.
        self.fake_mlflow.set_search_runs_result(["run-1", "run-2", "run-3"])
        # Register a trace per run so each goes through the genai.evaluate
        # batch path (log_assessments=False — genai.evaluate's harness logs
        # the assessments itself).  Without a trace a run falls back to the
        # on-demand score_trace path, which now requires a trace to reach
        # judged=true (Bug 3 invariant); the batch path is what this test
        # exercises.
        self.fake_mlflow.set_traces([
            _make_fake_trace("req-1", "run-1"),
            _make_fake_trace("req-2", "run-2"),
            _make_fake_trace("req-3", "run-3"),
        ])

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

        # The summary carries an eval_run_id key.  Now that the runs carry
        # traces, they go through genai.evaluate, which produces an eval
        # run id (the fake genai.evaluate returns "fake-eval-run-1").  The
        # real-mlflow end-to-end behaviour is covered by
        # tests/test_evaluator.py.
        self.assertIn("eval_run_id", result)
        self.assertIsNotNone(result["eval_run_id"])

    def test_handles_errors_gracefully(self):
        """A failing score_trace is captured as an error, not a crash."""
        from judge.scorer import run_judge_evaluation

        self.fake_mlflow.set_search_runs_result(["run-ok", "run-bad"])
        # run-ok has a trace (goes through genai.evaluate); run-bad has NO
        # trace (falls back to score_trace, where its Opus call fails).
        self.fake_mlflow.set_traces([_make_fake_trace("req-ok", "run-ok")])

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

        # One scored (run-ok via genai.evaluate), one error (run-bad's Opus
        # call failed in the score_trace fallback).
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
        # Register a trace per UNJUDGED run so they go through the
        # genai.evaluate batch path (log_assessments=False).  The
        # judged=true run is filtered out by the sampler before trace
        # resolution, so it never needs a trace.
        self.fake_mlflow.set_traces([
            _make_fake_trace("req-notag-1", "run-notag-1"),
            _make_fake_trace("req-judged-err", "run-judged-err"),
            _make_fake_trace("req-notag-2", "run-notag-2"),
        ])

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

    def _register_artifacts(self, meta=None, *, run_id="run-123"):
        """Register artifacts + a parse trace linked to ``run_id`` so the
        on-demand path (score_trace) can log the 9 assessments and reach
        judged=true.  The Bug 3 invariant requires a resolvable trace for
        judged=true; OK-asserting tests must register one."""
        meta = meta or _make_extraction_meta()
        self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4 fake")
        self.fake_mlflow.artifacts.register("extraction.json", json.dumps(meta).encode())
        # Trace linked by sourceRun=run_id; request_id matches the meta so
        # _resolve_trace_for_run(run_id) finds it.
        self.fake_mlflow.set_traces([
            _make_fake_trace(meta["request_id"], run_id),
        ])
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
        self._register_artifacts(run_id="run-err")
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
        and its metrics are returned (best-effort guarantee).  The Lakebase
        failure is independent of assessment persistence, so the run still
        reaches judged=true when the 9 assessments persist (Bug 3 invariant:
        the tag reflects assessment success, NOT the Lakebase write)."""
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
        # Register a trace so run-1 goes through the genai.evaluate batch
        # path (log_assessments=False) — the save_verdict failure is
        # isolated from assessment persistence on this path.
        self.fake_mlflow.set_traces([_make_fake_trace("req-1", "run-1")])
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

    def test_log_dict_redacts_pii_fields(self):
        """The log_dict payload redacts PII fields (cardDisplayName, description)
        per the field-aware policy, and retains non-PII numerics raw.

        Without an HMAC key configured (the default), PII leaves are OMITTED
        (None) — never a reversible unsalted digest. lastFourDigit, amount,
        date, and rewards points are retained raw (not individually
        identifying; documented trade-off). The rationale is OMITTED.
        """
        from judge.scorer import score_trace
        self._register_artifacts()
        store = _FakeResultStore()
        # A verdict carrying BOTH PII fields: cardDisplayName + a txn
        # description, plus non-PII fields (lastFour, amount, date, points).
        verdict = JudgeVerdict(
            request_id="req-test",
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
                FieldComparison(
                    "transactions[].date", "2026-01-01", "2026-01-01",
                    ComparisonOutcome.AGREE, FieldScope.TRANSACTION_ROW,
                    MatchMethod.DESCRIPTION_SIMILARITY_1TO1,
                    expected_row_index=0, actual_row_index=0,
                ),
                FieldComparison(
                    "rewards.pointsEarnedThisCycle", 100, 100,
                    ComparisonOutcome.AGREE, FieldScope.SCALAR,
                ),
            ),
            latency_ms=50.0,
            summary=json.dumps({"status": "OK"}),
        )
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = verdict
            score_trace("run-123", result_store=store)

        logged = [(rid, d, p) for rid, d, p in self.fake_mlflow.logged_dicts
                  if rid == "run-123"]
        self.assertEqual(len(logged), 1)
        body = logged[0][1]

        # Build a lookup by field_path for easier assertions.
        by_path = {row["field_path"]: row for row in body}

        # PII field cardDisplayName — expected/actual are OMITTED (None),
        # NOT the cleartext "Platinum Card" (no HMAC key → omit policy).
        card_row = by_path["cards[].cardMeta.cardDisplayName"]
        self.assertIsNone(card_row["expected"])
        self.assertIsNone(card_row["actual"])
        self.assertNotIn("Platinum", json.dumps(body))

        # PII field description — OMITTED, NOT "UPI-Amazon Pay".
        desc_row = by_path["transactions[].description"]
        self.assertIsNone(desc_row["expected"])
        self.assertIsNone(desc_row["actual"])
        self.assertNotIn("Amazon", json.dumps(body))

        # Non-PII fields retained RAW.
        self.assertEqual(by_path["cards[].cardMeta.lastFourDigit"]["expected"], "1234")
        self.assertEqual(by_path["transactions[].amount"]["expected"], 150.0)
        self.assertEqual(by_path["transactions[].date"]["expected"], "2026-01-01")
        self.assertEqual(
            by_path["rewards.pointsEarnedThisCycle"]["expected"], 100,
        )

    def test_log_dict_redacts_pii_with_hmac_key(self):
        """When an HMAC key IS configured, PII fields become keyed HMAC
        digests (not None, not cleartext). Non-PII fields stay raw."""
        from judge.scorer import score_trace
        self._register_artifacts()
        store = _FakeResultStore()
        verdict = JudgeVerdict(
            request_id="req-test", judge_model_id="databricks-claude-opus-5",
            comparisons=(
                FieldComparison(
                    "cards[].cardMeta.cardDisplayName", "Platinum Card",
                    "Platinum", ComparisonOutcome.DISAGREE,
                    FieldScope.SCALAR, card_index=0,
                ),
                FieldComparison(
                    "cards[].cardMeta.lastFourDigit", "1234", "1234",
                    ComparisonOutcome.AGREE, FieldScope.SCALAR, card_index=0,
                ),
            ),
            latency_ms=50.0, summary=json.dumps({"status": "OK"}),
        )
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter, \
             patch("harness.config_ws4.get_tracing_config") as mock_cfg:
            mock_cfg.return_value.feedback_hmac_key = b"test-hmac-key"
            MockAdapter.return_value.judge.return_value = verdict
            score_trace("run-123", result_store=store)

        logged = [(rid, d, p) for rid, d, p in self.fake_mlflow.logged_dicts
                  if rid == "run-123"]
        body = logged[0][1]
        by_path = {row["field_path"]: row for row in body}

        # cardDisplayName → keyed HMAC (prefix "hmac:"), NOT cleartext.
        card_expected = by_path["cards[].cardMeta.cardDisplayName"]["expected"]
        self.assertIsInstance(card_expected, str)
        self.assertTrue(card_expected.startswith("hmac:"))
        self.assertNotIn("Platinum", card_expected)

        # lastFourDigit stays raw.
        self.assertEqual(
            by_path["cards[].cardMeta.lastFourDigit"]["expected"], "1234",
        )

    def test_log_dict_rationale_omitted(self):
        """Fix #1 (round 2): the ``rationale`` key is OMITTED entirely from
        the log_dict payload (not just truncated) — it is Opus free-text that
        can echo cardholder names / transaction descriptions from the PDF, and
        a length cap still leaks the first N chars in cleartext. The outcome
        + similarity already convey the verdict signal in the artifact.
        """
        from judge.scorer import score_trace
        self._register_artifacts()
        store = _FakeResultStore()
        # A rationale containing a known cleartext PII string.
        pii_rationale = "cardholder name is John Doe, txn: UPI-Amazon Pay"
        verdict = JudgeVerdict(
            request_id="req-test", judge_model_id="databricks-claude-opus-5",
            comparisons=(
                FieldComparison(
                    "rewards.closingPoints", 500, 500,
                    ComparisonOutcome.AGREE, FieldScope.SCALAR,
                    rationale=pii_rationale,
                ),
            ),
            latency_ms=50.0, summary=json.dumps({"status": "OK"}),
        )
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = verdict
            score_trace("run-123", result_store=store)

        body = [(rid, d) for rid, d, _ in self.fake_mlflow.logged_dicts
                if rid == "run-123"][0][1]
        # The rationale key is ABSENT from every comparison row.
        for row in body:
            self.assertNotIn("rationale", row)
        # The known cleartext PII rationale does NOT appear anywhere in the
        # serialized log_dict payload.
        self.assertNotIn("John Doe", json.dumps(body))
        self.assertNotIn("UPI-Amazon", json.dumps(body))

    def test_log_dict_rationale_omitted_on_judge_error(self):
        """The rationale is omitted even on JUDGE_ERROR paths — the judge-error
        sentinels carry a rationale echoing the Opus failure, which can still
        contain echoed PDF text. The log_dict artifact must never carry it."""
        from judge.scorer import score_trace
        self._register_artifacts()
        store = _FakeResultStore()
        judge_error_verdict = JudgeVerdict(
            request_id="req-test", judge_model_id="databricks-claude-opus-5",
            comparisons=(
                FieldComparison(
                    "cards[].cardMeta.cardDisplayName", None, None,
                    ComparisonOutcome.ABSENT_IN_PDF, FieldScope.SCALAR,
                    rationale="judge response unusable: saw card 'Platinum'",
                ),
            ),
            latency_ms=50.0, summary=json.dumps({"status": "JUDGE_ERROR"}),
        )
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = judge_error_verdict
            score_trace("run-123", result_store=store)

        body = [(rid, d) for rid, d, _ in self.fake_mlflow.logged_dicts
                if rid == "run-123"][0][1]
        for row in body:
            self.assertNotIn("rationale", row)
        self.assertNotIn("Platinum", json.dumps(body))
        self.assertNotIn("unusable", json.dumps(body))


# ---------------------------------------------------------------------------
# resolve_run_id — request_id → MLflow run_id (two-tier: run tag + trace)
# ---------------------------------------------------------------------------

class ResolveRunIdTest(unittest.TestCase):
    """The on-demand single-trace judge resolves request_id → run_id via a
    two-tier lookup:

    1. Indexed run-tag fast path: ``search_runs`` with
       ``tags.request_id = '<id>'`` (works when the tag landed).
    2. Trace-based fallback: ``search_traces`` scanning
       ``trace_metadata.mlflow.traceInputs.request_id`` → ``mlflow.sourceRun``
       (reliable — the trace always carries request_id even when the run tag
       did not, the live root cause of the 404 bug).

    The fake ``search_runs`` HONOURS the filter (the old fake ignored it,
    making these tests vacuous) and the fake ``search_traces`` models
    ``trace_metadata`` so the fallback is actually exercised. Returns None
    cleanly when nothing is found (the endpoint maps that to 404).
    """

    def setUp(self):
        self.fake_mlflow = _install_fake_mlflow()
        import judge.scorer as scorer_mod
        scorer_mod._mlflow_configured = False

    def tearDown(self):
        _uninstall_fake_mlflow()

    # --- fast path (run tag) ---

    def test_resolves_via_run_tag_fast_path(self):
        """When a run carries the request_id RUN tag, the indexed fast path
        returns its run_id without scanning traces."""
        from judge.scorer import resolve_run_id
        self.fake_mlflow.set_request_id_tag("run-abc", "req-aabbccddeeff")
        # No traces registered — the fast path must resolve this alone.
        run_id = resolve_run_id("req-aabbccddeeff")
        self.assertEqual(run_id, "run-abc")

    def test_fast_path_takes_precedence_over_trace(self):
        """When BOTH a run tag and a trace exist for the same request_id but
        point at different runs, the indexed fast path wins (it is the
        precise, indexed lookup). The trace fallback is only a fallback."""
        from judge.scorer import resolve_run_id
        self.fake_mlflow.set_request_id_tag("run-tagged", "req-aabbccddeeff")
        self.fake_mlflow.set_traces([
            _make_fake_trace("req-aabbccddeeff", "run-from-trace"),
        ])
        run_id = resolve_run_id("req-aabbccddeeff")
        self.assertEqual(run_id, "run-tagged")

    # --- trace fallback (the live root cause: run tag is unreliable) ---

    def test_resolves_via_trace_when_run_tag_absent(self):
        """THE KEY FIX: a parse whose RUN lacks the request_id tag (the live
        root cause — the tag lands on only a minority of runs) is resolved via
        the trace, which ALWAYS carries request_id in traceInputs and the
        backing run in sourceRun."""
        from judge.scorer import resolve_run_id
        # No run tag registered (mirrors the 42/44 untagged live runs).
        self.fake_mlflow.set_traces([
            _make_fake_trace("req-283a2e31adc6", "run-0a923677"),
        ])
        run_id = resolve_run_id("req-283a2e31adc6")
        self.assertEqual(run_id, "run-0a923677")

    def test_trace_fallback_among_multiple_traces(self):
        """The trace scan matches the ONE trace whose traceInputs.request_id
        equals the target, ignoring other parse traces in the window."""
        from judge.scorer import resolve_run_id
        self.fake_mlflow.set_traces([
            _make_fake_trace("req-703c407f5764", "run-175a"),
            _make_fake_trace("req-283a2e31adc6", "run-0a92"),
            _make_fake_trace("req-d49bb046cbdc", "run-136d"),
        ])
        run_id = resolve_run_id("req-283a2e31adc6")
        self.assertEqual(run_id, "run-0a92")

    def test_trace_fallback_ignores_trace_without_trace_inputs(self):
        """A trace whose trace_metadata lacks mlflow.traceInputs (e.g. a
        judge-evaluation trace) is skipped, not mistaken for a match."""
        from judge.scorer import resolve_run_id
        other = SimpleNamespace(info=SimpleNamespace(
            request_metadata={"mlflow.sourceRun": "run-eval", "mlflow.traceName": "judge-evaluation"},
            trace_id="tr-eval",
        ))
        self.fake_mlflow.set_traces([
            other,
            _make_fake_trace("req-aabbccddeeff", "run-parse"),
        ])
        run_id = resolve_run_id("req-aabbccddeeff")
        self.assertEqual(run_id, "run-parse")

    def test_trace_fallback_recovers_when_run_tag_search_raises(self):
        """When the run-tag fast path RAISES (transient MLflow error), the
        trace fallback is still attempted and resolves the run — the bug
        is not masked by a fast-path failure."""
        from judge.scorer import resolve_run_id
        self.fake_mlflow.search_runs = Mock(
            side_effect=RuntimeError("mlflow internal error")
        )
        self.fake_mlflow.set_traces([
            _make_fake_trace("req-aabbccddeeff", "run-recovered"),
        ])
        run_id = resolve_run_id("req-aabbccddeeff")
        self.assertEqual(run_id, "run-recovered")
        # The fast path was attempted (and raised) before the fallback.
        self.assertTrue(self.fake_mlflow.search_runs.called)

    # --- pagination (the BLOCKING fix: no hard age cliff) ---

    def test_resolves_via_trace_on_later_page(self):
        """THE BLOCKING FIX: the matching trace is NOT on the first page.
        The paginated scan must follow the continuation token to page 2 and
        find it there. This FAILS if the scan is reverted to a single capped
        ``search_traces`` call (the match at index 200 would be missed by a
        single ``max_results=200`` page)."""
        from judge.scorer import resolve_run_id, _TRACE_PAGE_SIZE
        # 200 decoy traces (no match) + the target at index 200.
        decoys = [
            _make_fake_trace(f"req-deadbeef{i:04x}", f"run-decoy-{i}")
            for i in range(_TRACE_PAGE_SIZE)
        ]
        target = _make_fake_trace("req-aabbccddeeff", "run-target")
        self.fake_mlflow.set_traces(decoys + [target])
        run_id = resolve_run_id("req-aabbccddeeff")
        self.assertEqual(run_id, "run-target")
        # Pagination was followed: page 1 (decoys) + page 2 (target).
        self.assertGreaterEqual(self.fake_mlflow._trace_search_calls, 2)

    def test_trace_scan_exhaustion_returns_none(self):
        """When no trace matches across multiple pages, the scan exhausts all
        pages and returns None cleanly (→ 404, never raises). Multi-page so
        the continuation-token break is genuinely exercised."""
        from judge.scorer import resolve_run_id, _TRACE_PAGE_SIZE
        # More traces than one page, none matching the target.
        n = _TRACE_PAGE_SIZE + 50
        self.fake_mlflow.set_traces([
            _make_fake_trace(f"req-deadbeef{i:04x}", f"run-decoy-{i}")
            for i in range(n)
        ])
        run_id = resolve_run_id("req-aabbccddeeff")
        self.assertIsNone(run_id)
        # All pages were fetched (not just the first).
        self.assertGreaterEqual(self.fake_mlflow._trace_search_calls, 2)

    def test_trace_scan_global_bound_terminates(self):
        """The global ``_MAX_TRACES_SCAN`` bound stops the loop before
        scanning an unbounded number of traces — a genuinely-absent
        request_id can't loop forever. Uses small patched constants so the
        test is fast and deterministic."""
        from judge.scorer import resolve_run_id
        import judge.scorer as scorer_mod
        # Patch to small values: page_size=2, max_scan=5. With 10 decoy
        # traces and no match, the loop processes 3 pages (6 traces) then
        # hits the bound (6 >= 5) and stops — it does NOT scan all 10.
        with patch.object(scorer_mod, "_TRACE_PAGE_SIZE", 2), \
             patch.object(scorer_mod, "_MAX_TRACES_SCAN", 5):
            self.fake_mlflow.set_traces([
                _make_fake_trace(f"req-deadbeef{i:04x}", f"run-d-{i}")
                for i in range(10)
            ])
            run_id = resolve_run_id("req-aabbccddeeff")
        self.assertIsNone(run_id)
        # Stopped at the bound after 3 pages (2+2+2=6 > 5), NOT 5 pages.
        self.assertEqual(self.fake_mlflow._trace_search_calls, 3)

    # --- negative paths ---

    def test_returns_none_when_no_run_and_no_trace(self):
        """When neither a tagged run nor a matching trace exists, returns
        None (→ endpoint 404)."""
        from judge.scorer import resolve_run_id
        # Register an UNRELATED trace so the scan is non-vacuous (it must
        # scan and reject it, not short-circuit on an empty result).
        self.fake_mlflow.set_traces([
            _make_fake_trace("req-deadbeefdead", "run-other"),
        ])
        run_id = resolve_run_id("req-aabbccddeeff")
        self.assertIsNone(run_id)

    def test_returns_none_for_malformed_request_id(self):
        """A request_id not matching the canonical req-<12hex> form is rejected
        before any MLflow search (→ 404/400, no filter injection risk)."""
        from judge.scorer import resolve_run_id
        # Replace search_runs with a Mock that would record a call — assert
        # it is NOT called for a malformed id (the regex guard short-circuits
        # before the fast path). The trace path uses MlflowClient.search_traces;
        # assert its call counter stays at 0 too.
        self.fake_mlflow.search_runs = Mock(return_value=_FakeRunsFrame([]))
        run_id = resolve_run_id("req-123")
        self.assertIsNone(run_id)
        self.fake_mlflow.search_runs.assert_not_called()
        self.assertEqual(self.fake_mlflow._trace_search_calls, 0)

    def test_rejects_filter_injection_attempt(self):
        """A request_id crafted to alter the MLflow filter (e.g. a quote) is
        rejected by the canonical-form guard — never interpolated into the
        filter_string."""
        from judge.scorer import resolve_run_id
        self.fake_mlflow.search_runs = Mock(return_value=_FakeRunsFrame([]))
        for malicious in (
            "req-' OR '1'='1",      # SQL-style injection
            "req-abc' OR tags.x='y", # MLflow filter injection
            "'; DROP TABLE runs;--", # classical
            "req-aaaaaaaaaaaa'",     # quote after valid-looking prefix
        ):
            self.assertIsNone(resolve_run_id(malicious))
        self.fake_mlflow.search_runs.assert_not_called()
        self.assertEqual(self.fake_mlflow._trace_search_calls, 0)

    def test_returns_none_when_experiment_not_found(self):
        """When the experiment is unreachable, returns None (→ 404, never 500)."""
        from judge.scorer import resolve_run_id
        self.fake_mlflow._experiment = None
        run_id = resolve_run_id("req-aabbccddeeff")
        self.assertIsNone(run_id)

    def test_both_searches_failing_returns_none_not_raise(self):
        """When BOTH the run-tag search and the trace search raise, returns
        None (→ 404, never 500)."""
        from judge.scorer import resolve_run_id
        self.fake_mlflow.search_runs = Mock(
            side_effect=RuntimeError("run search error")
        )
        self.fake_mlflow.set_trace_search_raises(True)
        run_id = resolve_run_id("req-aabbccddeeff")
        self.assertIsNone(run_id)

    # --- DataFrame path of _iter_trace_metadata (non-blocking: closes a
    #     regression gap — the pandas branch was previously untested) ---

    def test_iter_trace_metadata_dataframe_path(self):
        """The pandas ``DataFrame`` branch of ``_iter_trace_metadata``
        (``iterrows`` + ``row.get('trace_metadata')``) is exercised directly
        so a future column-name or Series-.get regression is caught. Codex
        confirmed the column IS named ``trace_metadata`` and rows are pandas
        Series in both mlflow 3.10.1 (the pinned version) and earlier; this
        fake models that shape.
        The production paginated path uses ``PagedList[Trace]`` (the
        Trace-object branch), but the DataFrame branch is kept for
        defensive coverage of the module-level API shape."""
        from judge.scorer import _iter_trace_metadata
        df = _FakeDataFrame([
            {
                "mlflow.traceInputs": json.dumps(
                    {"request_id": "req-aabbccddeeff", "bank": "HDFC"}
                ),
                "mlflow.sourceRun": "run-1",
            },
            {
                "mlflow.traceInputs": json.dumps(
                    {"request_id": "req-deadbeefdead", "bank": "ICICI"}
                ),
                "mlflow.sourceRun": "run-2",
            },
            None,  # a trace with no trace_metadata (e.g. a judge-eval trace)
        ])
        metas = list(_iter_trace_metadata(df))
        self.assertEqual(len(metas), 2)  # the None row is skipped
        self.assertEqual(metas[0]["mlflow.sourceRun"], "run-1")
        self.assertEqual(metas[1]["mlflow.sourceRun"], "run-2")
        # Verify the traceInputs JSON is parseable and carries request_id.
        inp0 = json.loads(metas[0]["mlflow.traceInputs"])
        self.assertEqual(inp0["request_id"], "req-aabbccddeeff")


# ---------------------------------------------------------------------------
# genai.evaluate path tests (run_judge_evaluation with traces → genai scorer)
# ---------------------------------------------------------------------------

class RunGenaiEvaluationTest(unittest.TestCase):
    """Tests for the genai.evaluate batch path: run_judge_evaluation resolves
    each sampled run's trace, drives ONE @scorer (single Opus call per trace),
    and returns 7 per-field Feedback assessments per trace + the unchanged
    aggregate summary shape.

    Uses the fake mlflow.genai.evaluate which ASSERTS scorers were passed and
    drives the scorer once per row (so the full _judge_and_persist pipeline
    runs through the fake mlflow artifacts + MlflowClient).
    """

    def setUp(self):
        self.fake_mlflow = _install_fake_mlflow()
        import judge.scorer as scorer_mod
        scorer_mod._mlflow_configured = False

    def tearDown(self):
        _uninstall_fake_mlflow()

    def _setup_three_traced_runs(self, store):
        """Register 3 unjudged runs + their traces + shared artifacts.
        Returns the 3 run_ids."""
        run_ids = ["run-1", "run-2", "run-3"]
        self.fake_mlflow.set_search_runs_result(run_ids)
        # Register a trace per run (sourceRun matches the run_id).
        traces = [_make_fake_trace(f"req-{i}", rid)
                  for i, rid in enumerate(run_ids, 1)]
        self.fake_mlflow.set_traces(traces)
        # Register shared artifacts (the fake matches by artifact_path).
        meta = _make_extraction_meta()
        self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4 fake")
        self.fake_mlflow.artifacts.register(
            "extraction.json", json.dumps(meta).encode()
        )
        return run_ids

    def test_genai_evaluate_called_with_scorers(self):
        """genai.evaluate is called with a non-empty scorers list (the fake
        asserts this), and exactly one scorer is passed (not 7)."""
        from judge.scorer import run_judge_evaluation

        store = _FakeResultStore()
        self._setup_three_traced_runs(store)
        verdict = _make_verdict()
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = verdict
            run_judge_evaluation(sample_size=3, result_store=store)

        self.assertGreater(self.fake_mlflow.genai.evaluate_calls, 0)
        self.assertEqual(len(self.fake_mlflow.genai.scorers_passed), 1)

    def test_opus_called_once_per_trace_not_seven_times(self):
        """Opus (OpusJudgeAdapter.judge) is called EXACTLY ONCE per trace —
        not 7× (one scorer, not seven).  3 traces → 3 judge calls."""
        from judge.scorer import run_judge_evaluation

        store = _FakeResultStore()
        self._setup_three_traced_runs(store)
        verdict = _make_verdict()
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = verdict
            run_judge_evaluation(sample_size=3, result_store=store)

        # 3 traces → exactly 3 Opus calls (one per trace, NOT 7×3=21).
        self.assertEqual(MockAdapter.return_value.judge.call_count, 3)

    def test_seven_per_field_assessments_per_trace(self):
        """The scorer returns 7 per-field + 2 overall Feedbacks per trace.
        3 traces → 27 assessments collected by the fake genai.evaluate.
        The 7 per-field names are the expected ones (3× each)."""
        from judge.evaluator import FIELD_ASSESSMENT_NAMES
        from judge.scorer import run_judge_evaluation

        store = _FakeResultStore()
        self._setup_three_traced_runs(store)
        verdict = _make_verdict()
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = verdict
            run_judge_evaluation(sample_size=3, result_store=store)

        assessments = self.fake_mlflow.genai.assessments
        # 3 traces × 9 Feedbacks (7 per-field + 2 overall) = 27.
        self.assertEqual(len(assessments), 27)
        # 7 per-field names × 3 traces = 21 per-field assessments.
        per_field = [a for a in assessments
                     if not a.name.startswith("judge_overall")]
        self.assertEqual(len(per_field), 21)
        # Each of the 7 field names appears exactly 3 times.
        from collections import Counter
        name_counts = Counter(a.name for a in per_field)
        self.assertEqual(set(name_counts.keys()), set(FIELD_ASSESSMENT_NAMES.values()))
        for name, count in name_counts.items():
            self.assertEqual(count, 3, f"{name} should appear 3× (once per trace)")

    def test_save_verdict_called_per_trace(self):
        """The verdict is persisted to Lakebase (save_verdict) once per
        OK-scored trace — the inline per-parse verdict keeps working."""
        from judge.scorer import run_judge_evaluation

        store = _FakeResultStore()
        self._setup_three_traced_runs(store)
        verdict = _make_verdict()
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = verdict
            run_judge_evaluation(sample_size=3, result_store=store)

        self.assertEqual(len(store.saved), 3)

    def test_aggregate_summary_shape_unchanged(self):
        """The aggregate summary shape consumed by the frontend is unchanged:
        count_judged, count_errors, overall_strict, overall_narration_forgiven,
        per_field (7 fields), per_bank, eval_run_id."""
        from judge.scorer import run_judge_evaluation

        store = _FakeResultStore()
        self._setup_three_traced_runs(store)
        verdict = _make_verdict()
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = verdict
            result = run_judge_evaluation(sample_size=3, result_store=store)

        self.assertEqual(result["count_judged"], 3)
        self.assertEqual(result["count_errors"], 0)
        self.assertEqual(result["overall_strict"], 1.0)
        self.assertEqual(result["overall_narration_forgiven"], 1.0)
        self.assertEqual(len(result["per_field"]), 7)
        self.assertIn("HDFC", result["per_bank"])
        self.assertEqual(result["per_bank"]["HDFC"]["count"], 3)
        # eval_run_id is set (from the fake genai.evaluate).
        self.assertIsNotNone(result["eval_run_id"])

    def test_pii_redacted_in_assessments(self):
        """PII fields (cardDisplayName, description) are HMAC'd/omitted in the
        assessment metadata; the rationale is dropped (None).  No card names
        or descriptions in cleartext."""
        from judge.scorer import run_judge_evaluation

        store = _FakeResultStore()
        self._setup_three_traced_runs(store)
        # A verdict with PII fields.
        verdict = JudgeVerdict(
            request_id="req-test",
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
            ),
            latency_ms=50.0,
            summary=json.dumps({"status": "OK"}),
        )
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = verdict
            run_judge_evaluation(sample_size=3, result_store=store)

        assessments = self.fake_mlflow.genai.assessments
        card_fb = next(a for a in assessments if a.name == "judge_cardDisplayName")
        # The comparisons metadata is a JSON STRING (Databricks tracking
        # store validates Feedback.metadata as flat dict[str, str]; a nested
        # list is rejected — see judge/evaluator.py COMPARISONS_METADATA_KEY).
        comps = json.loads(card_fb.metadata["comparisons"])
        # PII omitted (None) without HMAC key — NOT the cleartext.
        self.assertIsNone(comps[0]["expected"])
        self.assertIsNone(comps[0]["actual"])
        self.assertNotIn("Platinum", json.dumps(comps))
        # Rationale is dropped.
        self.assertIsNone(card_fb.rationale)

    def test_runs_without_traces_fall_back_to_score_trace(self):
        """Runs without a trace (search_traces returns empty) fall back to
        score_trace.  Bug 3 invariant: there is no trace_id to log the 9
        assessments onto, so the run MUST NOT be tagged judged=true (that
        would strand it — no assessments AND the batch sampler skips
        judged=true runs forever).  Instead each run is left RE-JUDGEABLE
        (judged=error, status ASSESSMENT_ERROR) and the failure is
        SURFACED in the aggregate.  The Lakebase verdict is still persisted
        (preserved regardless — the verdict is valid; only the assessment
        persistence failed).  genai.evaluate is NOT called (no traces)."""
        from judge.scorer import run_judge_evaluation

        store = _FakeResultStore()
        # 2 runs, but NO traces registered → _resolve_trace_for_run returns None.
        self.fake_mlflow.set_search_runs_result(["run-1", "run-2"])
        meta = _make_extraction_meta()
        self.fake_mlflow.artifacts.register("statement.pdf", b"%PDF-1.4 fake")
        self.fake_mlflow.artifacts.register(
            "extraction.json", json.dumps(meta).encode()
        )
        verdict = _make_verdict()
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = verdict
            result = run_judge_evaluation(sample_size=2, result_store=store)

        # genai.evaluate was NOT called (no traces).
        self.assertEqual(self.fake_mlflow.genai.evaluate_calls, 0)
        # Bug 3 invariant: no trace → NOT judged=true → counted as errors
        # (ASSESSMENT_ERROR), re-judgeable in the next evaluation.
        self.assertEqual(result["count_judged"], 0)
        self.assertEqual(result["count_errors"], 2)
        self.assertEqual(len(result["errors"]), 2)
        # Lakebase verdict PRESERVED regardless of the assessment failure
        # (the verdict is valid; assessments are a separate concern).
        self.assertEqual(len(store.saved), 2)
        # Both runs tagged judged=error (re-judgeable), NOT judged=true.
        judged_tags = {rid: v for k, v, rid in self.fake_mlflow.set_tags
                       if k == "judged"}
        self.assertEqual(judged_tags, {"run-1": "error", "run-2": "error"})
        # eval_run_id is None (no genai.evaluate run).
        self.assertIsNone(result["eval_run_id"])

    def test_genai_failure_falls_back_to_score_trace(self):
        """If genai.evaluate fails, run_judge_evaluation falls back to
        score_trace for all traced runs — they are still scored."""
        from judge.scorer import run_judge_evaluation

        store = _FakeResultStore()
        self._setup_three_traced_runs(store)
        verdict = _make_verdict()

        # Make genai.evaluate raise.
        original_evaluate = self.fake_mlflow.genai.evaluate
        self.fake_mlflow.genai.evaluate = lambda *a, **kw: (_ for _ in ()).throw(
            RuntimeError("genai evaluate boom")
        )
        try:
            with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
                MockAdapter.return_value.judge.return_value = verdict
                result = run_judge_evaluation(sample_size=3, result_store=store)
        finally:
            self.fake_mlflow.genai.evaluate = original_evaluate

        # Fallback: all 3 runs scored via score_trace.
        self.assertEqual(result["count_judged"], 3)
        self.assertEqual(len(store.saved), 3)
        self.assertIsNone(result["eval_run_id"])

    def test_run_metrics_logged_to_source_run(self):
        """Per-trace metrics are logged to the SOURCE run (not the eval run)
        via MlflowClient.log_metric — the per-run view still works."""
        from judge.scorer import run_judge_evaluation

        store = _FakeResultStore()
        run_ids = self._setup_three_traced_runs(store)
        verdict = _make_verdict()
        with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
            MockAdapter.return_value.judge.return_value = verdict
            run_judge_evaluation(sample_size=3, result_store=store)

        # Each source run has judge.accuracy logged.
        for rid in run_ids:
            run_metrics = [(k, v) for k, v, r in self.fake_mlflow.logged_metrics
                           if r == rid]
            self.assertIn(("judge.accuracy", 1.0), run_metrics)
        # Each source run is tagged judged=true.
        for rid in run_ids:
            self.assertIn(("judged", "true", rid), self.fake_mlflow.set_tags)

    def test_partial_genai_failure_does_not_rescore_completed_traces(self):
        """BLOCKING regression guard: if genai.evaluate completes N scorers
        then RAISES mid-run, the completed traces are NOT re-scored by the
        score_trace fallback.  Without the fix, the fallback re-scores EVERY
        traced run — double-calling Opus and duplicating save_verdict/metric
        writes for the completed subset.

        Setup: 3 traced runs.  genai.evaluate processes 2 rows (Opus +
        save_verdict + metrics for each), then raises on the 3rd.  The 3rd
        run was never scored by genai.  Expected: the fallback scores ONLY
        the 3rd run — 3 total Opus calls + 3 total save_verdict (NOT 6).
        """
        from judge.scorer import run_judge_evaluation

        store = _FakeResultStore()
        self._setup_three_traced_runs(store)  # 3 traced runs
        verdict = _make_verdict()

        # genai.evaluate completes 2 scorers then raises on the 3rd row.
        self.fake_mlflow.genai.fail_after_n_rows = 2
        try:
            with patch("harness.judge_adapter.OpusJudgeAdapter") as MockAdapter:
                MockAdapter.return_value.judge.return_value = verdict
                result = run_judge_evaluation(sample_size=3, result_store=store)
        finally:
            self.fake_mlflow.genai.fail_after_n_rows = None

        # 3 traces → exactly 3 Opus calls total (2 from genai + 1 from
        # fallback).  NOT 5 (2 genai + 3 fallback) — the 2 completed
        # traces must NOT be re-scored.
        self.assertEqual(MockAdapter.return_value.judge.call_count, 3)
        # 3 save_verdict total (2 from genai + 1 from fallback).  NOT 5 —
        # no duplicate save_verdict for the completed traces.
        self.assertEqual(len(store.saved), 3)
        # All 3 runs are scored (2 by genai + 1 by fallback).
        self.assertEqual(result["count_judged"], 3)
        self.assertEqual(result["count_errors"], 0)
        # eval_run_id is None — partial genai failure (no eval run to log
        # supplementary metrics to).
        self.assertIsNone(result["eval_run_id"])


if __name__ == "__main__":
    unittest.main()
