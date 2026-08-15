"""In-memory fake port implementations for tests and local graph runs.

This is the key to four-workstream parallelism: the graph depends only on the
``contracts/ports.py`` ABCs, and these fakes satisfy them with stdlib-only
in-memory storage. WS3 (Lakebase), WS4 (MLflow), and WS5 (Opus judge) hand in
their real implementations later; until then the graph runs end-to-end against
these fakes, so integration is incremental, not a big-bang.

No third-party imports. Safe to import on the stdlib test path.
"""

from __future__ import annotations

import copy
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from contracts.models import (
    ComparisonOutcome,
    ExtractionResult,
    FieldComparison,
    FieldFeedback,
    FieldScope,
    JudgeVerdict,
    MatchMethod,
    ParseRequest,
    TraceEvent,
)
from contracts.ports import (
    ExtractionAdapter,
    FeedbackStore,
    JudgeAdapter,
    ResultStore,
    TraceSink,
)


class InMemoryResultStore(ResultStore):
    """Dict-backed extraction/verdict store; get_* return None if absent."""

    def __init__(self) -> None:
        self.extractions: dict[str, ExtractionResult] = {}
        self.verdicts: dict[str, JudgeVerdict] = {}

    def save_extraction(self, result: ExtractionResult) -> None:
        self.extractions[result.request_id] = result

    def save_verdict(self, verdict: JudgeVerdict) -> None:
        self.verdicts[verdict.request_id] = verdict

    def get_extraction(self, request_id: str) -> ExtractionResult | None:
        return self.extractions.get(request_id)

    def get_verdict(self, request_id: str) -> JudgeVerdict | None:
        return self.verdicts.get(request_id)


class InMemoryFeedbackStore(FeedbackStore):
    """List-backed feedback store keyed by request_id."""

    def __init__(self) -> None:
        self._items: list[FieldFeedback] = []

    def append_feedback(self, feedback: FieldFeedback) -> None:
        self._items.append(feedback)

    def list_feedback(self, request_id: str) -> Sequence[FieldFeedback]:
        return [f for f in self._items if f.request_id == request_id]


class InMemoryTraceSink(TraceSink):
    """Captures trace events in order; useful for assertions.

    Also captures artifacts logged via ``log_artifact`` so tests can assert
    that the PDF was logged during persist.
    """

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []
        self.artifacts: list[tuple[bytes, str]] = []

    def record(self, event: TraceEvent) -> None:
        self.events.append(event)

    def log_artifact(self, data: bytes, path: str) -> None:
        self.artifacts.append((data, path))


class FakeExtractionAdapter(ExtractionAdapter):
    """Returns a canned, schema-valid payload without any HTTP call.

    Accepts an optional ``mutator(state)`` to let a test corrupt the payload
    (e.g. set a negative amount) and exercise the validation node.
    """

    def __init__(self, payload: dict[str, Any] | None = None, *, mutator=None) -> None:
        self._payload = payload
        self._mutator = mutator
        self.calls: list[ParseRequest] = []

    def extract(self, request: ParseRequest) -> ExtractionResult:
        self.calls.append(request)
        # deepcopy so a mutator's nested mutation never contaminates the caller's
        # template across calls (real endpoint responses are always fresh).
        payload = copy.deepcopy(self._payload) if self._payload is not None else _synthetic_valid_payload()
        if self._mutator is not None:
            self._mutator(payload)
        # Mirror the REAL adapter: schema_valid is left False here; the validate
        # node propagates the validated value via dataclasses.replace. Setting it
        # in the fake would hide the exact class of defect (BLOCKING 2) that fakes
        # are supposed to catch.
        return ExtractionResult(
            request_id=request.request_id,
            payload=payload,
            model_id="fake-luna",
            latency_ms=0.0,
            raw_response_id="fake-resp-id",
            schema_valid=False,
        )


class FakeJudgeAdapter(JudgeAdapter):
    """Returns a canned AGREE verdict on the seven judged fields."""

    def __init__(self, *, outcome: ComparisonOutcome = ComparisonOutcome.AGREE) -> None:
        self._outcome = outcome
        self.calls: list[tuple[ParseRequest, ExtractionResult]] = []

    def judge(self, request: ParseRequest, extraction: ExtractionResult) -> JudgeVerdict:
        self.calls.append((request, extraction))
        comparisons: list[FieldComparison] = []
        cards = extraction.payload.get("cards") or []
        if isinstance(cards, list):
            for idx, card in enumerate(cards):
                if not isinstance(card, dict):
                    continue
                meta = card.get("cardMeta") or {}
                comparisons.append(FieldComparison(
                    "cards[].cardMeta.cardDisplayName", meta.get("cardDisplayName"),
                    meta.get("cardDisplayName"), self._outcome, FieldScope.SCALAR, card_index=idx,
                ))
                comparisons.append(FieldComparison(
                    "cards[].cardMeta.lastFourDigit", meta.get("lastFourDigit"),
                    meta.get("lastFourDigit"), self._outcome, FieldScope.SCALAR, card_index=idx,
                ))
        rewards = extraction.payload.get("rewards") or {}
        if isinstance(rewards, dict):
            comparisons.append(FieldComparison(
                "rewards.pointsEarnedThisCycle", rewards.get("pointsEarnedThisCycle"),
                rewards.get("pointsEarnedThisCycle"), self._outcome, FieldScope.SCALAR,
            ))
            comparisons.append(FieldComparison(
                "rewards.closingPoints", rewards.get("closingPoints"),
                rewards.get("closingPoints"), self._outcome, FieldScope.SCALAR,
            ))
        txns = extraction.payload.get("transactions") or []
        if isinstance(txns, list):
            for idx, txn in enumerate(txns):
                if not isinstance(txn, dict):
                    continue
                for field in ("date", "description", "amount"):
                    comparisons.append(FieldComparison(
                        f"transactions[].{field}", txn.get(field), txn.get(field),
                        self._outcome, FieldScope.TRANSACTION_ROW,
                        MatchMethod.DESCRIPTION_SIMILARITY_1TO1,
                        expected_row_index=idx, actual_row_index=idx, similarity=1.0,
                    ))
        return JudgeVerdict(
            request_id=request.request_id,
            judge_model_id="fake-opus",
            comparisons=tuple(comparisons),
            latency_ms=0.0,
            match_method=MatchMethod.DESCRIPTION_SIMILARITY_1TO1,
            raw_response_id="fake-judge-id",
            summary=f"fake judge: {self._outcome.value}",
        )


class FailingExtractionAdapter(ExtractionAdapter):
    """Always raises; used to test the terminal EXTRACTION_FAILED path."""

    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc or RuntimeError("synthetic extraction failure")

    def extract(self, request: ParseRequest) -> ExtractionResult:
        raise self._exc


def _synthetic_valid_payload() -> dict[str, Any]:
    """A minimal payload that passes both schema conformance and GT rules.

    Obviously synthetic: card holder is "SYNTHETIC CARDHOLDER", last four are
    "0000", amounts are round 1.0/2.0. No real PII.
    """
    return {
        "statementMeta": {
            "issuerName": "SYNTHETIC BANK",
            "statementDate": "01/04/2026",
            "dueDate": "20/04/2026",
            "statementPeriodStart": "01/03/2026",
            "statementPeriodEnd": "31/03/2026",
            "rawStatementId": "synthetic-001",
        },
        "statementLevelSummary": {
            "totalAmountDue": 3.0,
            "totalMinimumAmountDue": 1.0,
            "totalCreditLimit": 100000.0,
            "availableCreditLimit": 99997.0,
        },
        "cards": [{
            "cardMeta": {
                "cardDisplayName": "SYNTHETIC CARDHOLDER",
                "productFamily": "SYNTHETIC",
                "lastFourDigit": "0000",
                "network": "VISA",
                "isPrimaryCard": True,
            },
            "bigPicture": {"cardCreditLimit": 100000.0, "cardAvailableCreditLimit": 99997.0},
        }],
        "transactions": [
            {"date": "05/03/2026", "description": "SYNTHETIC PURCHASE", "amount": 1.0,
             "direction": "DEBIT", "txnType": "PURCHASE",
             "rewardPointsOnThisTransaction": 1, "currency": "INR"},
            {"date": "06/03/2026", "description": "SYNTHETIC PAYMENT", "amount": 2.0,
             "direction": "CREDIT", "txnType": "PAYMENT",
             "rewardPointsOnThisTransaction": 0, "currency": "INR"},
        ],
        "rewards": {
            "programType": "SYNTHETIC",
            "openingPoints": 0,
            "pointsEarnedThisCycle": 1,
            "pointsRedeemedThisCycle": 0,
            "closingPoints": 1,
            "pointsExpiringNext30Days": 0,
            "pointsExpiringNext60Days": 0,
            "bonusPointsThisCycle": 0,
        },
    }


def make_synthetic_request(bank=None) -> ParseRequest:
    """A request with an obviously-synthetic in-memory PDF (no file I/O)."""
    from contracts.models import Bank
    return ParseRequest(
        pdf=b"%PDF-1.4 synthetic not a real pdf",
        filename="synthetic.pdf",
        bank=bank or Bank.HDFC,
        request_id="synthetic-req-001",
    )


def make_all_fakes(
    *,
    extraction_payload: dict[str, Any] | None = None,
    extraction_mutator=None,
    judge_outcome: ComparisonOutcome = ComparisonOutcome.AGREE,
) -> tuple[InMemoryResultStore, InMemoryFeedbackStore, InMemoryTraceSink, FakeExtractionAdapter, FakeJudgeAdapter]:
    """Convenience: build the full set of in-memory fakes in one call."""
    return (
        InMemoryResultStore(),
        InMemoryFeedbackStore(),
        InMemoryTraceSink(),
        FakeExtractionAdapter(extraction_payload, mutator=extraction_mutator),
        FakeJudgeAdapter(outcome=judge_outcome),
    )


# Re-exported so test assertions can reference the synthetic timestamp shape.
_NOW = datetime.now(UTC)
_ = _NOW  # kept to document the UTC default used by TraceEvent constructions
