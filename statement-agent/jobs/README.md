# Databricks Jobs

`run_judge.py` samples recent runs that are not tagged `judged=true`, invokes the existing Opus 5 judge pipeline, logs judge metrics and assessments to MLflow, and tags successfully judged runs.

`sync_traces.py` reads recent MLflow traces and their source-run metadata, then upserts them by `trace_id` into `savesage.ops.traces` (or a table supplied with `--table`).

## Requirements

Run both scripts from the repository root on a Databricks Job cluster with `mlflow`, `databricks-sdk`, and `pyspark` available. Configure:

- `MLFLOW_EXPERIMENT_ID` (required unless `--experiment-id` is passed)
- Databricks authentication through the Job identity
- Judge configuration used by `judge/opus.py`, including access to the configured Opus 5 serving endpoint
- Unity Catalog permissions to create and modify the trace-sync table

## Create the jobs

The following examples assume the repository is available at `/Workspace/Repos/<user>/statement-agent` and the JSON job definitions reference an existing cluster. Adjust paths and cluster IDs for the workspace.

```bash
databricks jobs create --json '{
  "name": "savesage-judge",
  "tasks": [{
    "task_key": "judge",
    "existing_cluster_id": "<cluster-id>",
    "spark_python_task": {
      "python_file": "/Workspace/Repos/<user>/statement-agent/jobs/run_judge.py",
      "parameters": ["--sample-size", "10"]
    }
  }],
  "schedule": {
    "quartz_cron_expression": "0 0 * * * ?",
    "timezone_id": "UTC",
    "pause_status": "UNPAUSED"
  }
}'
```

```bash
databricks jobs create --json '{
  "name": "savesage-trace-sync",
  "tasks": [{
    "task_key": "sync_traces",
    "existing_cluster_id": "<cluster-id>",
    "spark_python_task": {
      "python_file": "/Workspace/Repos/<user>/statement-agent/jobs/sync_traces.py",
      "parameters": ["--hours-back", "24", "--table", "savesage.ops.traces"]
    }
  }],
  "schedule": {
    "quartz_cron_expression": "0 0/15 * * * ?",
    "timezone_id": "UTC",
    "pause_status": "UNPAUSED"
  }
}'
```

Suggested schedules are hourly for judging and every 15 minutes for trace sync.
