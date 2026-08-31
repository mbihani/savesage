# Workstream 4 — MLflow resources for WS6 consolidation

This file holds the **commented** Databricks App `resources` snippet that WS6
must consolidate into `app.yaml`. WS4 owns the telemetry code; WS6 solely owns
`app.yaml`. Do not edit `app.yaml` from this workstream — paste the block below
into the WS6 integration pass.

The snippet binds the MLflow experiment used by `harness/tracing.py`. In the
Databricks Apps runtime, a bound experiment resource supplies the tracking
URI / auth context, so the local `DATABRICKS_CONFIG_PROFILE=fevm-stable`
fallback in `harness/config_ws4.py` is moot there.

## app.yaml `resources` block (commented — WS6 to uncomment & merge)

```yaml
# --- Workstream 4: MLflow experiment binding ---
# Bind the experiment at /Shared/savesage/statement-agent (config.Settings.
# mlflow_experiment_path) so the App can write traces/feedback/assessments.
# Schema verified against databricks-sdk 0.63.0:
#   AppResourceExperiment(experiment_id: str, permission: CAN_READ|CAN_EDIT|CAN_MANAGE)
# Replace <EXPERIMENT_ID> with the numeric experiment id from the workspace
# (databricks experiments get --experiment-id <id> -p fevm-stable). CAN_EDIT
# lets the agent create/append traces and log feedback assessments.
resources:
  - name: savesage-statement-agent-mlflow
    description: "MLflow experiment for statement-agent tracing + field feedback"
    experiment_spec:
      permission: CAN_EDIT
    # experiment_id is supplied at deploy via the UI/CLI (the workspace assigns
    # the numeric id; WS6 fills it during integration). Equivalent SDK call:
    #   AppResourceExperiment(experiment_id="<EXPERIMENT_ID>", permission="CAN_EDIT")
```

## Why CAN_EDIT (not CAN_READ)

The agent *writes* traces (`mlflow.start_span_no_context` → `mlflow._log_trace`),
logs feedback assessments (`mlflow.log_feedback`), and logs judge assessments.
All three require edit on the experiment; `CAN_READ` would let traces render
but block writes.

## Env vars WS4 expects WS6 to wire

These come from `harness/config_ws4.py` (WS4-owned, `CONFIGURE(<slug>)`-tagged).
WS6 may surface them in `app.yaml` `env:` if operator-tunable:

| Env var | Default | Purpose |
|---|---|---|
| `WS4_TRACING_ENABLED` | `true` | Master telemetry switch (requirement 6 kill-switch) |
| `WS4_TRACKING_URI` | `databricks` | `mlflow.set_tracking_uri` target |
| `DATABRICKS_CONFIG_PROFILE` | `fevm-stable` | Local-only profile; runtime ignores |
| `MLFLOW_EXPERIMENT_PATH` | (from `config.py`) | Fallback experiment path |
| `WS4_AUTOLOG_LANGCHAIN` | `true` | Enable `mlflow.langchain.autolog` |
| `WS4_REDACT_PII` | `true` | PII redaction in telemetry (see judge/scorer.py) |
| `WS4_LOG_NONPII_RAW` | `true` | Log non-PII values (amount/date/last4) raw |
| `WS4_COST_RATES_*` | (via code) | Per-model USD/1M-token cost rates |

## What needs verifying on fevm-stable at deploy

`mlflow` cannot be installed locally (pypi blackholed), so these were NOT RUN
against a live Databricks MLflow. Verify on fevm-stable:

1. `mlflow.set_tracking_uri("databricks")` + `mlflow.set_experiment(path)`
   resolve the bound experiment and traces land in it.
2. `mlflow.start_span_no_context` writes a trace visible in the experiment UI
   with the nested span hierarchy (parse → extraction/validation/persistence/
   judging).
3. `mlflow.log_feedback(...)` with `AssessmentSource(AssessmentSourceType.HUMAN,
   ...)` appears as a feedback assessment on the trace.
4. Cost set via `span.set_attribute("mlflow.llm.cost", {...})` renders in the
   trace UI (explicit cost, since the FMAPI model names are not natively priced).
5. Kill-switch: with `WS4_TRACING_ENABLED=false`, a parse still succeeds and
   emits no MLflow calls.
