"""Persist extraction and verdict through workstream 3's store seam."""

from contracts.models import Bank, ExtractionResult, JudgeVerdict
from contracts.ports import ResultStore


def persist_result(result: ExtractionResult, verdict: JudgeVerdict | None,
                   store: ResultStore, bank: Bank) -> None:
    store.save_extraction(result, bank)
    if verdict is not None:
        if verdict.request_id != result.request_id:
            raise ValueError("extraction and verdict request_id must match")
        store.save_verdict(verdict)
