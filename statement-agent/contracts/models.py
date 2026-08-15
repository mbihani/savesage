"""Stable data contracts. This module deliberately has no third-party imports."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, TypeAlias

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
PdfSource: TypeAlias = bytes | Path


class Bank(str, Enum):
    HDFC = "HDFC"
    ICICI = "ICICI"
    SBI = "SBI"
    AXIS = "AXIS"


class ComparisonOutcome(str, Enum):
    AGREE = "AGREE"
    DISAGREE = "DISAGREE"
    FORMAT_ONLY = "FORMAT_ONLY"
    ABSENT_IN_PDF = "ABSENT_IN_PDF"
    UNMATCHED_ROW = "UNMATCHED_ROW"


class FieldScope(str, Enum):
    SCALAR = "SCALAR"
    TRANSACTION_ROW = "TRANSACTION_ROW"


class FeedbackDisposition(str, Enum):
    ACCEPT = "ACCEPT"
    CORRECT = "CORRECT"


@dataclass(frozen=True, slots=True)
class ParseRequest:
    """One PDF extraction request; `pdf` is raw bytes or a filesystem Path."""

    pdf: PdfSource
    filename: str
    bank: Bank
    request_id: str


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """A GT_SCHEMA payload plus transport metadata."""

    request_id: str
    payload: dict[str, JsonValue]
    model_id: str
    latency_ms: float
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    raw_response_id: str | None = None
    schema_valid: bool = False


JUDGED_SCALAR_FIELDS = frozenset({
    "cards[].cardMeta.cardDisplayName",
    "cards[].cardMeta.lastFourDigit",
    "rewards.pointsEarnedThisCycle",
    "rewards.closingPoints",
})
JUDGED_TRANSACTION_FIELDS = frozenset({
    "transactions[].date",
    "transactions[].description",
    "transactions[].amount",
})
JUDGED_FIELDS = JUDGED_SCALAR_FIELDS | JUDGED_TRANSACTION_FIELDS


@dataclass(frozen=True, slots=True)
class FieldComparison:
    """Agreement on one of exactly seven fields.

    Scalar comparisons use `scope=SCALAR`; card fields may identify a card with
    `card_index`. Transaction fields use `scope=TRANSACTION_ROW` and carry the
    candidate/reference row indices. `match_method` must name the evidence used
    for matching; transaction matching is description-similarity-only, strict
    1:1, and order-insensitive.
    """

    field_path: str
    expected: JsonValue
    actual: JsonValue
    outcome: ComparisonOutcome
    scope: FieldScope
    match_method: str
    card_index: int | None = None
    expected_row_index: int | None = None
    actual_row_index: int | None = None
    similarity: float | None = None
    rationale: str | None = None

    def __post_init__(self) -> None:
        if self.field_path not in JUDGED_FIELDS:
            raise ValueError(f"unsupported judged field: {self.field_path}")
        is_row = self.field_path in JUDGED_TRANSACTION_FIELDS
        if is_row != (self.scope is FieldScope.TRANSACTION_ROW):
            raise ValueError("field_path and scope disagree")


@dataclass(frozen=True, slots=True)
class JudgeVerdict:
    request_id: str
    judge_model_id: str
    comparisons: tuple[FieldComparison, ...]
    match_method: str
    latency_ms: float
    raw_response_id: str | None = None
    summary: str | None = None


@dataclass(frozen=True, slots=True)
class FieldFeedback:
    """Client acceptance/correction for one JSON-pointer-ish field path."""

    request_id: str
    field_path: str
    original_value: JsonValue
    corrected_value: JsonValue
    accepted: bool
    actor: str
    timestamp: datetime

    @property
    def disposition(self) -> FeedbackDisposition:
        return FeedbackDisposition.ACCEPT if self.accepted else FeedbackDisposition.CORRECT


@dataclass(frozen=True, slots=True)
class TraceEvent:
    request_id: str
    name: str
    started_at: datetime
    ended_at: datetime
    attributes: Mapping[str, JsonValue] = field(default_factory=dict)
    error: str | None = None
