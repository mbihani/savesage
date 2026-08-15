"""Workstream-5 Opus adapter shell; model transport is intentionally separate."""

from contracts.models import ExtractionResult, JudgeVerdict, ParseRequest
from contracts.ports import JudgeAdapter


class OpusJudgeAdapter(JudgeAdapter):
    def judge(self, request: ParseRequest, extraction: ExtractionResult) -> JudgeVerdict:
        raise NotImplementedError("workstream 5 supplies HTTP invocation and adjudication")
