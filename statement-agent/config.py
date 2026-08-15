"""Environment-only runtime configuration; no secrets are embedded here."""

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    # CONFIGURE(workspace-host)
    workspace_host: str = os.getenv("DATABRICKS_HOST", "https://fevm-stable-classic-7ppxjq.cloud.databricks.com")
    # CONFIGURE(extraction-endpoint)
    extraction_endpoint: str = os.getenv("EXTRACTION_ENDPOINT", "databricks-gpt-5-6-luna")
    # CONFIGURE(judge-endpoint)
    judge_endpoint: str = os.getenv("JUDGE_ENDPOINT", "databricks-claude-opus-5")
    # CONFIGURE(uc-catalog)
    uc_catalog: str = os.getenv("UC_CATALOG", "stable_classic_7ppxjq_catalog")
    # CONFIGURE(uc-schema)
    uc_schema: str = os.getenv("UC_SCHEMA", "savesage")
    # CONFIGURE(mlflow-experiment)
    mlflow_experiment_path: str = os.getenv("MLFLOW_EXPERIMENT_PATH", "/Shared/savesage/statement-agent")
    # CONFIGURE(lakebase-project)
    lakebase_project: str = os.getenv("LAKEBASE_PROJECT", "savesage")
    # CONFIGURE(lakebase-host)
    lakebase_host: str = os.getenv("LAKEBASE_HOST", "")
    # CONFIGURE(lakebase-database)
    lakebase_database: str = os.getenv("LAKEBASE_DATABASE", "savesage")
    # CONFIGURE(results-table)
    results_table: str = os.getenv("RESULTS_TABLE", "statement_results")
    # CONFIGURE(feedback-table)
    feedback_table: str = os.getenv("FEEDBACK_TABLE", "field_feedback")
    # CONFIGURE(cdf-table)
    cdf_table: str = os.getenv("CDF_TABLE", "statement_results_cdf")
    # CONFIGURE(request-timeout)
    request_timeout_seconds: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "180"))
    # CONFIGURE(max-attempts)
    max_attempts: int = int(os.getenv("MAX_ATTEMPTS", "4"))

    def endpoint_url(self, endpoint: str) -> str:
        return f"{self.workspace_host.rstrip('/')}/serving-endpoints/{endpoint}/invocations"


def get_settings() -> Settings:
    return Settings()
