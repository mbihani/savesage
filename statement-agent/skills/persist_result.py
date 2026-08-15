"""Persist extraction and verdict through workstream 3's store seam."""

from contracts.models import ExtractionResult, JudgeVerdict
from contracts.ports import ResultStore


def persist_result(result: ExtractionResult, verdict: JudgeVerdict | None, store: ResultStore) -> None:
    store.save_extraction(result)
    if verdict is not None:
        if verdict.request_id != result.request_id:
            raise ValueError("extraction and verdict request_id must match")
        store.save_verdict(verdict)
