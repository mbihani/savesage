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

# How many recent traces the trace-based fallback in resolve_run_id scans.
# The on-demand judge targets a freshly-parsed statement, so a generous
# recent window reliably contains it; the run-tag fast path handles the
# rare tagged run in O(1) via an indexed filter.
_TRACE_SCAN_LIMIT = 200

# Module-level flag so _ensure_mlflow_configured runs once per process.
_mlflow_configured = False

# Module-level cache for the Lakebase ``ResultStore`` used to persist verdicts
# inline so ``GET /api/results`` can surface the per-field expected/actual/
# outcome on the per-parse Results view. Built lazily and best-effort: a
# ``None`` store simply skips the inline persist — the judge metrics still
# flow to MLflow. Tests inject a fake store via the ``result_store`` param of
# :func:`score_trace` so this builder is never called on the test path.
_result_store: Any = None
_result_store_init_done = False


def _build_result_store() -> Any:
    """Build a Lakebase-backed ``ResultStore`` for verdict persistence.

    Mirrors :func:`app.main._build_lakebase_stores` but only the result store.
    Raises on failure (missing env / connection error); callers catch and
    degrade to ``None``. ``databricks-sdk`` and ``psycopg`` are imported
    function-local inside the dependency modules, so importing this function
    does not require them — only *calling* it does.
    """
    required = ("ENDPOINT_NAME", "PGHOST", "PGUSER", "PGDATABASE")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            "Lakebase database resource did not inject required environment "
            f"variables: {', '.join(missing)}"
        )
    from databricks.sdk import WorkspaceClient
    from db.connection import OAuthConnectionFactory
    from db.stores import LakebaseResultStore, init_tables
    client = WorkspaceClient()
    connect = OAuthConnectionFactory(
        client,
        os.environ["ENDPOINT_NAME"],
        os.environ["PGHOST"],
        os.environ["PGDATABASE"],
        os.environ["PGUSER"],
        port=int(os.environ.get("PGPORT", "5432")),
        sslmode=os.environ.get("PGSSLMODE", "require"),
    )
    init_tables(connect)
    return LakebaseResultStore(connect)


def _get_result_store() -> Any:
    """Return a cached Lakebase ``ResultStore`` or ``None`` if unavailable.

    Lazily initialised on first call. Failures are logged and cached as
    ``None`` so a transient outage does not retry on every trace (the same
    pattern as :func:`app.main._get_stores`).
    """
    global _result_store, _result_store_init_done
    if _result_store_init_done:
        return _result_store
    _result_store_init_done = True
    try:
        _result_store = _build_result_store()
    except Exception:
        _LOGGER.warning(
            "Lakebase result store unavailable; verdicts will not be "
            "persisted inline", exc_info=True,
        )
        _result_store = None
    return _result_store


def resolve_run_id(request_id: str) -> str | None:
    """Resolve a ``request_id`` to its MLflow ``run_id``.

    Two-tier resolution, most-precise first:

    1. **Run-tag fast path** (indexed, O(1)): ``mlflow.search_runs`` with
       ``tags.request_id = '<id>'``. Cheapest lookup; works for runs whose
       ``request_id`` tag actually landed. The tracing sink sets this tag
       best-effort right after ``start_run`` (see :meth:`harness.tracing.
       MLflowTraceSink._ensure_run`), but in MLflow 3 with tracing enabled the
       module-level ``set_tag`` operates on the *active run*, whose context
       is racy under ``start_span_no_context`` — so the tag lands on only a
       small minority of parse runs in practice (verified live: ~2/44).

    2. **Trace-based fallback** (reliable): ``mlflow.search_traces`` returns
       the parse TRACE, which ALWAYS carries ``request_id`` in its
       ``trace_metadata['mlflow.traceInputs']`` (the serialised root-span
       inputs — ``{"request_id": "req-…", "bank": …}``) and the backing
       run in ``trace_metadata['mlflow.sourceRun']``. The trace is the
       canonical entity for a parse (the run is a side effect for artifact
       storage); ``request_id`` is set on the root span's inputs by the
       graph, so it is present on every parse trace regardless of whether
       the run tag landed. We return ``mlflow.sourceRun`` — the same run
       :func:`score_trace` downloads ``statement.pdf`` / ``extraction.json``
       from.

    Defense-in-depth: validates ``request_id`` against the canonical
    ``req-<12hex>`` form before interpolating it into any MLflow filter,
    so a crafted/quoted value can never alter the filter or select the wrong
    run. Returns ``None`` for a malformed id (the endpoint maps that to 400
    upstream, but this guard protects any other caller).

    Returns the ``run_id`` or ``None`` if no run/trace is found (the trace
    may predate the ``request_id`` tag, has aged out of the experiment, or
    the experiment is unreachable). Never raises — callers use the ``None``
    result to return a clean 404.
    """
    import re

    # Validate BEFORE building any filter — never interpolate an untrusted
    # value into an MLflow filter_string.
    if not re.match(r"^req-[0-9a-f]{12}$", request_id or ""):
        return None

    import mlflow

    _ensure_mlflow_configured(mlflow)
    exp_id = _get_experiment_id(mlflow)
    if exp_id is None:
        return None

    # 1. Indexed run-tag fast path (works when the tag landed).
    run_id = _resolve_run_id_via_run_tag(mlflow, exp_id, request_id)
    if run_id is not None:
        return run_id

    # 2. Trace-based fallback (reliable — traceInputs always carries request_id).
    return _resolve_run_id_via_traces(mlflow, exp_id, request_id)


def _resolve_run_id_via_run_tag(mlf: Any, exp_id: str, request_id: str) -> str | None:
    """Indexed run-tag lookup: ``tags.request_id = '<id>'``.

    Returns the ``run_id`` or ``None`` (no match, or search unavailable).
    Never raises — a search failure just falls through to the trace path.
    """
    try:
        runs_df = mlf.search_runs(
            experiment_ids=[exp_id],
            filter_string=f"tags.request_id = '{request_id}'",
            max_results=10,
        )
    except Exception:  # noqa: BLE001 - fall through to the trace path
        _LOGGER.warning(
            "resolve_run_id run-tag search failed for %s", request_id, exc_info=True,
        )
        return None
    if runs_df is None:
        return None
    # ``empty`` is exposed by both the pandas DataFrame (production) and the
    # stdlib fake. Avoid ``len(runs_df)`` — the fake has no ``__len__`` and a
    # TypeError here would be swallowed by the guard below, masking a hit.
    if getattr(runs_df, "empty", False):
        return None
    try:
        return runs_df["run_id"].tolist()[0]
    except Exception:  # noqa: BLE001 - defensive
        return None


def _resolve_run_id_via_traces(mlf: Any, exp_id: str, request_id: str) -> str | None:
    """Trace-based fallback: scan recent traces for one whose root-span
    inputs carry ``request_id`` and return its backing ``mlflow.sourceRun``.

    The parse TRACE reliably carries ``request_id`` in
    ``trace_metadata['mlflow.traceInputs']`` (serialised root-span inputs)
    even when the run-level ``request_id`` tag did not land. The backing
    run (``mlflow.sourceRun``) is the same run :func:`score_trace` downloads
    artifacts from. Returns ``None`` if no matching trace is found.
    Never raises — a search failure returns ``None`` (→ clean 404).
    """
    try:
        traces = mlf.search_traces(
            experiment_ids=[exp_id], max_results=_TRACE_SCAN_LIMIT,
        )
    except Exception:  # noqa: BLE001 - never fatal; surface as "not found"
        _LOGGER.warning(
            "resolve_run_id trace search failed for %s", request_id, exc_info=True,
        )
        return None
    for tmeta in _iter_trace_metadata(traces):
        inputs_json = (tmeta or {}).get("mlflow.traceInputs")
        if not inputs_json:
            continue
        try:
            inp = json.loads(inputs_json)
        except Exception:  # noqa: BLE001 - malformed preview, skip
            continue
        if isinstance(inp, dict) and inp.get("request_id") == request_id:
            run_id = (tmeta or {}).get("mlflow.sourceRun")
            if run_id:
                return run_id
    return None


def _iter_trace_metadata(traces: Any):
    """Yield each trace's ``request_metadata`` dict (the ``trace_metadata``
    column) from a ``search_traces`` result.

    Handles both the pandas ``DataFrame`` returned by ``mlflow.search_traces``
    in production (each row has a ``trace_metadata`` column) and a plain
    iterable of Trace-like objects (tests / older mlflow) whose ``.info``
    carries ``request_metadata``. Yields nothing for a ``None`` result.
    """
    if traces is None:
        return
    if hasattr(traces, "iterrows"):
        # pandas DataFrame path (production).
        for _, row in traces.iterrows():
            try:
                tmeta = row.get("trace_metadata")
            except Exception:  # noqa: BLE001 - defensive
                tmeta = None
            if tmeta is not None:
                yield tmeta
        return
    # Iterable of Trace-like objects (tests / older mlflow).
    for t in traces:
        info = getattr(t, "info", None)
        tmeta = getattr(info, "request_metadata", None)
        if isinstance(tmeta, dict):
            yield tmeta


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
    except Exception:  # noqa: BLE001 - retry configuration on the next call
        _LOGGER.warning("failed to configure MLflow tracking URI", exc_info=True)
        return
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


# The ``rationale`` field is OMITTED from the log_dict payload (not just
# truncated): it is Opus free-text that can echo card names / transaction
# descriptions from the PDF, and a length cap alone still leaks the first 200
# chars in cleartext — including on JUDGE_ERROR paths. The outcome +
# similarity already convey the verdict signal in the artifact; the
# free-text rationale is not needed there and is the one remaining PII
# vector. It is retained in the Lakebase verdict_payload (same protected
# Postgres boundary as extraction_payload).
_RATIONALE_OMITTED = True


def _redact_comparisons(comparisons: Any) -> list[dict[str, Any]]:
    """Build a PII-redacted JSON payload for the ``verdict_comparisons.json``
    MLflow artifact.

    Reuses :func:`harness.tracing_feedback.redact_feedback_value` — the SAME
    field-aware policy as feedback telemetry — so the leaf-driven rules stay
    consistent across both paths:

    * ``cardDisplayName`` / ``description`` (client PII) -> keyed HMAC, or
      omitted (None) when no HMAC key is configured (never a reversible
      unsalted digest).
    * ``lastFourDigit`` / ``amount`` / ``date`` / ``pointsEarnedThisCycle``
      / ``closingPoints`` -> retained raw (not individually identifying;
      documented trade-off — hashing them destroys analytics value).
    * any other leaf -> omitted (or HMAC if a key is configured).

    The ``rationale`` string is OMITTED entirely (not just truncated): it is
    Opus free-text that can echo cardholder names / transaction descriptions
    from the PDF, and a length cap still leaks the first N chars in
    cleartext — including on JUDGE_ERROR paths. The ``outcome`` and
    ``similarity`` already convey the verdict signal in the artifact. The
    ``field_path`` and row indices are NOT PII and are carried verbatim.
    """
    from harness.tracing_feedback import redact_feedback_value

    hmac_key = _resolve_feedback_hmac_key()
    out: list[dict[str, Any]] = []
    for c in comparisons:
        out.append({
            "field_path": c.field_path,
            "expected": redact_feedback_value(c.field_path, c.expected, hmac_key=hmac_key),
            "actual": redact_feedback_value(c.field_path, c.actual, hmac_key=hmac_key),
            "outcome": c.outcome.value,
            "card_index": c.card_index,
            "expected_row_index": c.expected_row_index,
            "actual_row_index": c.actual_row_index,
            "similarity": c.similarity,
            # rationale OMITTED — see module docstring (PII vector).
        })
    return out


def _resolve_feedback_hmac_key() -> bytes:
    """Resolve the feedback HMAC key from the tracing config (best-effort).

    Returns ``b""`` when unset — in which case PII leaves are OMITTED (None)
    rather than risk a reversible unsalted digest (the documented policy in
    harness.tracing_feedback). Never raises.
    """
    try:
        from harness.config_ws4 import get_tracing_config
        return get_tracing_config().feedback_hmac_key
    except Exception:  # noqa: BLE001 - telemetry-only; never fatal
        return b""


def score_trace(run_id: str, result_store: Any = None) -> dict[str, Any]:
    """Score a single MLflow trace by re-running the judge on its artifacts.

    Steps:
    1. Download ``statement.pdf`` and ``extraction.json`` artifacts from the run.
    2. Reconstruct a ``ParseRequest`` and ``ExtractionResult``.
    3. Call ``OpusJudgeAdapter.judge()`` → ``JudgeVerdict`` (Opus + comparisons).
    4. Compute metrics via ``verdict_to_metrics``.
    5. Log metrics + tag ``judged=true`` on the SAME MLflow run.
    6. Persist the verdict to Lakebase (best-effort) so ``GET /api/results``
       can surface the per-field expected/actual/outcome inline.
    7. Return a per-trace result dict.

    ``result_store`` is an optional :class:`contracts.ports.ResultStore`. When
    ``None`` the scorer builds its own lazily (best-effort; ``None`` if the
    Lakebase env is absent). Tests inject a fake to assert save behaviour.

    Returns ``{"run_id": ..., "status": "OK"|"ERROR", ...}`` — never raises
    (errors are captured in the ``status``/``error`` fields).
    """
    try:
        return _score_trace_impl(run_id, result_store)
    except Exception as exc:
        _LOGGER.warning(
            "score_trace failed for run %s: %s", run_id, exc, exc_info=True
        )
        return {"run_id": run_id, "status": "ERROR",
                "error": _sanitize_error(exc)}


def _sanitize_error(exc: Exception) -> str:
    """Map known exception types to safe error messages for the UI."""
    exc_name = type(exc).__name__
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return "network error"
    if "auth" in exc_name.lower() or "permission" in str(exc).lower():
        return "authentication error"
    if "not found" in str(exc).lower():
        return "resource not found"
    return exc_name


def _score_trace_impl(run_id: str, result_store: Any = None) -> dict[str, Any]:
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
    # it's distinguishable but still retriable. The status is parsed once here
    # and reused for the Lakebase persist decision below.
    summary = json.loads(verdict.summary) if verdict.summary else {}
    status = summary.get("status", "OK")
    if status == "OK":
        client.set_tag(run_id, "judged", "true")
    else:
        client.set_tag(run_id, "judged", "error")

    # 8. Persist the verdict to Lakebase so ``GET /api/results`` can surface
    # the per-field expected/actual/outcome inline on the per-parse Results
    # view. Best-effort by contract: a Lakebase write failure is logged and
    # MUST NOT abort the judge run or the aggregate — the MLflow metrics
    # above are already recorded. Only written on OK status: a JUDGE_ERROR
    # verdict carries no usable ground truth (Opus failed to read the PDF),
    # so persisting it would surface misleading expected values.
    # The verdict's ``request_id`` is set by the adapter from
    # ``request.request_id`` (== ``meta['request_id']``), so the upsert keys
    # the verdict into the same ``statement_results`` row as the extraction.
    if status == "OK":
        store = result_store if result_store is not None else _get_result_store()
        if store is not None:
            try:
                store.save_verdict(verdict)
            except Exception:
                _LOGGER.warning(
                    "save_verdict failed for request %s; inline verdict "
                    "not persisted", verdict.request_id, exc_info=True,
                )

    # 9. Best-effort: log the per-field expected/actual/outcome comparisons as
    # a JSON artifact on the run for trace visibility. Guarded like the
    # metrics — must never fail the run. Uses ``log_dict`` (plain JSON, no
    # pandas dependency) rather than ``log_table``; ``set_tag`` is avoided
    # (tag size limits).
    #
    # PII REDACTION (review fix): the raw ``expected``/``actual`` values
    # bypass MLflowTraceSink's recursive span-attribute redaction (which only
    # applies to span attrs/inputs/outputs). Real card display names and
    # transaction descriptions are client PII and MUST NOT reach the MLflow
    # artifact in cleartext. We reuse the EXACT field-aware policy from
    # harness.tracing_feedback.redact_feedback_value — keyed HMAC (or omit
    # when no HMAC key) for ``cardDisplayName``/``description``; retain
    # ``lastFourDigit`` and the non-PII numerics (amount/date/points) raw.
    # The ``rationale`` string is OMITTED entirely (it is Opus free-text that
    # may echo cardholder names / transaction descriptions from the PDF).
    # NOTE: Lakebase verdict_payload is NOT redacted here — it lives behind
    # the same protected Postgres boundary as the already-stored
    # extraction_payload, consistent with codex's posture confirmation.
    try:
        client.log_dict(run_id, _redact_comparisons(verdict.comparisons),
                        "verdict_comparisons.json")
    except Exception:
        _LOGGER.warning(
            "log_dict of verdict comparisons failed for %s", run_id, exc_info=True,
        )

    # 10. Build and return the per-trace result.
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


def run_judge_evaluation(sample_size: int = 10, result_store: Any = None) -> dict[str, Any]:
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
        tried = (
            os.getenv("MLFLOW_EXPERIMENT_ID")
            or os.getenv("MLFLOW_EXPERIMENT_PATH")
            or _EXPERIMENT_PATH
        )
        return {
            "count_judged": 0,
            "errors": [{"error": f"experiment not found: {tried}"}],
            "overall_strict": None,
            "overall_narration_forgiven": None,
            "per_field": {},
            "per_bank": {},
            "eval_run_id": None,
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
            "eval_run_id": None,
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
            "eval_run_id": None,
        }

    # 3. Pick the sample_size most recent randomly.
    sample = random.sample(run_ids, min(sample_size, len(run_ids)))

    # 4. Score each trace.  The optional ``result_store`` threads through to
    # :func:`score_trace` so each OK verdict is persisted inline (best-effort).
    results = [score_trace(rid, result_store=result_store) for rid in sample]

    # 5. Aggregate.
    summary = _aggregate_results(results)

    # 6. Additionally create an aggregated MLflow Evaluate run (best-effort)
    # so all judge results appear in one place in the experiments "Evaluations"
    # tab — the per-run metrics logged by ``score_trace`` are scattered across
    # individual parse runs; this evaluation run aggregates them into a
    # single per-row table + aggregate metrics.  Best-effort: failure is
    # logged and ``eval_run_id`` is left ``None`` so the summary still returns.
    from judge.evaluator import run_mlflow_evaluation

    eval_info = run_mlflow_evaluation(results, summary, experiment_id=exp_id)
    summary["eval_run_id"] = eval_info["eval_run_id"] if eval_info else None

    return summary


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
