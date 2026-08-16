"""Five implementation seams, each owned by a downstream workstream."""

from abc import ABC, abstractmethod
from collections.abc import Sequence

from .models import Bank, ExtractionResult, FieldFeedback, JudgeVerdict, ParseRequest, TraceEvent


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
    def save_extraction(self, result: ExtractionResult, bank: Bank) -> None:
        raise NotImplementedError

    @abstractmethod
    def save_verdict(self, verdict: JudgeVerdict) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_extraction(self, request_id: str) -> ExtractionResult | None:
        raise NotImplementedError

    @abstractmethod
    def get_verdict(self, request_id: str) -> JudgeVerdict | None:
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
    """Workstream 4: record framework-neutral trace events in MLflow.

    ``log_artifact`` has a default no-op implementation so existing concrete
    sinks (e.g. the in-memory test fake) inherit it without breaking; the
    MLflow-backed sink overrides it to persist the PDF alongside the trace so
    the post-hoc judge can re-read the PDF later.
    """

    @abstractmethod
    def record(self, event: TraceEvent) -> None:
        raise NotImplementedError

    def log_artifact(self, data: bytes, path: str) -> None:
        """Log a binary artifact (e.g. the source PDF) on the current trace.

        Default no-op; the MLflow sink overrides this to call
        ``mlflow.log_artifact``. Best-effort: must never raise.
        """
        pass
