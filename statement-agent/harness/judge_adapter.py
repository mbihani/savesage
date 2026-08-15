"""Opus-5 adapter: independently read the PDF, then compare locally."""

import json
from pathlib import Path

from contracts.models import ExtractionResult, JudgeVerdict, ParseRequest
from contracts.ports import JudgeAdapter
from judge.aggregation import aggregate
from judge.comparison import build_comparisons
from judge.opus import extract_response_text, invoke_opus, parse_ground_truth

PROMPT_PATH = Path(__file__).resolve().parents[1] / "judge" / "prompt_v1.txt"


class OpusJudgeAdapter(JudgeAdapter):
    def judge(self, request: ParseRequest, extraction: ExtractionResult) -> JudgeVerdict:
        pdf = request.pdf.read_bytes() if isinstance(request.pdf, Path) else request.pdf
        response, latency_ms = invoke_opus(pdf, PROMPT_PATH.read_text(encoding="utf-8"))
        expected = parse_ground_truth(extract_response_text(response))
        comparisons = build_comparisons(request, expected, extraction.payload)
        summary = aggregate(comparisons)
        choices = response.get("choices") or [{}]
        response_id = response.get("id") or choices[0].get("id")
        return JudgeVerdict(request.request_id, "databricks-claude-opus-5", comparisons,
                            latency_ms, raw_response_id=response_id,
                            summary=json.dumps(summary, sort_keys=True))
