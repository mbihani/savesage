"""MLflow Evaluate-based judge scorer: aggregates judge results into one
evaluation run visible in the MLflow experiments "Evaluations" tab.

WHY THIS EXISTS — answers "why can't we add a custom scorer via MLflow?":

The existing post-hoc judge (``judge/scorer.py``) logs per-field accuracy
metrics to EACH individual trace run via
``MlflowClient.log_metric(run_id, key, value)``.  Those metrics ARE in MLflow,
but they are scattered across individual parse runs — there is no single
aggregated view in the experiments tab.  You can open one trace and see ITS
``judge.accuracy``, but you cannot see all judge results in one place, nor
the per-trace breakdown side by side.

``mlflow.models.evaluate`` (the non-deprecated successor to ``mlflow.evaluate``
as of MLflow 3.0) creates a dedicated **Evaluation Run** that fixes this:

* Each judged trace becomes a ROW in the ``eval_results_table`` artifact
  (run_id, bank, strict/forgiven accuracy, per-field accuracy, outputs).
* Custom scorers (``mlflow.models.make_metric``) compute aggregate metrics
  that render in the run's metrics column AND the "Evaluations" tab.
* The run is tagged ``eval_run=true`` so it is identifiable in the
  experiment list alongside the individual parse traces.

This module keeps the per-trace metric logging in ``judge/scorer.py`` (so
individual traces still show their own scores) and ADDS this aggregated
evaluation run on top of it.  The two are complementary: per-run metrics for
drilling into one parse, the evaluation run for the cross-trace view.

CURRENT PROCESS vs THIS MODULE
-------------------------------
* Current: ``score_trace`` logs ``judge.accuracy`` etc. to each parse run.
  Visible per-trace, NOT aggregated in the experiments tab.
* This module: ``run_mlflow_evaluation`` builds one row per judged trace and
  calls ``mlflow.models.evaluate`` with custom scorers, producing ONE
  evaluation run whose per-row table + aggregate metrics ARE the aggregated
  view the current process cannot provide.

The module is stdlib-importable at the module level (third-party imports
like ``mlflow``/``pandas`` are function-local inside ``try/except``) so the
test gate runs on this machine where ``pypi`` is blackholed.
"""

from __future__ import annotations

import logging
from typing import Any

from judge.scorer import JUDGED_FIELDS

_LOGGER = logging.getLogger("statement-agent.evaluator")


def _field_key(field_path: str) -> str:
    """Map a judged field path to its flat metric/row key (mirrors scorer)."""
    return field_path.replace("[]", "").replace(".", "_")


def build_eval_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build one eval-table row per OK-scored trace.

    Each row carries the trace's ``run_id``, ``bank``, strict/forgiven
    accuracy, and the seven per-field strict accuracies.  Non-OK traces
    (``ERROR`` / ``JUDGE_ERROR``) are excluded — they have no accuracy to
    tabulate; they remain counted in the summary's ``count_errors``.

    Pure stdlib (no pandas) so it is unit-testable without third-party deps;
    ``run_mlflow_evaluation`` wraps the rows in a DataFrame at call time.
    """
    rows: list[dict[str, Any]] = []
    for r in results:
        if r.get("status") != "OK":
            continue
        row: dict[str, Any] = {
            "run_id": r.get("run_id", ""),
            "bank": r.get("bank", ""),
            "strict_accuracy": r.get("strict_accuracy"),
            "narration_forgiven_accuracy": r.get("narration_forgiven_accuracy"),
        }
        per_field = r.get("per_field", {})
        for field in JUDGED_FIELDS:
            row[_field_key(field)] = per_field.get(_field_key(field))
        rows.append(row)
    return rows


def _mean_and_scores(series) -> tuple[float | None, list[float | None]]:
    """Return ``(mean of non-null values, per-row scores)`` for a Series.

    pandas turns ``None`` into ``NaN`` in numeric columns, so ``is not None``
    is insufficient — ``pd.isna`` catches both ``None`` and ``NaN``.  Shared
    by the two custom scorers so "aggregate over scored rows" means one
    thing.  Rows with no scored comparisons (accuracy ``None``) are excluded
    from the mean but kept as ``None`` in the per-row scores so the eval
    table renders them as null.
    """
    import pandas as pd

    raw = series.tolist()
    vals = [float(x) for x in raw if not pd.isna(x)]
    agg = sum(vals) / len(vals) if vals else None
    scores = [float(x) if not pd.isna(x) else None for x in raw]
    return agg, scores


def _make_strict_scorer():
    """Custom scorer: mean strict accuracy across judged traces.

    ``predictions`` resolves to the ``strict_accuracy`` column (the
    ``predictions="strict_accuracy"`` arg to ``mlflow.models.evaluate``).
    The per-row scores populate the eval table; the aggregate lands in the
    run's metrics.  ``targets`` is intentionally omitted from the signature —
    there is no ground-truth target column here (the judge already produced
    the accuracies), and a ``targets`` parameter with no target column makes
    MLflow's metric-arg resolver raise.
    """
    from mlflow.metrics import MetricValue
    from mlflow.models import make_metric

    def eval_fn(predictions, metrics):
        agg, scores = _mean_and_scores(predictions)
        return MetricValue(
            scores=scores,
            aggregate_results={"judge.mean_strict_accuracy": agg},
        )

    return make_metric(
        eval_fn=eval_fn,
        name="judge.mean_strict_accuracy",
        greater_is_better=True,
    )


def _make_forgiven_scorer():
    """Custom scorer: mean narration-forgiven accuracy across judged traces.

    The ``narration_forgiven_accuracy`` parameter name matches the input
    DataFrame column exactly, so MLflow's metric-arg resolver fetches that
    column as a Series (no ``col_mapping`` needed).  This is the "custom
    scorer via MLflow" the per-trace logging cannot express — an aggregate
    over a non-prediction column, surfaced as an evaluation metric.
    """
    from mlflow.metrics import MetricValue
    from mlflow.models import make_metric

    def eval_fn(predictions, metrics, narration_forgiven_accuracy):
        agg, scores = _mean_and_scores(narration_forgiven_accuracy)
        return MetricValue(
            scores=scores,
            aggregate_results={"judge.mean_narration_forgiven": agg},
        )

    return make_metric(
        eval_fn=eval_fn,
        name="judge.mean_narration_forgiven",
        greater_is_better=True,
    )


def _log_supplementary_metrics(summary: dict[str, Any]) -> None:
    """Log aggregate metrics not covered by the two custom scorers.

    The scorers handle ``judge.mean_strict_accuracy`` and
    ``judge.mean_narration_forgiven``.  Counts, per-field means, and the
    per-bank breakdown are logged here directly so they appear in the run's
    metrics column too (the "Evaluations" tab aggregates these).  ``None``
    values are skipped — ``MlflowClient.log_metric`` rejects them.
    """
    import mlflow

    def _log(key: str, value: Any) -> None:
        if value is not None:
            try:
                mlflow.log_metric(key, float(value))
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
            _log(f"judge.bank.{bank}.forgiven", data.get("narration_forgiven_accuracy"))


def run_mlflow_evaluation(
    results: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    experiment_id: str | None = None,
) -> dict[str, Any] | None:
    """Create an MLflow Evaluate run aggregating all judge results.

    Builds a DataFrame (one row per OK trace) and calls
    ``mlflow.models.evaluate`` with two custom scorers (strict + forgiven),
    then logs supplementary aggregate metrics (counts, per-field means,
    per-bank breakdown) and tags the run ``eval_run=true``.  The per-row
    ``eval_results_table`` artifact + the aggregate metrics render in the
    MLflow experiments "Evaluations" tab — the one-place cross-trace view
    the per-run metric logging cannot provide.

    Best-effort: any failure (mlflow unavailable, API mismatch, empty result
    set) is caught and logged so the per-trace scoring + aggregate summary
    in ``run_judge_evaluation`` still return successfully.  Returns
    ``None`` on failure or when there is nothing to evaluate, ``{"eval_run_id": ...}`` on success.
    """
    rows = build_eval_rows(results)
    if not rows:
        # No OK traces to tabulate.  Don't create an empty evaluation run —
        # it would render an empty table and add noise to the experiment.
        _LOGGER.info("skipping MLflow Evaluate run: no OK-scored traces")
        return None

    try:
        import pandas as pd
        import mlflow

        # Reuse the scorer's MLflow config (tracking URI + Databricks profile)
        # so the eval run lands in the SAME experiment as the traces.
        from judge.scorer import _ensure_mlflow_configured, _get_experiment_id

        _ensure_mlflow_configured(mlflow)
        if experiment_id is None:
            experiment_id = _get_experiment_id(mlflow)

        df = pd.DataFrame(rows)
        strict_metric = _make_strict_scorer()
        forgiven_metric = _make_forgiven_scorer()

        # mlflow.models.evaluate is the non-deprecated successor to
        # mlflow.evaluate (deprecated in MLflow 3.0).  Fall back to
        # mlflow.evaluate for older runtimes that lack the new path.
        evaluate = getattr(getattr(mlflow, "models", None), "evaluate", None)
        if evaluate is None:
            evaluate = mlflow.evaluate

        with mlflow.start_run(
            experiment_id=experiment_id, run_name="judge-evaluation"
        ) as run:
            eval_run_id = run.info.run_id
            evaluate(
                data=df,
                predictions="strict_accuracy",
                extra_metrics=[strict_metric, forgiven_metric],
            )
            _log_supplementary_metrics(summary)
            mlflow.set_tag("eval_run", "true")
            mlflow.set_tag("judge_evaluation", "true")
            mlflow.set_tag("n_judged", str(summary.get("count_judged", 0)))
            mlflow.set_tag("n_errors", str(summary.get("count_errors", 0)))

        _LOGGER.info(
            "MLflow Evaluate run created: %s (%d traces)", eval_run_id, len(rows)
        )
        return {"eval_run_id": eval_run_id, "count_rows": len(rows)}
    except Exception as exc:  # noqa: BLE001 - best-effort, never fatal
        _LOGGER.warning("MLflow Evaluate run failed: %s", exc)
        return None
