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


class MatchMethod(str, Enum):
    DIRECT = "DIRECT"
    DESCRIPTION_SIMILARITY_1TO1 = "DESCRIPTION_SIMILARITY_1TO1"


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
    candidate/reference row indices. `expected` is PDF ground truth read by the
    Opus-5 judge; `actual` is the extraction value under test. Transaction
    matching is description-similarity-only, strict 1:1, and order-insensitive.
    """

    field_path: str
    expected: JsonValue
    actual: JsonValue
    outcome: ComparisonOutcome
    scope: FieldScope
    match_method: MatchMethod = MatchMethod.DIRECT
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
        required_method = MatchMethod.DESCRIPTION_SIMILARITY_1TO1 if is_row else MatchMethod.DIRECT
        if self.match_method is not required_method:
            raise ValueError("match_method and scope disagree")


@dataclass(frozen=True, slots=True)
class JudgeVerdict:
    """Judge output; `match_method` summarizes transaction-row matching."""

    request_id: str
    judge_model_id: str
    comparisons: tuple[FieldComparison, ...]
    latency_ms: float
    match_method: MatchMethod = MatchMethod.DESCRIPTION_SIMILARITY_1TO1
    raw_response_id: str | None = None
    summary: str | None = None


@dataclass(frozen=True, slots=True)
class FieldFeedback:
    """Client decision for one canonical concrete dot path.

    Array indices are zero-based decimal integers. Examples:
    `cards.0.cardMeta.cardDisplayName`, `transactions.14.amount`, and
    `rewards.closingPoints`. Templates (`[]`), wildcards, JSON Pointer slashes,
    negative indices, and leading-zero indices are invalid.
    """

    request_id: str
    field_path: str
    original_value: JsonValue
    corrected_value: JsonValue
    accepted: bool
    actor: str
    timestamp: datetime

    def __post_init__(self) -> None:
        from .paths import is_valid_feedback_path

        if not is_valid_feedback_path(self.field_path):
            raise ValueError(f"invalid canonical feedback path: {self.field_path}")

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
    span_id: str | None = None
    parent_span_id: str | None = None
    # Payload data for span inputs/outputs.  When non-None these are passed to
    # ``span.set_inputs()`` / ``span.set_outputs()`` by the MLflow sink so the
    # trace view shows the actual extraction data, not just metadata attributes.
    # The recursive PII scrubber (``redact_telemetry_attributes``) is applied
    # before they reach MLflow, so nested PII keys (cardholder name, transaction
    # description, etc.) are redacted while the structure and non-PII values
    # remain visible.
    inputs: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None
