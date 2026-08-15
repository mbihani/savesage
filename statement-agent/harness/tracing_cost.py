"""Pure token-usage and explicit-cost attribute builders (stdlib-only, no mlflow).

The extraction/judge models are served through the Databricks AI Gateway under
FMAPI names that MLflow does not natively price, and AI-Gateway usage tables lag,
so cost must be set explicitly on the span (requirement 5) rather than relied on
for automatic attribution. When a model has no configured rate, cost is recorded
explicitly as 0.0 — it is never silently absent.
"""

from typing import Any, Mapping

from contracts.models import TokenUsage

from .tracing_keys import (
    SPAN_ATTR_CHAT_USAGE,
    SPAN_ATTR_LLM_COST,
    SPAN_ATTR_MODEL,
    SPAN_ATTR_MODEL_PROVIDER,
)

_USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def _usage_dict(usage: Any) -> dict[str, int] | None:
    if usage is None:
        return None
    if isinstance(usage, TokenUsage):
        pairs = {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
        }
    elif isinstance(usage, Mapping):
        pairs = {k: usage.get(k) for k in _USAGE_KEYS if k in usage}
    else:
        return None
    out = {k: v for k, v in pairs.items() if v is not None}
    return out or None


def usage_attributes(usage: Any) -> dict[str, int] | None:
    """Build the ``mlflow.chat.tokenUsage`` attribute payload (or None)."""
    return _usage_dict(usage)


def cost_attributes(usage: Any, model_id: str, rates: Mapping[str, Mapping[str, float]]) -> dict[str, float] | None:
    """Build the ``mlflow.llm.cost`` attribute payload in USD, set explicitly.

    Returns None only when there is no usage to price. When usage exists but the
    model has no configured rate, returns explicit zeros — never relied on auto.
    """
    u = _usage_dict(usage)
    if u is None:
        return None
    rate = rates.get(model_id)
    if not rate:
        return {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0}
    inp = u.get("input_tokens", 0) or 0
    out = u.get("output_tokens", 0) or 0
    input_cost = inp * float(rate.get("input", 0.0)) / 1_000_000
    output_cost = out * float(rate.get("output", 0.0)) / 1_000_000
    return {"input_cost": input_cost, "output_cost": output_cost, "total_cost": input_cost + output_cost}


def model_attributes(model_id: str | None, endpoint: str | None) -> dict[str, str]:
    """Build the model-name / provider span attributes (empty dict if neither)."""
    attrs: dict[str, str] = {}
    if model_id:
        attrs[SPAN_ATTR_MODEL] = model_id
    if endpoint:
        attrs[SPAN_ATTR_MODEL_PROVIDER] = "databricks-ai-gateway"
    return attrs
