"""Stdlib-only mappings between frozen dataclasses and database values."""

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from contracts.models import (ComparisonOutcome, ExtractionResult, FieldComparison,
    FieldFeedback, FieldScope, JudgeVerdict, MatchMethod, TokenUsage)
from contracts.paths import is_valid_feedback_path


def _first(payload: dict[str, Any], *paths: tuple[str, ...]) -> Any:
    for path in paths:
        value: Any = payload
        for key in path:
            if not isinstance(value, dict) or key not in value:
                break
            value = value[key]
        else:
            return value
    return None


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        return None


def promoted_columns(payload: dict[str, Any]) -> tuple[Any, ...]:
    cards = payload.get("cards")
    card = cards[0] if isinstance(cards, list) and cards and isinstance(cards[0], dict) else {}
    meta = card.get("cardMeta", {}) if isinstance(card, dict) else {}
    return (_first(payload, ("bank",), ("bankName",), ("statement", "bank")),
            _first(payload, ("statementDate",), ("statement", "date")),
            meta.get("cardDisplayName") if isinstance(meta, dict) else None,
            meta.get("lastFourDigit") if isinstance(meta, dict) else None,
            _decimal(_first(payload, ("rewards", "pointsEarnedThisCycle"))),
            _decimal(_first(payload, ("rewards", "closingPoints"))))


def extraction_from_row(row: tuple[Any, ...]) -> ExtractionResult:
    return ExtractionResult(row[0], row[1], row[2], row[3], TokenUsage(**(row[4] or {})), row[5], row[6])


def verdict_to_dict(verdict: JudgeVerdict) -> dict[str, Any]:
    data = asdict(verdict)
    data["match_method"] = verdict.match_method.value
    for item, comparison in zip(data["comparisons"], verdict.comparisons):
        item.update(outcome=comparison.outcome.value, scope=comparison.scope.value,
                    match_method=comparison.match_method.value)
    return data


def verdict_from_dict(data: dict[str, Any]) -> JudgeVerdict:
    comparisons = tuple(FieldComparison(**{**item,
        "outcome": ComparisonOutcome(item["outcome"]), "scope": FieldScope(item["scope"]),
        "match_method": MatchMethod(item["match_method"])}) for item in data["comparisons"])
    return JudgeVerdict(**{**data, "comparisons": comparisons,
                           "match_method": MatchMethod(data["match_method"])})


def feedback_values(feedback: FieldFeedback) -> tuple[Any, ...]:
    if not is_valid_feedback_path(feedback.field_path):
        raise ValueError(f"invalid canonical feedback path: {feedback.field_path}")
    return (feedback.request_id, feedback.field_path, feedback.original_value,
            feedback.corrected_value, feedback.accepted, feedback.actor, feedback.timestamp)


def feedback_from_row(row: tuple[Any, ...]) -> FieldFeedback:
    timestamp = datetime.fromisoformat(row[6]) if isinstance(row[6], str) else row[6]
    return FieldFeedback(row[0], row[1], row[2], row[3], row[4], row[5], timestamp)
