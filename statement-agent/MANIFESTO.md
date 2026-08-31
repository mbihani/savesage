# Configuration manifesto

Find every adjustable setting with `rg 'CONFIGURE\(' statement-agent`.

## Required

- Databricks app identity must be authorized for both serving endpoints and bound resources.

## Customize

- `CONFIGURE(workspace-host)`, `CONFIGURE(extraction-endpoint)`, `CONFIGURE(judge-endpoint)`
- `CONFIGURE(uc-catalog)`, `CONFIGURE(uc-schema)`, `CONFIGURE(mlflow-experiment)`
- `CONFIGURE(results-table)`, `CONFIGURE(feedback-table)`, `CONFIGURE(cdf-table)`

## Optional

- `CONFIGURE(request-timeout)` and `CONFIGURE(max-attempts)` tune transport behavior.
