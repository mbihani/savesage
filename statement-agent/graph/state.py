"""Typed graph state and stage/outcome enums (stdlib-only, unit-testable).

This is the single object that flows through every LangGraph node. It carries
the immutable :class:`ParseRequest`, the progressively-populated extraction and
verdict, the validation outcome, and a non-fatal error sink. Nodes read from and
write to this state; the graph never reaches into a node's locals.

Kept stdlib-only so the state-transition and routing tests import without
langgraph installed.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from contracts.models import (
    ExtractionResult,
    FieldFeedback,
    JudgeVerdict,
    ParseRequest,
)


class Stage(str, Enum):
    """Linear pipeline stages; a failure records the stage that produced it."""

    INIT = "INIT"
    ROUTED = "ROUTED"
    EXTRACTED = "EXTRACTED"
    VALIDATED = "VALIDATED"
    PERSISTED = "PERSISTED"
    JUDGED = "JUDGED"


class Outcome(str, Enum):
    """Terminal disposition of one parse run.

    SUCCESS means every stage completed. PARTIAL means the extraction succeeded
    enough to persist and show, but validation flagged schema/rule violations.
    EXTRACTION_FAILED and JUDGE_FAILED are hard failures of a single stage that
    short-circuit the rest of the pipeline.
    """

    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    JUDGE_FAILED = "JUDGE_FAILED"


@dataclass(slots=True)
class GraphState:
    """Mutable per-run state shared by every node.

    ``request`` and ``prompt`` are set by the caller/router. ``errors`` is an
    ordered list of human-readable stage failures; an empty list means a clean
    run. ``verdict`` and ``feedback`` stay ``None``/empty when the judge is
    skipped (validation short-circuit, or no judge adapter injected).
    """

    request: ParseRequest
    prompt: str | None = None
    extraction: ExtractionResult | None = None
    schema_valid: bool = False
    validation_errors: list[str] = field(default_factory=list)
    verdict: JudgeVerdict | None = None
    feedback: list[FieldFeedback] = field(default_factory=list)
    stage: Stage = Stage.INIT
    outcome: Outcome | None = None
    errors: list[str] = field(default_factory=list)

    # ---- read-only views used by nodes and tests ------------------------

    @property
    def request_id(self) -> str:
        return self.request.request_id

    def mark_failure(self, stage: Stage, message: str) -> None:
        """Record a non-fatal or terminal failure at `stage`."""
        self.stage = stage
        self.errors.append(message)

    def as_summary(self) -> dict[str, Any]:
        """Flat dict snapshot for tracing/logging; never the full payload."""
        return {
            "request_id": self.request_id,
            "bank": self.request.bank.value,
            "stage": self.stage.value,
            "outcome": self.outcome.value if self.outcome else None,
            "schema_valid": self.schema_valid,
            "n_validation_errors": len(self.validation_errors),
            "n_errors": len(self.errors),
            "has_verdict": self.verdict is not None,
            "n_transactions": _txn_count(self.extraction),
        }


def _txn_count(extraction: ExtractionResult | None) -> int | None:
    if extraction is None:
        return None
    txns = extraction.payload.get("transactions")
    return len(txns) if isinstance(txns, list) else None
