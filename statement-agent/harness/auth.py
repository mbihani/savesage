"""Databricks token acquisition without importing the SDK during contract tests."""

import os


def acquire_token() -> str:
    """Use app-provided token first, then lazily ask the Databricks SDK."""
    token = os.getenv("DATABRICKS_TOKEN")
    if token:
        return token
    try:
        from databricks.sdk import WorkspaceClient  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("DATABRICKS_TOKEN is unset and databricks-sdk is unavailable") from exc
    headers = WorkspaceClient().config.authenticate()
    auth = headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise RuntimeError("Databricks SDK did not provide a bearer token")
    return auth.removeprefix("Bearer ")
