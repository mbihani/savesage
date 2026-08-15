"""Judge seven contract fields through workstream 5's `JudgeAdapter`."""

from contracts.models import ExtractionResult, JudgeVerdict, ParseRequest
from contracts.ports import JudgeAdapter


def judge_statement(request: ParseRequest, result: ExtractionResult, adapter: JudgeAdapter) -> JudgeVerdict:
    raise NotImplementedError("workstream 5 owns judging")
