"""Extract one PDF through the workstream-2 `ExtractionAdapter` seam."""

from contracts.models import ExtractionResult, ParseRequest
from contracts.ports import ExtractionAdapter


def extract_statement(request: ParseRequest, adapter: ExtractionAdapter) -> ExtractionResult:
    """Return a strict-GT-schema extraction for `request`."""
    raise NotImplementedError("workstream 2 owns extraction orchestration")
