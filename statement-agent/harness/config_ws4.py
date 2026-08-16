"""Workstream 4 telemetry configuration.

Per CONTRACTS.md, workstream-specific configuration lives here (tagged with
``CONFIGURE(<slug>)``) rather than in shared ``config.py``. The MLflow experiment
*path* is read from shared ``config.Settings.mlflow_experiment_path``; this module
adds the Databricks profile, tracking URI, enable flag, the PII redaction policy,
and an explicit per-model cost-rate table.

This module is stdlib-only (no mlflow import) so it stays on the contract-test path.
"""

import json
import logging
import math
import os
from dataclasses import dataclass, field

# CONFIGURE(ws4-cost-rates) — explicit USD per-1,000,000 tokens for models routed
# through the Databricks AI Gateway under FMAPI names. MLflow does not natively
# price these, and AI-Gateway usage tables lag, so cost is set explicitly on the
# trace. A rate of 0.0 means "cost is recorded explicitly as 0.0" — it is NEVER
# silently absent. The Luna extraction model carries its real rates; the judge
# model stays 0.0 (no judge rate was provided, and the judge path captures no
# usage yet — out of scope here). Override per-workspace at deploy via
# WS4_COST_RATES_JSON (see get_tracing_config); do NOT hand-edit these rates
# for a deploy.
#
# Rate-table KEY vs endpoint name: ``cost_attributes`` (tracing_cost.py) looks the
# span's ``model_id`` up in this table. For the Luna extraction call that
# ``model_id`` is the value the AI-Gateway returns in its response ``model``
# field ("gpt-5.6-luna" — verified from the live MLflow run param / trace), NOT
# the ``EXTRACTION_ENDPOINT`` name. So "gpt-5.6-luna" is the EFFECTIVE cost-lookup
# key (the one the extract span actually records); "databricks-gpt-5-6-luna" is
# the AI-Gateway endpoint name, keyed too as an alias so an ops override or a
# future API change surfacing the endpoint name still prices the span. Both
# carry the same rate. Without the "gpt-5.6-luna" key cost stays $0 even with a
# non-zero rate, because the lookup misses.
_DEFAULT_COST_RATES: dict[str, dict[str, float]] = {
    "gpt-5.6-luna": {"input": 0.2, "output": 1.2},
    "databricks-gpt-5-6-luna": {"input": 0.2, "output": 1.2},
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
    # When empty: PII values (cardholder names, descriptions) are OMITTED (sent
    # as None) and the actor is sent as "redacted" — rather than hashing with an
    # unsalted digest (which is dictionary-reversible for low-entropy values).
    # Set WS4_FEEDBACK_HMAC_KEY at deploy to a per-workspace secret to retain
    # linkable pseudonyms. CONFIGURE(ws4-feedback-hmac-key)
    feedback_hmac_key: bytes = b""  # CONFIGURE(ws4-feedback-hmac-key)
    # Bounded-memory limits for long-lived Apps processes. CONFIGURE(ws4-max-*)
    max_pending_requests: int = 1024  # CONFIGURE(ws4-max-pending) — buffered request IDs
    max_trace_ids: int = 1024  # CONFIGURE(ws4-max-trace-ids) — LRU trace-id map
    max_flushed: int = 2048  # CONFIGURE(ws4-max-flushed) — late-arrival guard
    # Per-request event cap: a single stuck request (whose root never arrives)
    # must not accumulate events indefinitely. When exceeded, the request's
    # buffer is abandoned and a warning is logged. The root event is always
    # allowed through even if it exceeds the cap (it triggers a flush, so no
    # accumulation). CONFIGURE(ws4-max-events-per-request)
    max_events_per_request: int = 100  # CONFIGURE(ws4-max-events-per-request)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    """Fail-safe int env-var parse: returns default on non-numeric values.

    A malformed ``WS4_MAX_*`` env var must not prevent app startup — it degrades
    to the default with a warning, never a raise (review B1).
    """
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        logging.getLogger("statement-agent.tracing").warning(
            "invalid WS4 config %s=%r; using default %d", name, raw, default,
        )
        return default


def _merged_cost_rates() -> dict[str, dict[str, float]]:
    """Return the deploy-time cost-rate table (USD per 1,000,000 tokens).

    Starts from a fresh copy of ``_DEFAULT_COST_RATES`` and overlays any
    per-model override supplied via the ``WS4_COST_RATES_JSON`` env var — a JSON
    object mapping ``model_id -> {"input": float, "output": float}``. Best-effort,
    matching ``_env_int``: a missing var or empty string means "no override" and
    returns the defaults unchanged; invalid JSON or a non-dict value is warned
    about (never raised) and the defaults are used. Each override is shallow-merged
    per model, so an override may set one rate and inherit the other. Per-rate
    values are coerced to float; a rate that is non-numeric, overflows float (a
    huge JSON integer), non-finite (NaN, +/-Infinity), or negative is skipped
    (not stored) so the model inherits its default/other rate — a bad
    ``WS4_COST_RATES_JSON`` must never crash startup or a ``cost_attributes``
    call at trace time. A model id that is new (absent from the defaults) is
    added; one present is updated.
    """
    rates = {k: dict(v) for k, v in _DEFAULT_COST_RATES.items()}
    raw = os.getenv("WS4_COST_RATES_JSON", "")
    if not raw:
        return rates
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        logging.getLogger("statement-agent.tracing").warning(
            "invalid WS4_COST_RATES_JSON (not valid JSON); using default cost rates",
        )
        return rates
    if not isinstance(parsed, dict):
        logging.getLogger("statement-agent.tracing").warning(
            "invalid WS4_COST_RATES_JSON (not a JSON object); using default cost rates",
        )
        return rates
    for model_id, override in parsed.items():
        if not isinstance(model_id, str) or not isinstance(override, dict):
            continue
        merged = dict(rates.get(model_id, {}))
        for key in ("input", "output"):
            if key in override:
                # Coerce to float; a bad rate is skipped (not stored) so the model
                # inherits its default/other rate — a bad WS4_COST_RATES_JSON must
                # never crash startup or a trace-time cost_attributes call.
                try:
                    val = float(override[key])
                except (ValueError, TypeError, OverflowError):
                    # OverflowError: a huge JSON integer (e.g. 10**400) overflows
                    # float() but is NOT a ValueError (Python promotes it past the
                    # int->float conversion). Without it the override raises and
                    # breaks the never-raises contract.
                    continue
                # Reject non-finite (NaN, +/-Infinity) and negative rates — they
                # would produce a nonsensical or invalid cost on the trace. Skip
                # (do not store) so the model inherits its default/other rate.
                # (float(True)==1.0 and bool is an int subclass, but a JSON boolean
                # is an unlikely rate; float() already guards the type, and
                # isfinite+>=0 guards the value.)
                if math.isfinite(val) and val >= 0:
                    merged[key] = val
        rates[model_id] = merged
    return rates


def get_tracing_config() -> TracingConfig:
    """Read a fresh environment snapshot on every call (mirrors config.get_settings).

    Fail-safe: invalid ``WS4_MAX_*`` env vars fall back to defaults rather than
    raising (review B1) — a config typo must not prevent app startup. The same
    discipline applies to ``WS4_COST_RATES_JSON`` (see ``_merged_cost_rates``): a
    missing/empty/malformed override never raises; the default cost-rate table is
    used. This lets ops set per-model rates per workspace without a code edit.
    """
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
        cost_rates_per_million=_merged_cost_rates(),
        max_pending_requests=_env_int("WS4_MAX_PENDING", 1024),
        max_trace_ids=_env_int("WS4_MAX_TRACE_IDS", 1024),
        max_flushed=_env_int("WS4_MAX_FLUSHED", 2048),
        max_events_per_request=_env_int("WS4_MAX_EVENTS_PER_REQUEST", 100),
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
