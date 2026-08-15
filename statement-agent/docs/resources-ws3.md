# Workstream 3: Lakebase resources and CDF runbook

## WS6 app resource handoff

Attach the autoscaling Lakebase database resource to the app with **Can connect**
permission. Databricks Apps injects `PGHOST`, `PGUSER`, `PGPORT`, and `PGDATABASE`;
only the endpoint resource name must be declared explicitly. WS6 can consolidate
this commented snippet into `app.yaml` after attaching the database resource:

```yaml
# WS3 Lakebase resource (attach in Compute > Apps > Edit > Add resource > Database):
# project: savesage-statement-agent
# branch: production
# database: databricks_postgres
# permission: CAN_CONNECT
# env:
#   - name: ENDPOINT_NAME
#     value: projects/savesage-statement-agent/branches/production/endpoints/primary
#   - name: PGSSLMODE
#     value: require
```

The app service principal must also have `CONNECT` to `databricks_postgres`,
`USAGE` and `CREATE` on Postgres schema `public`, and DML privileges on
`statement_results` and `field_feedback`. Do not place a database password in
`app.yaml`; the adapter requests a fresh OAuth database credential per connection.

## Provision or re-run

From `statement-agent`:

```bash
python3 -m db.provision --cdf-timeout-seconds 600
```

Defaults are tagged `CONFIGURE(...)` in `db/config_ws3.py` and can be overridden
with `WS3_DATABRICKS_PROFILE`, `WS3_LAKEBASE_PROJECT`, `WS3_LAKEBASE_DATABASE`,
`WS3_UC_CATALOG`, `WS3_UC_SCHEMA`, and `WS3_LAKEBASE_ENDPOINT`.

The script reuses only a project whose reported version is PostgreSQL 17. It
resolves the default branch instead of assuming `main`, creates/reuses the UC
schema, serializes concurrent DDL initialization with a transaction advisory
lock, sets both source tables to `REPLICA IDENTITY FULL`, creates the `public`
CDF configuration, and waits until both table statuses are streaming.

CDF is intentionally used in the Postgres -> UC direction. Synced tables run in
the reverse direction, while Lakehouse Federation is passthrough and creates no
managed Delta files; neither satisfies this design.

## Verify flow

Inspect the control plane:

```bash
python3 - <<'PY'
from databricks.sdk import WorkspaceClient
import json
w = WorkspaceClient(profile="fevm-stable")
base = "/api/2.0/postgres/projects/savesage-statement-agent/branches/production/databases/databricks_postgres/cdf-configs/public"
print(json.dumps(w.api_client.do("GET", base), indent=2, sort_keys=True))
print(json.dumps(w.api_client.do("GET", base + "/cdf-statuses"), indent=2, sort_keys=True))
PY
databricks tables get stable_classic_7ppxjq_catalog.savesage.lb_statement_results_history --profile fevm-stable -o json
databricks tables get stable_classic_7ppxjq_catalog.savesage.lb_field_feedback_history --profile fevm-stable -o json
```

Insert an obviously synthetic source row through the application/store, wait at
least 15 seconds, then query the managed Delta history and confirm
`_pg_change_type = 'insert'`. Never use a real statement for this check.

Apply `db/current_state.sql` with a SQL warehouse after the history tables exist.
It exposes current-state views by ranking changes on `_pg_lsn` then `_sort_by`,
the WAL and within-WAL ordering fields. Wall-clock `_timestamp` is not safe for
ordering batched change records. A latest `delete` removes that key from the
view; update preimages lose to their ordered postimages.

## Tear down

This destroys the WS3 project and its Postgres source data. Delete the CDF
configuration first so replication stops, then delete the project. UC history
tables and views are retained by default for audit; remove them separately only
when their retention owner approves.

```bash
databricks api delete /api/2.0/postgres/projects/savesage-statement-agent/branches/production/databases/databricks_postgres/cdf-configs/public --profile fevm-stable
databricks api delete /api/2.0/postgres/projects/savesage-statement-agent --profile fevm-stable
```

## Live run on 2026-08-15

Project `savesage-statement-agent` was created at 0.5-1 CU and reports PostgreSQL
17; its default branch is `production`. The UC schema already existed and the
idempotent source DDL completed repeatedly. CDF creation did **not** complete:

```text
databricks.sdk.errors.platform.NotFound: CdfConfig not found: projects/savesage-statement-agent/branches/production/databases/databricks_postgres
```

That response came from `POST .../cdf-configs?cdf_config_id=public` with the
required `catalog`, `schema`, and `postgres_schema` fields. Consequently the two
`lb_*_history` tables, current-state views, and row-flow evidence were not
created in this run.
