# Configuration manifesto

Find every adjustable setting with `rg 'CONFIGURE\(' statement-agent`.

## Required

- `CONFIGURE(lakebase-host)`: set `LAKEBASE_HOST` to the provisioned project host.
- Databricks app identity must be authorized for both serving endpoints and bound resources.

## Customize

- `CONFIGURE(workspace-host)`, `CONFIGURE(extraction-endpoint)`, `CONFIGURE(judge-endpoint)`
- `CONFIGURE(uc-catalog)`, `CONFIGURE(uc-schema)`, `CONFIGURE(mlflow-experiment)`
- `CONFIGURE(lakebase-project)`, `CONFIGURE(lakebase-database)`
- `CONFIGURE(results-table)`, `CONFIGURE(feedback-table)`, `CONFIGURE(cdf-table)`

## Optional

- `CONFIGURE(request-timeout)` and `CONFIGURE(max-attempts)` tune transport behavior.
