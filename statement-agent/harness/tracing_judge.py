"""Pure judge-verdict -> MLflow metrics/assessment mapping (stdlib-only, no mlflow).

WS5 produces a :class:`JudgeVerdict` with per-field :class:`FieldComparison`s over
exactly seven fields (four scalars + three transaction-row fields, the latter
appearing once per matched row). This module maps a verdict to:

- per-field agreement fractions (1.0/0.0 for scalars; row-agreement fraction for
  transaction fields), keyed ``judge.<sanitized_field>``;
- an aggregate ``judge.accuracy`` = total agreements / total comparisons;
- a ``judge.comparisons`` count.

These are delivered as a single trace-bound assessment via ``mlflow.log_feedback``
(see harness/tracing.py), since a verdict belongs to a trace, not a run.
"""

from collections import defaultdict
from typing import Any

from contracts.models import ComparisonOutcome, JudgeVerdict

from .tracing_keys import ASSESSMENT_LLM_JUDGE, JUDGE_ASSESSMENT_NAME


def _agree(outcome: Any) -> bool:
    """ComparisonOutcome is a str enum; compare by value defensively."""
    value = getattr(outcome, "value", outcome)
    return value == ComparisonOutcome.AGREE.value


def _metric_key(field_path: str) -> str:
    return "judge." + field_path.replace("[]", "").replace(".", "_")


def verdict_to_metrics(verdict: JudgeVerdict) -> dict[str, float]:
    """Per-field agreement fractions + aggregate accuracy + comparison count."""
    by_field: dict[str, list[float]] = defaultdict(list)
    for comparison in verdict.comparisons:
        by_field[comparison.field_path].append(1.0 if _agree(comparison.outcome) else 0.0)

    metrics: dict[str, float] = {}
    total_agree = 0.0
    total = 0
    for field_path, agrees in by_field.items():
        metrics[_metric_key(field_path)] = sum(agrees) / len(agrees) if agrees else 0.0
        total_agree += sum(agrees)
        total += len(agrees)
    metrics["judge.accuracy"] = (total_agree / total) if total else 0.0
    metrics["judge.comparisons"] = float(total)
    return metrics


def build_judge_feedback(verdict: JudgeVerdict) -> dict[str, Any]:
    """Build the log_feedback payload for a judge verdict (trace-bound assessment)."""
    metrics = verdict_to_metrics(verdict)
    accuracy = metrics["judge.accuracy"]
    per_field = {
        k.removeprefix("judge."): v
        for k, v in metrics.items()
        if k not in ("judge.accuracy", "judge.comparisons")
    }
    return {
        "name": JUDGE_ASSESSMENT_NAME,
        "value": accuracy,
        "source_type": ASSESSMENT_LLM_JUDGE,
        "source_id": verdict.judge_model_id,
        "rationale": verdict.summary or f"judge accuracy {accuracy:.3f}",
        "metadata": {
            "judge_model_id": verdict.judge_model_id,
            "comparisons": metrics["judge.comparisons"],
            "accuracy": accuracy,
            "per_field": per_field,
            "match_method": getattr(verdict.match_method, "value", str(verdict.match_method)),
            "latency_ms": verdict.latency_ms,
        },
    }
