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


# Allowed keys for the judge ground-truth JSON shape.  These mirror the 28
# judged field paths in ``contracts.models.JUDGED_FIELDS`` (a SECOND,
# independent definition of the roster — ``judge/scorer.py`` and
# ``judge/evaluator.py`` hold two others; sync tests pin all of them
# together).  Keeping the parser's allow-list explicit here, rather than
# deriving it, matches the repo's established pattern and lets the strict
# validation below REJECT any key outside the 28-field roster — so an Opus
# that drifts onto an unexpected field surfaces as a JUDGE_ERROR instead of
# silently passing.  A sync test in ``tests/test_contracts.py`` asserts these
# sets cover exactly the 28 ``JUDGED_FIELDS`` leaves, so the parser cannot
# drift from the judged roster — the exact drift that left the 21 fields
# added in PR #47 permanently ABSENT_IN_PDF (the prompt emitted only the
# original 7 and this allow-list rejected the rest).
_CARD_META_KEYS = frozenset(
    {"cardDisplayName", "lastFourDigit", "productFamily", "network"})
_CARD_BIGPICTURE_KEYS = frozenset(
    {"cardCreditLimit", "cardAvailableCreditLimit"})
_CARD_KEYS = frozenset({"cardMeta", "bigPicture"})
_STATEMENT_META_KEYS = frozenset(
    {"issuerName", "statementDate", "dueDate", "statementPeriodStart",
     "statementPeriodEnd"})
_STATEMENT_SUMMARY_KEYS = frozenset(
    {"totalAmountDue", "totalMinimumAmountDue", "totalCreditLimit",
     "availableCreditLimit"})
_REWARD_KEYS = frozenset(
    {"pointsEarnedThisCycle", "closingPoints", "programType", "openingPoints",
     "pointsRedeemedThisCycle", "pointsExpiringNext30Days",
     "pointsExpiringNext60Days", "bonusPointsThisCycle"})
_TRANSACTION_KEYS = frozenset(
    {"date", "description", "amount", "direction",
     "rewardPointsOnThisTransaction"})
_TOP_LEVEL_KEYS = frozenset(
    {"cards", "rewards", "transactions", "statementMeta",
     "statementLevelSummary"})


def parse_ground_truth(text: str) -> dict:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate)
        candidate = re.sub(r"\s*```$", "", candidate)
    parsed = json.loads(candidate)
    if not isinstance(parsed, dict):
        raise ValueError("judge ground truth must be a JSON object")
    if set(parsed) - _TOP_LEVEL_KEYS:
        raise ValueError(
            f"judge returned unsupported top-level fields: "
            f"{sorted(set(parsed) - _TOP_LEVEL_KEYS)}")
    cards = parsed.get("cards", [])
    rewards = parsed.get("rewards", {})
    transactions = parsed.get("transactions", [])
    statement_meta = parsed.get("statementMeta", {})
    statement_level_summary = parsed.get("statementLevelSummary", {})
    if (not isinstance(cards, list) or not isinstance(rewards, dict)
            or not isinstance(transactions, list)
            or not isinstance(statement_meta, dict)
            or not isinstance(statement_level_summary, dict)):
        raise ValueError("judge ground-truth containers have invalid types")
    for card in cards:
        if not isinstance(card, dict) or set(card) - _CARD_KEYS:
            raise ValueError("judge returned unsupported card fields")
        meta = card.get("cardMeta", {})
        if not isinstance(meta, dict) or set(meta) - _CARD_META_KEYS:
            raise ValueError("judge returned unsupported cardMeta fields")
        big_picture = card.get("bigPicture", {})
        if not isinstance(big_picture, dict) or set(big_picture) - _CARD_BIGPICTURE_KEYS:
            raise ValueError("judge returned unsupported bigPicture fields")
    if set(rewards) - _REWARD_KEYS:
        raise ValueError("judge returned unsupported reward fields")
    if set(statement_meta) - _STATEMENT_META_KEYS:
        raise ValueError("judge returned unsupported statementMeta fields")
    if set(statement_level_summary) - _STATEMENT_SUMMARY_KEYS:
        raise ValueError("judge returned unsupported statementLevelSummary fields")
    for transaction in transactions:
        if not isinstance(transaction, dict) or set(transaction) - _TRANSACTION_KEYS:
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
