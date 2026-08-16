"""Post-hoc judge scorer: samples MLflow traces and scores them asynchronously.

The judge no longer runs inline on every parse. Instead, after live processing,
this module samples a few MLflow traces (each carrying the source PDF and the
Luna extraction as artifacts), re-invokes Opus 5 to get the ground truth,
compares it to the extraction via the existing scoring logic, logs per-field
accuracy metrics back to the SAME trace, and tags it ``judged=true`` so it is
not re-sampled.

This module is stdlib-importable at the module level (third-party imports like
``mlflow`` are function-local inside ``try/except``) so the test gate runs on
this machine where pypi is blackholed.

Reuses — does NOT rewrite — the existing scoring logic:
* :class:`harness.judge_adapter.OpusJudgeAdapter.judge` (Opus call + comparisons)
* :func:`harness.tracing_judge.verdict_to_metrics` (verdict → MLflow metrics)
"""

from __future__ import annotations

import json
import logging
import os
import random
from pathlib import Path
from typing import Any

_LOGGER = logging.getLogger("statement-agent.scorer")

# The seven judged fields, used for per-field metric reporting in the summary.
JUDGED_FIELDS = (
    "cards[].cardMeta.cardDisplayName",
    "cards[].cardMeta.lastFourDigit",
    "rewards.pointsEarnedThisCycle",
    "rewards.closingPoints",
    "transactions[].date",
    "transactions[].description",
    "transactions[].amount",
)

# Fallback MLflow experiment path used when neither the MLFLOW_EXPERIMENT_ID
# env var (injected by the bound Databricks App resource) nor the configurable
# MLFLOW_EXPERIMENT_PATH / config.Settings path resolve.  The scorer prefers
# the env-var experiment ID — the same approach used by
# harness.tracing.configure_tracing — because the ID is more robust: it
# survives experiment recreation and cross-workspace deploys.
_EXPERIMENT_PATH = "/Shared/savesage/statement-agent"

# Module-level flag so _ensure_mlflow_configured runs once per process.
_mlflow_configured = False


def _get_experiment_id(mlf: Any) -> str | None:
    """Resolve the MLflow experiment ID from the bound resource env var or config path.

    Prefers ``MLFLOW_EXPERIMENT_ID`` (set by the bound Databricks App resource,
    the same source the live trace sink uses) over a name-based lookup.  Falls
    back to ``get_experiment_by_name`` with the configurable experiment path
    from ``config.Settings.mlflow_experiment_path`` (read from the
    ``MLFLOW_EXPERIMENT_PATH`` env var or the hardcoded default).
    """
    exp_id = os.getenv("MLFLOW_EXPERIMENT_ID", "")
    if exp_id:
        return exp_id
    # Fall back to looking up the experiment by its configured path/name.
    try:
        from config import get_settings
        experiment_path = get_settings().mlflow_experiment_path
    except Exception:  # noqa: BLE001 - config import must never break the scorer
        experiment_path = _EXPERIMENT_PATH
    try:
        exp = mlf.get_experiment_by_name(experiment_path)
        if exp is not None:
            return exp.experiment_id
    except Exception:  # noqa: BLE001 - never fatal; surfaced as "experiment not found"
        pass
    return None


def _ensure_mlflow_configured(mlf: Any) -> None:
    """Ensure the MLflow tracking URI and Databricks profile are configured.

    Mirrors the ``DATABRICKS_CONFIG_PROFILE`` handling from
    :func:`harness.tracing.configure_tracing`.  Critical for the scheduled job
    (which runs outside the app process where MLflow defaults to a local file
    store) and for the in-app judge when no parse has been done yet (the
    tracing module configures MLflow lazily on the first trace event).
    """
    global _mlflow_configured
    if _mlflow_configured:
        return
    # Set tracking URI to databricks (the default is a local file/sqlite store).
    try:
        mlf.set_tracking_uri("databricks")
    except Exception:  # noqa: BLE001 - fake mlflow in tests may lack this method
        pass
    # Handle DATABRICKS_CONFIG_PROFILE: same logic as configure_tracing.
    # In the Databricks Apps runtime the config file is absent and the bound
    # experiment resource supplies auth — a stale DATABRICKS_CONFIG_PROFILE
    # pointing at a non-existent profile would break the SDK credential chain.
    cfg_path = os.environ.get(
        "DATABRICKS_CONFIG_FILE",
        os.path.expanduser("~/.databrickscfg"),
    )
    if os.path.isfile(cfg_path):
        profile = os.getenv("DATABRICKS_CONFIG_PROFILE", "fevm-stable")
        os.environ["DATABRICKS_CONFIG_PROFILE"] = profile
    else:
        os.environ.pop("DATABRICKS_CONFIG_PROFILE", None)
    _mlflow_configured = True


def score_trace(run_id: str) -> dict[str, Any]:
    """Score a single MLflow trace by re-running the judge on its artifacts.

    Steps:
    1. Download ``statement.pdf`` and ``extraction.json`` artifacts from the run.
    2. Reconstruct a ``ParseRequest`` and ``ExtractionResult``.
    3. Call ``OpusJudgeAdapter.judge()`` → ``JudgeVerdict`` (Opus + comparisons).
    4. Compute metrics via ``verdict_to_metrics``.
    5. Log metrics + tag ``judged=true`` on the SAME MLflow run.
    6. Return a per-trace result dict.

    Returns ``{"run_id": ..., "status": "OK"|"ERROR", ...}`` — never raises
    (errors are captured in the ``status``/``error`` fields).
    """
    try:
        return _score_trace_impl(run_id)
    except Exception as exc:
        _LOGGER.warning("score_trace failed for run %s: %s", run_id, exc)
        return {"run_id": run_id, "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}"}


def _score_trace_impl(run_id: str) -> dict[str, Any]:
    import mlflow
    from mlflow.tracking import MlflowClient

    from contracts.models import Bank, ExtractionResult, ParseRequest
    from harness.judge_adapter import OpusJudgeAdapter
    from harness.tracing_judge import verdict_to_metrics

    # 1. Download the PDF artifact.
    pdf_local = mlflow.artifacts.download_artifacts(
        run_id=run_id, artifact_path="statement.pdf"
    )
    pdf_bytes = Path(pdf_local).read_bytes()

    # 2. Download the extraction metadata artifact.
    extraction_local = mlflow.artifacts.download_artifacts(
        run_id=run_id, artifact_path="extraction.json"
    )
    meta = json.loads(Path(extraction_local).read_text("utf-8"))

    # 3. Reconstruct the ParseRequest and ExtractionResult.
    request = ParseRequest(
        pdf=pdf_bytes,
        filename="statement.pdf",
        bank=Bank(meta["bank"]),
        request_id=meta["request_id"],
    )
    extraction = ExtractionResult(
        request_id=meta["request_id"],
        payload=meta["payload"],
        model_id=meta.get("model_id", "unknown"),
        latency_ms=0.0,
        schema_valid=meta.get("schema_valid", False),
    )

    # 4. Call the judge (Opus + comparisons + aggregation).
    adapter = OpusJudgeAdapter()
    verdict = adapter.judge(request, extraction)

    # 5. Compute metrics from the verdict.
    metrics = verdict_to_metrics(verdict)

    # 6. Log metrics back to the SAME MLflow run.  Use the MlflowClient API
    # (not the module-level mlflow.log_metric / mlflow.set_tag) because the
    # module-level set_tag() does NOT accept a run_id keyword argument —
    # it only sets tags on the active run.  MlflowClient.log_metric /
    # .set_tag take run_id as the first positional argument and work on
    # any run, active or not.
    client = MlflowClient()
    for key, value in metrics.items():
        if value is not None:
            client.log_metric(run_id, key, float(value))

    # 7. Tag the run. Only tag judged=true on OK status so JUDGE_ERROR traces
    # can be retried in the next evaluation. JUDGE_ERROR gets judged=error so
    # it's distinguishable but still retriable.
    summary = json.loads(verdict.summary) if verdict.summary else {}
    status = summary.get("status", "OK")
    if status == "OK":
        client.set_tag(run_id, "judged", "true")
    else:
        client.set_tag(run_id, "judged", "error")

    # 8. Build and return the per-trace result.
    return {
        "run_id": run_id,
        "request_id": meta["request_id"],
        "bank": meta["bank"],
        "status": status,
        "strict_accuracy": metrics.get("judge.accuracy"),
        "narration_forgiven_accuracy": metrics.get("judge.accuracy_forgiven"),
        "comparisons": int(metrics.get("judge.comparisons", 0)),
        "scored": int(metrics.get("judge.scored", 0)),
        "correct": int(metrics.get("judge.correct", 0)),
        "per_field": {
            k.removeprefix("judge."): v
            for k, v in metrics.items()
            if k not in (
                "judge.accuracy", "judge.accuracy_forgiven",
                "judge.comparisons", "judge.scored", "judge.correct",
            )
        },
    }


def run_judge_evaluation(sample_size: int = 10) -> dict[str, Any]:
    """Sample unjudged MLflow runs, score each, and return an aggregate summary.

    1. Query MLflow for recent runs (ordered by start_time DESC).
    2. Filter in Python: exclude runs where ``tags.judged == "true"`` (already
       scored) — the MLflow inequality filter ``tags.judged != 'true'`` EXCLUDES
       entries where the tag is absent entirely, which would hide new parses.
    3. Pick the ``sample_size`` most recent randomly (to avoid always judging
       the same tail).
    4. Call ``score_trace`` on each (errors are captured per-trace, not fatal).
    5. Return an aggregate summary: overall strict, narration-forgiven, per-field
       breakdown, per-bank breakdown, count judged, errors.

    Returns ``{"count_judged": N, "errors": [...], "overall_strict": ..., ...}``.
    """
    import mlflow

    # 0. Ensure MLflow is configured (tracking URI + Databricks profile).
    # Critical for the scheduled job (runs outside the app process where
    # MLflow defaults to a local file store) and for the in-app judge when
    # no parse has been done yet (the tracing module configures MLflow
    # lazily on the first trace event).
    _ensure_mlflow_configured(mlflow)

    # 1. Resolve the experiment and search for recent runs.
    exp_id = _get_experiment_id(mlflow)
    if exp_id is None:
        # Surface the configured path (or env-var ID) so the operator can
        # see what was searched, not just "experiment not found".
        tried = os.getenv("MLFLOW_EXPERIMENT_ID") or _EXPERIMENT_PATH
        return {
            "count_judged": 0,
            "errors": [{"error": f"experiment not found: {tried}"}],
            "overall_strict": None,
            "overall_narration_forgiven": None,
            "per_field": {},
            "per_bank": {},
        }

    # Search ALL recent runs (no tag filter — MLflow's inequality filter
    # excludes entries where the tag is absent, hiding new parses).
    # Order by start_time DESC so we actually get the most recent.
    try:
        runs_df = mlflow.search_runs(
            experiment_ids=[exp_id],
            max_results=100,
            order_by=["attributes.start_time DESC"],
        )
    except Exception:
        # order_by may not be supported on all MLflow versions — fall back.
        runs_df = mlflow.search_runs(experiment_ids=[exp_id], max_results=100)

    if runs_df.empty:
        return {
            "count_judged": 0,
            "errors": [],
            "overall_strict": None,
            "overall_narration_forgiven": None,
            "per_field": {},
            "per_bank": {},
        }

    # 2. Filter in Python: only unjudged runs (no tag or judged != "true").
    # A run tagged judged=error (JUDGE_ERROR) is retriable — include it.
    run_ids_all = runs_df["run_id"].tolist()
    tag_col = None
    for col in ("tags.judged", "judged"):
        if col in runs_df.columns:
            tag_col = col
            break
    if tag_col is not None:
        run_ids = [
            rid for rid, val in zip(run_ids_all, runs_df[tag_col].tolist())
            if val != "true"
        ]
    else:
        # No judged column at all — all runs are unjudged.
        run_ids = run_ids_all

    if not run_ids:
        return {
            "count_judged": 0,
            "errors": [],
            "overall_strict": None,
            "overall_narration_forgiven": None,
            "per_field": {},
            "per_bank": {},
        }

    # 3. Pick the sample_size most recent randomly.
    sample = random.sample(run_ids, min(sample_size, len(run_ids)))

    # 4. Score each trace.
    results = [score_trace(rid) for rid in sample]

    # 5. Aggregate.
    return _aggregate_results(results)


def _aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the aggregate summary from per-trace results.

    Only ``status == "OK"`` traces are scored. ``JUDGE_ERROR`` (Opus returned
    an unusable response) and ``ERROR`` (exception during scoring) are both
    counted as errors — JUDGE_ERROR traces are tagged ``judged=error`` (not
    ``judged=true``) so they can be retried in the next evaluation.
    """
    scored = [r for r in results if r.get("status") == "OK"]
    errors = [
        {
            "run_id": r.get("run_id"),
            "error": r.get("error") or f"JUDGE_ERROR: {r.get('status')}",
            "status": r.get("status"),
        }
        for r in results if r.get("status") in ("ERROR", "JUDGE_ERROR")
    ]

    # Overall strict / narration-forgiven (average over scored traces).
    strict_vals = [r["strict_accuracy"] for r in scored if r.get("strict_accuracy") is not None]
    forgiven_vals = [r["narration_forgiven_accuracy"] for r in scored if r.get("narration_forgiven_accuracy") is not None]

    overall_strict = sum(strict_vals) / len(strict_vals) if strict_vals else None
    overall_forgiven = sum(forgiven_vals) / len(forgiven_vals) if forgiven_vals else None

    # Per-field accuracy (average across scored traces that have the field).
    per_field: dict[str, dict[str, Any]] = {}
    for field in JUDGED_FIELDS:
        field_key = field.replace("[]", "").replace(".", "_")
        vals = [
            r["per_field"].get(field_key)
            for r in scored
            if r.get("per_field", {}).get(field_key) is not None
        ]
        if vals:
            per_field[field] = {
                "accuracy": sum(vals) / len(vals),
                "count": len(vals),
            }
        else:
            per_field[field] = {"accuracy": None, "count": 0}

    # Per-bank breakdown.
    per_bank: dict[str, dict[str, Any]] = {}
    for r in scored:
        bank = r.get("bank", "unknown")
        if bank not in per_bank:
            per_bank[bank] = {"count": 0, "strict_vals": [], "forgiven_vals": []}
        per_bank[bank]["count"] += 1
        if r.get("strict_accuracy") is not None:
            per_bank[bank]["strict_vals"].append(r["strict_accuracy"])
        if r.get("narration_forgiven_accuracy") is not None:
            per_bank[bank]["forgiven_vals"].append(r["narration_forgiven_accuracy"])
    for bank, data in per_bank.items():
        sv = data.pop("strict_vals")
        fv = data.pop("forgiven_vals")
        data["strict_accuracy"] = sum(sv) / len(sv) if sv else None
        data["narration_forgiven_accuracy"] = sum(fv) / len(fv) if fv else None

    return {
        "count_judged": len(scored),
        "count_errors": len(errors),
        "errors": errors,
        "overall_strict": overall_strict,
        "overall_narration_forgiven": overall_forgiven,
        "per_field": per_field,
        "per_bank": per_bank,
    }
