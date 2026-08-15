"""Judge seven contract fields through workstream 5's `JudgeAdapter`."""

from contracts.models import ExtractionResult, JudgeVerdict, ParseRequest
from contracts.ports import JudgeAdapter


def judge_statement(request: ParseRequest, result: ExtractionResult, adapter: JudgeAdapter) -> JudgeVerdict:
    """Judge one extraction against an independent native-PDF reading."""
    if request.request_id != result.request_id:
        raise ValueError("request and extraction request_id differ")
    return adapter.judge(request, result)
