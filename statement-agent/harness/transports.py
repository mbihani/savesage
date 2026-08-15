"""Exact, deliberately non-interchangeable Luna and Opus request shapes."""

import base64
from typing import Any


def extraction_payload(pdf: bytes, filename: str, prompt: str, schema: dict[str, Any], max_tokens: int = 96_000) -> dict[str, Any]:
    """OpenAI chat-completions shape; Anthropic document blocks hard-400 Luna."""
    encoded = base64.b64encode(pdf).decode("ascii")
    return {
        "messages": [{"role": "user", "content": [
            {"type": "file", "file": {"filename": filename, "file_data": "data:application/pdf;base64," + encoded}},
            {"type": "text", "text": prompt},
        ]}],
        "max_tokens": max_tokens,
        "response_format": {"type": "json_schema", "json_schema": {"name": "credit_card_statement", "strict": True, "schema": schema}},
        "reasoning_effort": "medium",
    }


def judge_payload(pdf: bytes, prompt: str, max_tokens: int = 64_000) -> dict[str, Any]:
    """Anthropic shape for Opus 5; no data URL and no reasoning_effort."""
    encoded = base64.b64encode(pdf).decode("ascii")
    return {
        "messages": [{"role": "user", "content": [
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": encoded}},
            {"type": "text", "text": prompt},
        ]}],
        "max_tokens": max_tokens,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "medium"},
    }
