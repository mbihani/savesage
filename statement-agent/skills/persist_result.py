"""Persist extraction and verdict through workstream 3's store seam."""

from contracts.models import ExtractionResult, JudgeVerdict
from contracts.ports import ResultStore


def persist_result(result: ExtractionResult, verdict: JudgeVerdict | None, store: ResultStore) -> None:
    raise NotImplementedError("workstream 3 owns persistence")
