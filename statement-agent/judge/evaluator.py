"""MLflow ``genai.evaluate``-based judge scorer: per-field TRACE-LEVEL
assessments in the MLflow Assessments tab.

MIGRATION — ``mlflow.models.evaluate`` → ``mlflow.genai.evaluate`` + ``@scorer``

The previous implementation (``mlflow.models.evaluate`` + ``make_metric``)
produced ONLY an aggregate Evaluation Run visible in the experiments
"Evaluations" tab — per-field judge accuracy was NOT surfaced as trace-level
assessments.  This module migrates to ``mlflow.genai.evaluate`` (mode 1:
post-hoc, trace-column dataset, ``predict_fn=None``) with ONE ``@scorer``
that judges each already-logged parse trace and returns a LIST of per-field
``Feedback`` objects.  ``genai.evaluate`` logs those as ASSESSMENTS on the
ORIGINAL parse trace (one row per field in the Assessments tab).

SCORER DESIGN — one Opus call → list of 7 per-field Feedbacks

A single ``@scorer`` (``judge_per_field``) resolves the sourceRun ``run_id``
from the trace's ``mlflow.sourceRun`` metadata, reuses
:func:`judge.scorer._judge_and_persist` (the shared download→Opus→compare→
persist core) so Opus is called EXACTLY ONCE per trace, then returns 7
per-field ``Feedback`` objects (one per judged field:
``judge.cardDisplayName``, ``judge.transactions.amount``, …) plus 2 overall
(``judge.overall.strict`` / ``judge.overall.forgiven``).  Do NOT write 7
separate scorer functions — that would call Opus 7×.

PII REDACTION — reuse the EXACT field-aware policy from
:func:`judge.scorer._redact_comparisons` (keyed HMAC / omit for
``cardDisplayName`` + ``description``; retain ``lastFourDigit`` + non-PII
numerics raw; DROP the free-text ``rationale`` — set to ``None``).  No card
names or descriptions reach an assessment in cleartext.

ADDITIVE — both the existing aggregate Evaluations view AND the inline
per-parse verdict keep working:

* The aggregate summary shape consumed by the frontend
  (``count_judged``, ``count_errors``, ``overall_strict``,
  ``overall_narration_forgiven``, ``per_field``, ``per_bank``,
  ``eval_run_id``) is built by :func:`judge.scorer._aggregate_results`
  (unchanged) from a side-channel of per-trace result dicts the scorer
  collects — so the frontend contract is preserved.
* Supplementary aggregate metrics (counts, per-field means, per-bank
  breakdown) + tags are logged to the genai.evaluate run via
  ``MlflowClient`` after it returns, so the "Evaluations" tab still
  renders them.
* The inline per-parse verdict (Lakebase ``save_verdict``) is called inside
  ``_judge_and_persist`` — so the Results-view "Judge this statement" path
  keeps working.

This module is stdlib-importable at the module level (third-party imports
like ``mlflow`` are function-local) so the test gate runs on this machine
where ``pypi`` is blackholed.
"""

from __future__ import annotations

import logging
from typing import Any

from judge.scorer import JUDGED_FIELDS

_LOGGER = logging.getLogger("statement-agent.evaluator")

# Per-field assessment names — one row per field in the Assessments tab.
# Short, stable names (``judge.cardDisplayName``, ``judge.transactions.amount``)
# so each field is its own row.  Ordered to mirror :data:`JUDGED_FIELDS`.
FIELD_ASSESSMENT_NAMES: dict[str, str] = {
    "cards[].cardMeta.cardDisplayName": "judge.cardDisplayName",
    "cards[].cardMeta.lastFourDigit": "judge.lastFourDigit",
    "rewards.pointsEarnedThisCycle": "judge.pointsEarnedThisCycle",
    "rewards.closingPoints": "judge.closingPoints",
    "transactions[].date": "judge.transactions.date",
    "transactions[].description": "judge.transactions.description",
    "transactions[].amount": "judge.transactions.amount",
}

# Overall assessment names (additive to the 7 per-field ones).
OVERALL_STRICT_NAME = "judge.overall.strict"
OVERALL_FORGIVEN_NAME = "judge.overall.forgiven"


def _field_key(field_path: str) -> str:
    """Map a judged field path to its flat metric key (mirrors scorer)."""
    return field_path.replace("[]", "").replace(".", "_")


def build_field_feedbacks(
    verdict: Any, metrics: dict[str, Any]
) -> list[Any]:
    """Build 7 per-field ``Feedback`` objects + 2 overall from a ``JudgeVerdict``.

    One ``Feedback`` per judged field (``judge.cardDisplayName`` etc.), plus
    ``judge.overall.strict`` and ``judge.overall.forgiven``.  Each per-field
    assessment's ``value`` is the per-field strict accuracy (float or ``None``);
    its ``metadata`` carries the field path, comparison count, and the
    PII-REDACTED per-comparison details (reusing
    :func:`judge.scorer._redact_comparisons`).

    PII REDACTION — reuses the EXACT field-aware policy from the
    ``verdict_comparisons.json`` artifact:

    * ``cardDisplayName`` / ``description`` (client PII) → keyed HMAC, or
      omitted (``None``) when no HMAC key is configured (never a reversible
      unsalted digest).
    * ``lastFourDigit`` / ``amount`` / ``date`` / points → retained raw
      (not individually identifying; documented trade-off).
    * The free-text ``rationale`` is OMITTED entirely (``Feedback.rationale
      = None``) — it is Opus free-text that may echo cardholder names /
      transaction descriptions from the PDF.

    Third-party imports (``mlflow.entities``) are function-local so this
    module stays stdlib-importable for the test gate.  Returns a list of
    ``mlflow.entities.assessment.Feedback`` objects.
    """
    from collections import defaultdict

    from mlflow.entities import AssessmentSource, Feedback

    from harness.tracing_keys import ASSESSMENT_LLM_JUDGE
    from judge.scorer import _redact_comparisons

    source = AssessmentSource(ASSESSMENT_LLM_JUDGE, verdict.judge_model_id)

    # Group comparisons by field_path for per-field metadata.
    grouped: dict[str, list[Any]] = defaultdict(list)
    for c in verdict.comparisons:
        grouped[c.field_path].append(c)

    def _feedback_value(accuracy: Any) -> Any:
        """Map a per-field accuracy to a Feedback value.

        ``mlflow.entities.Feedback`` rejects ``value=None`` (it requires
        either a value or an error).  When a field has no scored comparisons
        (accuracy ``None`` — e.g. transactions absent from the PDF), use the
        string sentinel ``"not_scored"`` so the field still produces a row in
        the Assessments tab (7 per-field assessments per trace regardless),
        while genai.evaluate's aggregation skips it (``_cast_assessment_value
        _to_float`` returns ``None`` for unrecognised strings → excluded from
        the mean).
        """
        return accuracy if accuracy is not None else "not_scored"

    feedbacks: list[Any] = []
    for field_path in JUDGED_FIELDS:
        name = FIELD_ASSESSMENT_NAMES[field_path]
        accuracy = metrics.get(f"judge.{_field_key(field_path)}")
        comps = grouped.get(field_path, [])
        feedbacks.append(
            Feedback(
                name=name,
                value=_feedback_value(accuracy),
                source=source,
                rationale=None,  # OMITTED — PII vector (Opus free-text)
                metadata={
                    "field_path": field_path,
                    "n_comparisons": len(comps),
                    # Redacted per-comparison details (HMAC/omit PII, no rationale).
                    "comparisons": _redact_comparisons(comps),
                },
            )
        )

    # Overall strict + narration-forgiven (additive to the 7 per-field).
    feedbacks.append(
        Feedback(
            name=OVERALL_STRICT_NAME,
            value=_feedback_value(metrics.get("judge.accuracy")),
            source=source,
            rationale=None,
            metadata={
                "n_scored": int(metrics.get("judge.scored", 0)),
                "n_correct": int(metrics.get("judge.correct", 0)),
            },
        )
    )
    feedbacks.append(
        Feedback(
            name=OVERALL_FORGIVEN_NAME,
            value=_feedback_value(metrics.get("judge.accuracy_forgiven")),
            source=source,
            rationale=None,
        )
    )
    return feedbacks


def make_judge_scorer(
    result_store: Any, results_collector: list[dict[str, Any]]
) -> Any:
    """Build the ``@mlflow.genai.scorer`` that judges one trace per row.

    The scorer resolves the sourceRun ``run_id`` from the trace's
    ``mlflow.sourceRun`` metadata, reuses
    :func:`judge.scorer._judge_and_persist` (the SINGLE Opus call per trace),
    collects the per-trace result dict into ``results_collector`` (the
    side-channel :func:`run_judge_evaluation` aggregates via
    :func:`_aggregate_results`), and returns the 7+2 per-field ``Feedback``
    objects.  ``genai.evaluate`` logs those as trace-level assessments on the
    original parse trace.

    On failure the scorer appends an ``ERROR`` result dict to the side-channel
    (so the aggregate ``count_errors`` is correct) and re-raises —
    ``genai.evaluate`` catches it and logs an error assessment with the
    scorer name.

    ``result_store`` threads the app's cached Lakebase store into the scorer
    so each OK verdict is persisted inline (best-effort).  ``None`` lets the
    scorer build its own lazily.
    """
    from mlflow.genai.scorers import scorer

    @scorer(
        name="judge_per_field",
        description=(
            "Opus-5 per-field judge: one Opus call → 7 per-field Feedback "
            "assessments (one per judged field) + overall strict/forgiven."
        ),
    )
    def _judge_per_field(trace: Any) -> list[Any]:
        from judge.scorer import (
            _build_result_dict,
            _judge_and_persist,
            _sanitize_error,
        )

        # Resolve sourceRun run_id from the trace metadata (the same key
        # resolve_run_id / _iter_trace_metadata read).
        tmeta = (
            getattr(getattr(trace, "info", None), "request_metadata", None)
            or {}
        )
        run_id = tmeta.get("mlflow.sourceRun")
        if not run_id:
            # No sourceRun → cannot download artifacts.  Record an error in
            # the side-channel and re-raise so genai.evaluate logs an error
            # assessment.  count_errors stays correct.
            results_collector.append(
                {
                    "run_id": None,
                    "status": "ERROR",
                    "error": "trace has no mlflow.sourceRun metadata",
                }
            )
            raise ValueError("trace has no mlflow.sourceRun metadata")

        try:
            verdict, meta, metrics, status = _judge_and_persist(
                run_id, result_store
            )
        except Exception as exc:  # noqa: BLE001 - re-raise for genai.evaluate
            results_collector.append(
                {
                    "run_id": run_id,
                    "status": "ERROR",
                    "error": _sanitize_error(exc),
                }
            )
            raise

        # Side-channel: collect the result dict for the aggregate summary.
        results_collector.append(
            _build_result_dict(run_id, meta, metrics, status)
        )
        return build_field_feedbacks(verdict, metrics)

    return _judge_per_field


def _log_supplementary_metrics_and_tags(
    run_id: str, summary: dict[str, Any]
) -> None:
    """Log aggregate metrics + tags not produced by genai.evaluate's scorer
    aggregation to the evaluation run.

    genai.evaluate aggregates the per-field Feedback VALUES into ``mean``
    metrics (e.g. ``judge.cardDisplayName/mean``).  Counts, per-field means
    (keyed by the full field path), and the per-bank breakdown are NOT
    derivable from assessment values alone — they are logged here directly
    via ``MlflowClient`` (explicit ``run_id`` — works after the genai.evaluate
    run context has ended) so the "Evaluations" tab still renders them.
    Mirrors the previous ``_log_supplementary_metrics`` + tag logic.  ``None``
    values are skipped — ``log_metric`` rejects them.
    """
    from mlflow.tracking import MlflowClient

    client = MlflowClient()

    def _log(key: str, value: Any) -> None:
        if value is not None:
            try:
                client.log_metric(run_id, key, float(value))
            except Exception:  # noqa: BLE001 - a bad value never fails the run
                pass

    _log("judge.count_judged", summary.get("count_judged"))
    _log("judge.count_errors", summary.get("count_errors"))

    per_field = summary.get("per_field", {})
    for field, data in per_field.items():
        if isinstance(data, dict):
            _log(f"judge.mean_{_field_key(field)}", data.get("accuracy"))

    per_bank = summary.get("per_bank", {})
    for bank, data in per_bank.items():
        if isinstance(data, dict):
            _log(f"judge.bank.{bank}.strict", data.get("strict_accuracy"))
            _log(
                f"judge.bank.{bank}.forgiven",
                data.get("narration_forgiven_accuracy"),
            )

    try:
        client.set_tag(run_id, "eval_run", "true")
        client.set_tag(run_id, "judge_evaluation", "true")
        client.set_tag(run_id, "n_judged", str(summary.get("count_judged", 0)))
        client.set_tag(run_id, "n_errors", str(summary.get("count_errors", 0)))
    except Exception:  # noqa: BLE001 - best-effort tagging
        pass


def run_genai_evaluation(
    traces: list[Any],
    result_store: Any,
    *,
    experiment_id: str | None = None,
) -> dict[str, Any] | None:
    """Run ``mlflow.genai.evaluate`` over a list of parse ``Trace`` objects.

    Creates the ``@scorer`` via :func:`make_judge_scorer`, starts an MLflow run
    (which ``genai.evaluate`` reuses — its ``_start_run_or_reuse_active_run``
    yields the active run id), and evaluates.  Returns
    ``{"eval_run_id": ..., "results": [...]}`` where ``results`` is the
    side-channel of per-trace result dicts the scorer collected.

    PARTIAL-FAILURE PRESERVES COMPLETED RESULTS — never returns ``None`` when
    ``traces`` is non-empty.  If ``genai.evaluate`` raises AFTER one or more
    per-trace scorers already completed (each already called Opus once + wrote
    ``save_verdict`` + metrics), the completed ``results`` are returned with
    ``"partial": True`` and ``eval_run_id=None``.  The caller uses those to
    SKIP the completed run_ids in the ``score_trace`` fallback — preventing a
    second Opus call and duplicate ``save_verdict``/metric writes for the
    completed subset.  Returns ``None`` only when ``traces`` is empty.
    """
    if not traces:
        _LOGGER.info("skipping genai.evaluate run: no traces to evaluate")
        return None

    # Declared OUTSIDE the try so it survives a partial failure: if
    # genai.evaluate raises mid-run, the scorer calls already recorded here
    # are returned to the caller so it can skip those run_ids in the
    # score_trace fallback (no double Opus, no double save_verdict).
    results_collector: list[dict[str, Any]] = []
    try:
        import mlflow

        from judge.scorer import _ensure_mlflow_configured, _get_experiment_id

        _ensure_mlflow_configured(mlflow)
        if experiment_id is None:
            experiment_id = _get_experiment_id(mlflow)

        scorer = make_judge_scorer(result_store, results_collector)

        # genai.evaluate reuses the active run — start one ourselves so we
        # control the experiment + run_name, then genai.evaluate logs into it.
        # If start_run is unavailable the AttributeError propagates into the
        # surrounding except (best-effort partial return).
        with mlflow.start_run(
            experiment_id=experiment_id, run_name="judge-evaluation"
        ) as run:
            eval_result = mlflow.genai.evaluate(
                data=[{"trace": t} for t in traces],
                scorers=[scorer],
            )
            eval_run_id = eval_result.run_id

        return {"eval_run_id": eval_run_id, "results": results_collector}
    except Exception as exc:  # noqa: BLE001 - best-effort, never fatal
        _LOGGER.warning("genai.evaluate run failed: %s", exc, exc_info=True)
        # Preserve the run_ids the scorer already completed (each already
        # called Opus once + wrote save_verdict + metrics) so the caller does
        # NOT re-score them via the score_trace fallback — that would
        # double-call Opus and double-write save_verdict/metrics for the
        # completed subset.  ``partial=True`` signals the genai run did not
        # finish; ``eval_run_id=None`` so the caller skips supplementary
        # metric logging to a half-formed run.
        return {
            "eval_run_id": None,
            "results": results_collector,
            "partial": True,
        }
