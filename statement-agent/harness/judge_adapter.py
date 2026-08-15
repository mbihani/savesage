"""Opus-5 adapter: independently read the PDF, then compare locally."""

import json
from pathlib import Path

from contracts.models import ExtractionResult, JudgeVerdict, ParseRequest
from contracts.ports import JudgeAdapter
from judge.aggregation import aggregate
from judge.comparison import build_comparisons, judge_error_comparisons
from judge.opus import completion_reason, extract_response_text, invoke_opus, parse_ground_truth

PROMPT_PATH = Path(__file__).resolve().parents[1] / "judge" / "prompt_v1.txt"


class OpusJudgeAdapter(JudgeAdapter):
    def judge(self, request: ParseRequest, extraction: ExtractionResult) -> JudgeVerdict:
        pdf = request.pdf.read_bytes() if isinstance(request.pdf, Path) else request.pdf
        response, latency_ms = invoke_opus(pdf, PROMPT_PATH.read_text(encoding="utf-8"))
        reason = completion_reason(response)
        judge_error = None
        if reason in {"length", "max_tokens"}:
            judge_error = f"judge response truncated ({reason})"
        else:
            try:
                expected = parse_ground_truth(extract_response_text(response))
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                judge_error = f"judge response unusable: {type(exc).__name__}: {exc}"
        comparisons = (judge_error_comparisons(judge_error) if judge_error
                       else build_comparisons(request, expected, extraction.payload))
        summary = aggregate(comparisons)
        summary["status"] = "JUDGE_ERROR" if judge_error else "OK"
        if judge_error:
            summary["judge_error"] = judge_error
        if reason is not None:
            summary["completion_reason"] = reason
        choices = response.get("choices") or [{}]
        response_id = response.get("id") or choices[0].get("id")
        return JudgeVerdict(request.request_id, "databricks-claude-opus-5", comparisons,
                            latency_ms, raw_response_id=response_id,
                            summary=json.dumps(summary, sort_keys=True))
