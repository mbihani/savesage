"""Workstream-2 Luna adapter shell; stdlib transport, no eager LangGraph imports."""

from contracts.models import ExtractionResult, ParseRequest
from contracts.ports import ExtractionAdapter


class LunaExtractionAdapter(ExtractionAdapter):
    def extract(self, request: ParseRequest) -> ExtractionResult:
        raise NotImplementedError("workstream 2 supplies HTTP invocation and parsing")
