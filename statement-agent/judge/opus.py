"""Stdlib-only Opus invocation and strict response parsing."""

import json
import re
import time
import urllib.error
import urllib.request

from config import get_settings
from harness.auth import acquire_token
from harness.policy import RetryPolicy
from harness.transports import judge_payload


def extract_response_text(response: dict) -> str:
    """Read Anthropic-native responses first, then gateway-normalized OpenAI ones."""
    def content_text(content: object) -> str | None:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(block.get("text", "") for block in content
                           if isinstance(block, dict) and block.get("type") == "text")
        return None

    native_text = content_text(response.get("content"))
    if native_text is not None:
        return native_text
    choices = response.get("choices") or []
    if choices:
        normalized_text = content_text((choices[0].get("message") or {}).get("content"))
        if normalized_text is not None:
            return normalized_text
    raise ValueError("Opus response has no text content")


def completion_reason(response: dict) -> str | None:
    """Return Anthropic stop_reason or normalized OpenAI finish_reason."""
    reason = response.get("stop_reason")
    if reason is not None:
        return str(reason)
    choices = response.get("choices") or []
    return str(choices[0].get("finish_reason")) if choices and choices[0].get("finish_reason") is not None else None


def parse_ground_truth(text: str) -> dict:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("judge ground truth must be a JSON object")
    allowed = {"cards", "rewards", "transactions"}
    if set(parsed) - allowed:
        raise ValueError(f"judge returned unsupported top-level fields: {sorted(set(parsed) - allowed)}")
    cards, rewards, transactions = parsed.get("cards", []), parsed.get("rewards", {}), parsed.get("transactions", [])
    if not isinstance(cards, list) or not isinstance(rewards, dict) or not isinstance(transactions, list):
        raise ValueError("judge ground-truth containers have invalid types")
    for card in cards:
        if not isinstance(card, dict) or set(card) - {"cardMeta"}:
            raise ValueError("judge returned unsupported card fields")
        meta = card.get("cardMeta", {})
        if not isinstance(meta, dict) or set(meta) - {"cardDisplayName", "lastFourDigit"}:
            raise ValueError("judge returned unsupported cardMeta fields")
    if set(rewards) - {"pointsEarnedThisCycle", "closingPoints"}:
        raise ValueError("judge returned unsupported reward fields")
    for transaction in transactions:
        if not isinstance(transaction, dict) or set(transaction) - {"date", "description", "amount"}:
            raise ValueError("judge returned unsupported transaction fields")
    return parsed


def invoke_opus(pdf: bytes, prompt: str) -> tuple[dict, float]:
    settings = get_settings()
    policy = RetryPolicy(settings.request_timeout_seconds, settings.max_attempts)
    body = json.dumps(judge_payload(pdf, prompt)).encode("utf-8")
    started = time.monotonic()
    for attempt in range(1, policy.max_attempts + 1):
        request = urllib.request.Request(settings.endpoint_url(settings.judge_endpoint), data=body,
            headers={"Authorization": f"Bearer {acquire_token()}", "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=policy.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8")), (time.monotonic() - started) * 1000
        except urllib.error.HTTPError as exc:
            if exc.code not in policy.retry_statuses or attempt == policy.max_attempts:
                detail = exc.read().decode("utf-8", errors="replace")[:2000]
                raise RuntimeError(f"Opus HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError:
            if attempt == policy.max_attempts:
                raise
        time.sleep(policy.backoff_for_attempt(attempt))
    raise AssertionError("unreachable")
