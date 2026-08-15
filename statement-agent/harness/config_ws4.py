"""Workstream 4 telemetry configuration.

Per CONTRACTS.md, workstream-specific configuration lives here (tagged with
``CONFIGURE(<slug>)``) rather than in shared ``config.py``. The MLflow experiment
*path* is read from shared ``config.Settings.mlflow_experiment_path``; this module
adds the Databricks profile, tracking URI, enable flag, the PII redaction policy,
and an explicit per-model cost-rate table.

This module is stdlib-only (no mlflow import) so it stays on the contract-test path.
"""

import os
from dataclasses import dataclass, field

# CONFIGURE(ws4-cost-rates) — explicit USD per-1,000,000 tokens for models routed
# through the Databricks AI Gateway under FMAPI names. MLflow does not natively
# price these, and AI-Gateway usage tables lag, so cost is set explicitly on the
# trace. A rate of 0.0 means "cost is recorded explicitly as 0.0" — it is NEVER
# silently absent. Fill real rates at deploy.
_DEFAULT_COST_RATES: dict[str, dict[str, float]] = {
    "databricks-gpt-5-6-luna": {"input": 0.0, "output": 0.0},
    "databricks-claude-opus-5": {"input": 0.0, "output": 0.0},
}


@dataclass(frozen=True, slots=True)
class TracingConfig:
    """All telemetry knobs for workstream 4."""

    enabled: bool = True  # CONFIGURE(ws4-tracing-enabled)
    tracking_uri: str = "databricks"  # CONFIGURE(ws4-tracking-uri)
    databricks_profile: str = "fevm-stable"  # CONFIGURE(ws4-databricks-profile)
    # Empty => resolved from shared config.Settings.mlflow_experiment_path.
    experiment_path: str = ""  # CONFIGURE(ws4-experiment-path)
    autolog_langchain: bool = True  # CONFIGURE(ws4-autolog-langchain)
    # PII policy: see harness/tracing_feedback.py for the tiered redaction rules.
    redact_pii_values: bool = True  # CONFIGURE(ws4-redact-pii)
    log_nonpii_values_raw: bool = True  # CONFIGURE(ws4-log-nonpii-raw)
    # Explicit cost-rate table (per 1M tokens, USD). CONFIGURE(ws4-cost-rates)
    cost_rates_per_million: dict[str, dict[str, float]] = field(
        default_factory=lambda: {k: dict(v) for k, v in _DEFAULT_COST_RATES.items()}
    )
    # HMAC key (raw bytes) for pseudonymising PII values and actors in telemetry.
    # When empty, PII leaves (cardholder names, descriptions) and the actor are
    # OMITTED entirely from telemetry (sent as None) rather than hashed with an
    # unsalted digest (which is dictionary-reversible for low-entropy values).
    # Set WS4_FEEDBACK_HMAC_KEY at deploy to a per-workspace secret to retain
    # linkable pseudonyms. CONFIGURE(ws4-feedback-hmac-key)
    feedback_hmac_key: bytes = b""  # CONFIGURE(ws4-feedback-hmac-key)
    # Bounded-memory limits for long-lived Apps processes. CONFIGURE(ws4-max-*)
    max_pending_requests: int = 1024  # CONFIGURE(ws4-max-pending) — buffered trees
    max_trace_ids: int = 1024  # CONFIGURE(ws4-max-trace-ids) — LRU trace-id map
    max_flushed: int = 2048  # CONFIGURE(ws4-max-flushed) — late-arrival guard


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def get_tracing_config() -> TracingConfig:
    """Read a fresh environment snapshot on every call (mirrors config.get_settings)."""
    hmac_raw = os.getenv("WS4_FEEDBACK_HMAC_KEY", "")
    return TracingConfig(
        enabled=_env_bool("WS4_TRACING_ENABLED", True),
        tracking_uri=os.getenv("WS4_TRACKING_URI", "databricks"),
        databricks_profile=os.getenv("DATABRICKS_CONFIG_PROFILE", "fevm-stable"),
        experiment_path=os.getenv("MLFLOW_EXPERIMENT_PATH", ""),
        autolog_langchain=_env_bool("WS4_AUTOLOG_LANGCHAIN", True),
        redact_pii_values=_env_bool("WS4_REDACT_PII", True),
        log_nonpii_values_raw=_env_bool("WS4_LOG_NONPII_RAW", True),
        feedback_hmac_key=hmac_raw.encode() if hmac_raw else b"",
        max_pending_requests=int(os.getenv("WS4_MAX_PENDING", "1024")),
        max_trace_ids=int(os.getenv("WS4_MAX_TRACE_IDS", "1024")),
        max_flushed=int(os.getenv("WS4_MAX_FLUSHED", "2048")),
    )


def resolve_experiment_path(cfg: TracingConfig) -> str:
    """Return the configured experiment path, falling back to shared config."""
    if cfg.experiment_path:
        return cfg.experiment_path
    try:
        from config import get_settings  # local import; shared config is stdlib-only

        return get_settings().mlflow_experiment_path
    except Exception:  # noqa: BLE001 - never fatal; telemetry-only
        return ""
