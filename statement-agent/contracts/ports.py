"""Five implementation seams, each owned by a downstream workstream."""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from .models import ExtractionResult, FieldFeedback, JudgeVerdict, ParseRequest, TraceEvent


class ExtractionAdapter(ABC):
    """Workstream 2: invoke Luna and return a schema-bearing extraction."""

    @abstractmethod
    def extract(self, request: ParseRequest) -> ExtractionResult:
        raise NotImplementedError


class JudgeAdapter(ABC):
    """Workstream 5: compare extraction with PDF evidence using Opus 5."""

    @abstractmethod
    def judge(self, request: ParseRequest, extraction: ExtractionResult) -> JudgeVerdict:
        raise NotImplementedError


class ResultStore(ABC):
    """Workstream 3: durable extraction/verdict persistence and retrieval."""

    @abstractmethod
    def save_extraction(self, result: ExtractionResult) -> None:
        raise NotImplementedError

    @abstractmethod
    def save_verdict(self, verdict: JudgeVerdict) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_extraction(self, request_id: str) -> ExtractionResult | None:
        raise NotImplementedError


class FeedbackStore(ABC):
    """Workstream 3: append and retrieve field-level client feedback."""

    @abstractmethod
    def append_feedback(self, feedback: FieldFeedback) -> None:
        raise NotImplementedError

    @abstractmethod
    def list_feedback(self, request_id: str) -> Sequence[FieldFeedback]:
        raise NotImplementedError


class TraceSink(ABC):
    """Workstream 4: record framework-neutral trace events in MLflow."""

    @abstractmethod
    def record(self, event: TraceEvent) -> None:
        raise NotImplementedError
