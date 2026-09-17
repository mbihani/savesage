"""Three implementation seams, each owned by a downstream workstream.

The database persistence layer has been removed — the agent now returns
parsed JSON only and the client's own application persists to their RDS.
MLflow traces, the post-hoc judge, the judge scheduler, and the synchronous
API all remain.
"""

from abc import ABC, abstractmethod

from .models import ExtractionResult, JudgeVerdict, ParseRequest, TraceEvent


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

    def log_artifact(self, data: bytes, path: str, request_id: str | None = None) -> None:
        """Log a binary artifact (e.g. the source PDF) on a parse's MLflow run.

        Default no-op; the MLflow sink overrides this to call
        ``MlflowClient.log_artifact(run_id, ...)``. ``request_id`` selects which
        parse's run to attach to (runs are explicit — there is no active run to
        fall back on). Best-effort: must never raise.
        """
        pass

    def record_lifecycle(
        self,
        events: "list[TraceEvent]",
        artifacts: "list[tuple[bytes, str]] | tuple" = (),
    ) -> None:
        """Handle a request's WHOLE telemetry (all events + artifacts) as ONE unit.

        The background telemetry dispatcher submits exactly one of these per
        request, so a queue-overflow drop drops the request's telemetry as a whole
        rather than half a run lifecycle. Default: replay the buffered events
        through ``record`` and then log the artifacts — adequate for sinks with no
        explicit run lifecycle. The MLflow sink overrides this to run
        ``create_run → … → set_terminated`` as a single ordered, drop-safe unit.
        Best-effort: must never raise.
        """
        for event in events:
            self.record(event)
        request_id = events[0].request_id if events else None
        for data, path in artifacts:
            self.log_artifact(data, path, request_id)
