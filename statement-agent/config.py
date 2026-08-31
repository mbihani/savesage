"""Environment-only runtime configuration; no secrets are embedded here."""

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    workspace_host: str = "https://fevm-stable-classic-7ppxjq.cloud.databricks.com"
    extraction_endpoint: str = "databricks-gpt-5-6-luna"
    judge_endpoint: str = "databricks-claude-opus-5"
    uc_catalog: str = "stable_classic_7ppxjq_catalog"
    uc_schema: str = "savesage"
    mlflow_experiment_path: str = "/Shared/savesage/statement-agent"
    results_table: str = "statement_results"
    feedback_table: str = "field_feedback"
    cdf_table: str = "statement_results_cdf"
    request_timeout_seconds: float = 180.0
    max_attempts: int = 4

    def endpoint_url(self, endpoint: str) -> str:
        return f"{self.workspace_host.rstrip('/')}/serving-endpoints/{endpoint}/invocations"


_DEFAULTS = Settings()


def get_settings() -> Settings:
    """Read a fresh environment snapshot on every call."""
    return Settings(
        workspace_host=os.getenv("DATABRICKS_HOST", _DEFAULTS.workspace_host),  # CONFIGURE(workspace-host)
        extraction_endpoint=os.getenv("EXTRACTION_ENDPOINT", _DEFAULTS.extraction_endpoint),  # CONFIGURE(extraction-endpoint)
        judge_endpoint=os.getenv("JUDGE_ENDPOINT", _DEFAULTS.judge_endpoint),  # CONFIGURE(judge-endpoint)
        uc_catalog=os.getenv("UC_CATALOG", _DEFAULTS.uc_catalog),  # CONFIGURE(uc-catalog)
        uc_schema=os.getenv("UC_SCHEMA", _DEFAULTS.uc_schema),  # CONFIGURE(uc-schema)
        mlflow_experiment_path=os.getenv("MLFLOW_EXPERIMENT_PATH", _DEFAULTS.mlflow_experiment_path),  # CONFIGURE(mlflow-experiment)
        results_table=os.getenv("RESULTS_TABLE", _DEFAULTS.results_table),  # CONFIGURE(results-table)
        feedback_table=os.getenv("FEEDBACK_TABLE", _DEFAULTS.feedback_table),  # CONFIGURE(feedback-table)
        cdf_table=os.getenv("CDF_TABLE", _DEFAULTS.cdf_table),  # CONFIGURE(cdf-table)
        request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", str(_DEFAULTS.request_timeout_seconds))),  # CONFIGURE(request-timeout)
        max_attempts=int(os.getenv("MAX_ATTEMPTS", str(_DEFAULTS.max_attempts))),  # CONFIGURE(max-attempts)
    )
