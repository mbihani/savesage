"""Pure judge-verdict -> MLflow metrics/assessment mapping (stdlib-only, no mlflow).

WS5 produces a :class:`JudgeVerdict` with per-field :class:`FieldComparison`s over
exactly seven fields. This module maps a verdict to MLflow metrics/assessments.

ACCURACY POLICY — mirrors WS5's ``judge/aggregation.py`` EXACTLY (review B5).

WS5 (PR #13) computes strict and narration-forgiven aggregates in
``judge/aggregation.py``. The ``JudgeVerdict`` dataclass does NOT carry
precomputed aggregates (only the comparisons tuple), so we recompute here —
mirroring WS5's semantics, NOT inventing a divergent policy (two different
accuracy numbers for the same parse would be worse than one imperfect one).
We do NOT import WS5's module (ownership boundary); we depend on the frozen
``JudgeVerdict``/``FieldComparison`` dataclasses only.

The policy (from judge/aggregation.py ``stats()``):

- **scored** = all comparisons EXCEPT ``ABSENT_IN_PDF``. A field genuinely absent
  from the PDF is not a model error, so it is excluded from the DENOMINATOR (not
  merely scored as wrong).
- **correct (strict)** = ``AGREE`` or ``FORMAT_ONLY``. Format-only differences are
  NOT charged as errors (this repo's established scoring discipline).
- **correct (narration-forgiven)** = strict correct, OR ``DISAGREE`` on
  ``transactions[].description`` (description narration is forgiven).
- ``UNMATCHED_ROW`` is scored (in the denominator) and is NOT correct.
- **accuracy** = correct / scored, or ``None`` when scored is empty.

Denominator policy for transaction rows: each ``FieldComparison`` is scored
independently per its ``field_path`` (mirroring WS5). An unmatched transaction
row contributes one ``UNMATCHED_ROW`` comparison PER transaction field
(date/description/amount), each scored independently. This is WS5's established
behavior; we document it rather than diverge.

We emit ``judge.accuracy`` (strict) as the assessment value and carry both
strict + narration-forgiven plus per-field breakdown in metadata.
"""

from collections import defaultdict
from typing import Any

from contracts.models import ComparisonOutcome, FieldComparison, JudgeVerdict

from .tracing_keys import ASSESSMENT_LLM_JUDGE, JUDGE_ASSESSMENT_NAME

# Outcomes counted as correct in the STRICT reading (mirror judge/aggregation.py).
_STRICT_CORRECT = frozenset({ComparisonOutcome.AGREE, ComparisonOutcome.FORMAT_ONLY})


def _outcome_value(outcome: Any) -> str:
    """ComparisonOutcome is a str enum; compare by value defensively."""
    return getattr(outcome, "value", outcome)


def _is_scored(outcome: Any) -> bool:
    """ABSENT_IN_PDF is excluded from the denominator (not a model error)."""
    return _outcome_value(outcome) != ComparisonOutcome.ABSENT_IN_PDF.value


def _is_correct_strict(comparison: FieldComparison) -> bool:
    return _outcome_value(comparison.outcome) in {
        o.value for o in _STRICT_CORRECT
    }


def _is_correct_forgiven(comparison: FieldComparison) -> bool:
    if _is_correct_strict(comparison):
        return True
    # Narration-forgiven: a DISAGREE on transactions[].description is forgiven.
    return (
        comparison.field_path == "transactions[].description"
        and _outcome_value(comparison.outcome) == ComparisonOutcome.DISAGREE.value
    )


def _metric_key(field_path: str) -> str:
    return "judge." + field_path.replace("[]", "").replace(".", "_")


def _stats(items: list[FieldComparison], forgive_narration: bool = False) -> dict[str, Any]:
    """Mirror judge/aggregation.py stats() exactly."""
    scored = [item for item in items if _is_scored(item.outcome)]
    correct_fn = _is_correct_forgiven if forgive_narration else _is_correct_strict
    correct = sum(1 for item in scored if correct_fn(item))
    return {
        "correct": correct,
        "scored": len(scored),
        "accuracy": (correct / len(scored)) if scored else None,
    }


def verdict_to_metrics(verdict: JudgeVerdict) -> dict[str, float]:
    """Per-field + aggregate accuracy mirroring judge/aggregation.py (strict).

    Returns a flat dict with ``judge.accuracy`` (strict), ``judge.accuracy_forgiven``
    (narration-forgiven), ``judge.comparisons`` (total), ``judge.scored`` (denominator
    after excluding ABSENT_IN_PDF), and per-field ``judge.<field>`` strict accuracies.
    A None accuracy (no scored comparisons) is emitted as ``None``.
    """
    all_items = list(verdict.comparisons)
    grouped: dict[str, list[FieldComparison]] = defaultdict(list)
    for comparison in all_items:
        grouped[comparison.field_path].append(comparison)

    metrics: dict[str, Any] = {}
    for field_path in sorted(grouped):
        s = _stats(grouped[field_path])
        metrics[_metric_key(field_path)] = s["accuracy"]  # may be None
    strict = _stats(all_items)
    forgiven = _stats(all_items, forgive_narration=True)
    metrics["judge.accuracy"] = strict["accuracy"]
    metrics["judge.accuracy_forgiven"] = forgiven["accuracy"]
    metrics["judge.comparisons"] = float(len(all_items))
    metrics["judge.scored"] = float(strict["scored"])
    metrics["judge.correct"] = float(strict["correct"])
    return metrics


def build_judge_feedback(verdict: JudgeVerdict) -> dict[str, Any]:
    """Build the log_feedback payload for a judge verdict (trace-bound assessment).

    The assessment ``value`` is the strict accuracy (or ``None`` when no scored
    comparisons); both strict and narration-forgiven accuracies are carried in
    metadata alongside the per-field breakdown.
    """
    metrics = verdict_to_metrics(verdict)
    accuracy = metrics["judge.accuracy"]
    per_field = {
        k.removeprefix("judge."): v
        for k, v in metrics.items()
        if k not in ("judge.accuracy", "judge.accuracy_forgiven",
                     "judge.comparisons", "judge.scored", "judge.correct")
    }
    rationale = verdict.summary or (
        f"judge strict accuracy {accuracy:.3f}" if accuracy is not None
        else "no scored comparisons"
    )
    return {
        "name": JUDGE_ASSESSMENT_NAME,
        "value": accuracy,  # may be None — log_feedback accepts value=None
        "source_type": ASSESSMENT_LLM_JUDGE,
        "source_id": verdict.judge_model_id,
        "rationale": rationale,
        "metadata": {
            "judge_model_id": verdict.judge_model_id,
            "comparisons": metrics["judge.comparisons"],
            "scored": metrics["judge.scored"],
            "correct": metrics["judge.correct"],
            "accuracy_strict": accuracy,
            "accuracy_narration_forgiven": metrics["judge.accuracy_forgiven"],
            "per_field": per_field,
            "match_method": getattr(verdict.match_method, "value", str(verdict.match_method)),
            "latency_ms": verdict.latency_ms,
            "policy": "mirrors judge/aggregation.py: ABSENT_IN_PDF excluded from denominator; "
                      "FORMAT_ONLY counts as correct; narration-forgiven forgives DISAGREE on "
                      "transactions[].description",
        },
    }
