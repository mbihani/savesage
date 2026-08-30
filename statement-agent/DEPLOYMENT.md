# SaveSage Statement Agent — Deployment Guide

This guide deploys the statement-agent as a Databricks App on a customer
workspace. The app exposes a **synchronous JSON API** (`POST /api/v1/parse`)
as the primary programmatic integration point, plus an optional web UI and a
background judge scheduler.

```text
PDF + Bank  -->  POST /api/v1/parse  -->  JSON extraction (synchronous)
                    |
                    +-> MLflow traces (auto-created experiment)
                    +-> RDS Postgres (durable results + feedback)
                    +-> Background judge (every 6h, post-hoc verdicts)
```

---

## 1. Prerequisites

1. **Databricks workspace** with:
   - **Serverless** compute (the app runs on the Apps managed runtime).
   - **Unity Catalog** enabled (the app service principal needs access to any
     UC objects you reference; the default config does not require a catalog).
2. **AI Gateway** with two serving endpoints, fronted behind FMAPI names:
   - A **Luna** extraction endpoint (default FMAPI name
     `databricks-gpt-5-6-luna`).
   - A **Claude** judge endpoint (default FMAPI name
     `databricks-claude-opus-5`).
   - The app service principal must be granted **CAN_QUERY** on both endpoints.
3. **Postgres** for durable result + feedback storage. Any managed Postgres
   works (Databricks Lakebase PG, AWS RDS, etc.). The app connects directly
   over TCP with username/password — no Databricks `database` resource binding
   is required.
4. **Databricks CLI** (`databricks` ≥ 0.255.0), authenticated to the target
   workspace (`databricks auth login`).

> The app **auto-creates** its MLflow experiment on the first parse, so no
> pre-existing experiment or bound experiment resource is required.

---

## 2. Environment variables

All runtime configuration is environment variables set in `app.yaml`. The
values below are the defaults shipped in this repo — edit `app.yaml` before
deploying.

### Workspace + model endpoints

| Variable | Default | Description |
|---|---|---|
| `DATABRICKS_HOST` | `https://fevm-stable-classic-7ppxjq.cloud.databricks.com` | Target workspace URL. **Set to your workspace.** |
| `EXTRACTION_ENDPOINT` | `databricks-gpt-5-6-luna` | FMAPI name of the Luna extraction endpoint on your AI Gateway. |
| `JUDGE_ENDPOINT` | `databricks-claude-opus-5` | FMAPI name of the Claude judge endpoint on your AI Gateway. |

### Postgres (durable storage)

| Variable | Default | Description |
|---|---|---|
| `RDS_HOST` | `your-rds-instance.xxxxxx.us-east-1.rds.amazonaws.com` | Postgres host. **Set to your instance.** |
| `RDS_PORT` | `5432` | Postgres port. |
| `RDS_DATABASE` | `postgres` | Database name. |
| `RDS_USER` | `postgres` | Database user. |
| `RDS_PASSWORD` | `CHANGE_ME` | Database password. **Set a real secret.** |
| `RDS_SSLMODE` | `require` | `psycopg` SSL mode (`require` / `prefer` / `disable`). |

The app runs `CREATE TABLE IF NOT EXISTS` for the results + feedback tables on
first connect, so the database just needs to exist and the user needs
`CREATE` + `INSERT`/`SELECT`/`UPDATE` on the `public` schema.

### MLflow tracing

| Variable | Default | Description |
|---|---|---|
| `WS4_TRACING_ENABLED` | `true` | Master telemetry switch. Set `false` to disable all MLflow calls. |
| `WS4_TRACKING_URI` | `databricks` | `mlflow.set_tracking_uri` target. Keep `databricks` on the platform. |
| `MLFLOW_EXPERIMENT_NAME` | `/Shared/savesage/statement-agent` | MLflow experiment **path**. Auto-created on first parse. Override to isolate traces per workspace (e.g. `/Users/<you>/savesage/statement-agent`). |

> **Backward compat:** a deployment may instead set `MLFLOW_EXPERIMENT_ID` (the
> numeric experiment ID). When present it takes precedence over the NAME. New
> customer deployments should use `MLFLOW_EXPERIMENT_NAME` — no pre-existing
> experiment or bound resource is needed.

### Background judge scheduler

| Variable | Default | Description |
|---|---|---|
| `JUDGE_INTERVAL_HOURS` | `6` | Run the post-hoc judge every N hours. Set to `0` (or any value ≤ 0) to **disable** the scheduler. |
| `JUDGE_SAMPLE_SIZE` | `10` | Number of recent parses the scheduled judge samples per run (capped at 50). |

When the scheduler is disabled, the manual `POST /api/run-judge` and on-demand
`POST /api/results/{request_id}/judge` endpoints still work.

---

## 3. Deploy

From this directory (`statement-agent/`), with the CLI authenticated to your
workspace:

```bash
# Edit app.yaml with your workspace host, RDS credentials, and (optionally)
# custom endpoint/experiment/judge values.

# Create + deploy the app (the app.yaml in this directory is picked up
# automatically from the source path).
databricks apps deploy savesage-statement-agent --source-code-path .
```

The Apps runtime installs `requirements.txt` and starts
`uvicorn app.main:app --host 0.0.0.0 --port 8000`. The app URL is printed by the
deploy command (also visible in the Apps UI). Below, `<APP_URL>` is that URL.

> Deploy does not run locally — `requirements.txt` deps are installed by the
> Apps runtime. Only the stdlib contract tests run on a dev machine.

---

## 4. API usage — synchronous parse

`POST /api/v1/parse` is the **primary customer integration point**. It accepts
a PDF + bank name and returns the extracted JSON synchronously.

### Request

`multipart/form-data`:
- `file` (required): the statement PDF upload.
- `bank` (required): the bank name (e.g. `HDFC`, `ICICI`, `SBI`, `AXIS`).
  Case-insensitive; validated for format (letters, numbers, `_`, `-`).
  Unknown banks fall back to a generic prompt/schema.

```bash
curl -sS -X POST "<APP_URL>/api/v1/parse" \
  -F "file=@statement.pdf" \
  -F "bank=HDFC"
```

### Response — success (200)

```json
{
  "request_id": "req-a1b2c3d4e5f6",
  "bank": "HDFC",
  "status": "SUCCESS",
  "extraction": {
    "payload": {
      "cards": [ { "cardMeta": { "cardDisplayName": "..." }, ... } ],
      "transactions": [ ... ],
      "rewards": { ... },
      "statementMeta": { ... },
      "statementLevelSummary": { ... }
    },
    "model_id": "databricks-gpt-5-6-luna",
    "schema_valid": true,
    "validation_errors": []
  },
  "verdict": null
}
```

`status` is the pipeline outcome: `SUCCESS` (every stage clean) or `PARTIAL`
(extraction succeeded but validation flagged schema/rule issues — the payload
is still returned, with `validation_errors` populated). Both are HTTP 200.

### Response — extraction failed (422)

```json
{
  "request_id": "req-...",
  "bank": "HDFC",
  "status": "EXTRACTION_FAILED",
  "extraction": null,
  "error": "extraction produced no result",
  "verdict": null
}
```

### Errors

| HTTP | Cause |
|---|---|
| `400` | Invalid PDF (empty, or missing `%PDF` magic bytes) or invalid bank name. |
| `422` | Extraction failed (the `error` field carries the message). |
| `504` | The pipeline did not complete within the sync timeout (300s). |

### Notes

- **Tracing:** every `/api/v1/parse` call is traced end-to-end to MLflow
  (same trace sink as the UI path). The `request_id` is the trace/run tag.
- **Follow-up queries:** the result is kept in-memory for the process lifetime,
  so `GET <APP_URL>/api/results/{request_id}` returns the same extraction plus
  any feedback / on-demand verdict for follow-up workflows.
- The synchronous endpoint runs the full pipeline (route → extract → validate →
  persist → finalize) and blocks until it completes. It does **not** stream.

---

## 5. Judge configuration

The post-hoc judge compares Luna extractions against Opus-read ground truth and
writes per-field verdicts. There are three ways to trigger it:

1. **Background scheduler** (default, zero-config): a daemon thread runs the
   judge every `JUDGE_INTERVAL_HOURS` (default 6h), sampling
   `JUDGE_SAMPLE_SIZE` recent parses. Check its status:

   ```bash
   curl -sS "<APP_URL>/api/v1/judge/status"
   ```

   ```json
   {
     "active": true,
     "interval_hours": 6.0,
     "sample_size": 10,
     "last_run_at": "2026-08-30T12:00:00+00:00",
     "next_run_at": "2026-08-30T18:00:00+00:00",
     "last_summary": { "count_judged": 10, "count_errors": 0, "overall_strict": 0.91, ... }
   }
   ```

   Set `JUDGE_INTERVAL_HOURS=0` in `app.yaml` to disable the scheduler (the
   manual endpoints below still work). The scheduler never blocks app startup
   and skips a tick if a manual judge is already running.

2. **Manual batch trigger:**

   ```bash
   curl -sS -X POST "<APP_URL>/api/run-judge" \
     -H "Content-Type: application/json" -d '{"sample_size": 10}'
   # poll for the result:
   curl -sS "<APP_URL>/api/judge-results"
   ```

3. **On-demand single-trace judge** (renders inline verdicts on the Results
   view): `POST <APP_URL>/api/results/{request_id}/judge`, then poll
   `GET <APP_URL>/api/results/{request_id}/judge`.

The scheduler and the manual endpoints share one concurrency slot, so they
never run the (expensive) Opus judge concurrently.

---

## 6. MLflow experiment setup

No setup step is required — the experiment is **auto-created** on the first
parse. On startup the tracing layer calls `mlflow.set_experiment(
MLFLOW_EXPERIMENT_NAME)` (default `/Shared/savesage/statement-agent`); MLflow
creates the experiment at that path if it does not exist, then every parse
writes its trace there.

To verify after the first parse:

```bash
# List experiments and confirm the path exists.
databricks experiments list
# Open the experiment in the workspace UI:
#   <DATABRICKS_HOST>#mlflow/experiments/<EXPERIMENT_ID>
```

To isolate traces per workspace or user, set `MLFLOW_EXPERIMENT_NAME` to a
different path (e.g. `/Users/<you>/savesage/statement-agent`). The app needs
write permission on the parent folder; `/Shared/...` requires an admin to
grant the app service principal `CAN_EDIT` on `/Shared/savesage/` (or use a
`/Users/...` path the app identity owns).

> If you already have a numeric experiment ID from a prior deployment, set
> `MLFLOW_EXPERIMENT_ID` instead — it takes precedence over the NAME and skips
> the by-name lookup. Do not set both; prefer `MLFLOW_EXPERIMENT_NAME` for new
> deployments.

---

## 7. Other endpoints (existing UI + async API)

The synchronous `/api/v1/parse` is additive — the existing async/UI endpoints
are unchanged:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/parse` | Async parse (returns `request_id`; stream via SSE). Used by the web UI. |
| `GET` | `/api/parse/{request_id}/stream` | SSE stream of parse progress + extraction items. |
| `GET` | `/api/results/{request_id}` | Fetch extraction + verdict + feedback for a request. |
| `POST` | `/api/feedback/{request_id}` | Submit per-field Accept/Correct feedback. |
| `GET` | `/api/banks` | List built-in + dynamic banks. |
| `GET` | `/health` | Liveness + config diagnostics. |

The web UI is served at the app root (`<APP_URL>/`).
