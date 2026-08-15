"""Idempotently provision PG17 Lakebase and its Postgres-to-Delta CDF."""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

from .config_ws3 import LakebaseSettings, get_lakebase_settings
from .sql import DDL


def _client(settings: LakebaseSettings) -> Any:
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient(profile=settings.profile)


def _project_id(name: str | None) -> str:
    return (name or "").removeprefix("projects/")


def ensure_project(client: Any, settings: LakebaseSettings) -> Any:
    from databricks.sdk.service.postgres import Project, ProjectDefaultEndpointSettings, ProjectSpec
    projects = {_project_id(item.name): item for item in client.postgres.list_projects()}
    project = projects.get(settings.project_id)
    if project is None:
        operation = client.postgres.create_project(Project(spec=ProjectSpec(
            display_name="SaveSage statement agent",
            pg_version=17,
            default_endpoint_settings=ProjectDefaultEndpointSettings(
                autoscaling_limit_min_cu=0.5, autoscaling_limit_max_cu=1.0))), settings.project_id)
        operation.wait()
        project = client.postgres.get_project(f"projects/{settings.project_id}")
        print("CREATE_PROJECT", json.dumps(project.as_dict(), sort_keys=True))
    else:
        print("REUSE_PROJECT", json.dumps(project.as_dict(), sort_keys=True))
    version = project.status.pg_version if project.status else None
    if version != 17:
        raise RuntimeError(f"project {settings.project_id} is PostgreSQL {version}, CDF requires 17")
    return project


def resolve_runtime(client: Any, settings: LakebaseSettings) -> tuple[str, str, str]:
    parent = f"projects/{settings.project_id}"
    branches = list(client.postgres.list_branches(parent))
    branch = next((item for item in branches if item.status and item.status.default), None)
    if branch is None:
        raise RuntimeError(f"no default branch: {[item.as_dict() for item in branches]}")
    endpoints = list(client.postgres.list_endpoints(branch.name))
    endpoint = next((item for item in endpoints if item.name.endswith(f"/{settings.endpoint_id}")), None)
    if endpoint is None or not endpoint.status or not endpoint.status.hosts:
        raise RuntimeError(f"endpoint {settings.endpoint_id} unavailable: {[item.as_dict() for item in endpoints]}")
    return branch.name.rsplit("/", 1)[-1], endpoint.name, endpoint.status.hosts.host


def _connect(client: Any, endpoint: str, host: str, settings: LakebaseSettings) -> Any:
    token = client.postgres.generate_database_credential(endpoint).token
    user = client.current_user.me().user_name
    try:
        import psycopg
        return psycopg.connect(host=host, port=5432, dbname=settings.database,
                               user=user, password=token, sslmode="require")
    except ImportError:
        try:
            import pg8000.dbapi
        except ImportError as exc:
            raise RuntimeError("provisioning DDL requires psycopg or pg8000") from exc
        return pg8000.dbapi.connect(host=host, port=5432, database=settings.database,
                                    user=user, password=token, ssl_context=True)


def apply_ddl(client: Any, endpoint: str, host: str, settings: LakebaseSettings) -> None:
    connection = _connect(client, endpoint, host, settings)
    try:
        cursor = connection.cursor()
        try:
            for statement in (part.strip() for part in DDL.split(";") if part.strip()):
                cursor.execute(statement)
            connection.commit()
        finally:
            cursor.close()
    finally:
        connection.close()
    print("DDL_APPLIED", settings.database)


def ensure_uc_schema(client: Any, settings: LakebaseSettings) -> None:
    full_name = f"{settings.catalog}.{settings.schema}"
    try:
        schema = client.schemas.get(full_name)
        action = "REUSE_UC_SCHEMA"
    except Exception as exc:
        if getattr(exc, "error_code", None) not in {"SCHEMA_DOES_NOT_EXIST", "RESOURCE_DOES_NOT_EXIST"}:
            raise
        schema = client.schemas.create(settings.schema, settings.catalog,
            comment="SaveSage Lakebase CDF managed Delta history")
        action = "CREATE_UC_SCHEMA"
    print(action, json.dumps(schema.as_dict(), sort_keys=True))


def cdf_path(settings: LakebaseSettings, branch: str) -> str:
    return (f"/api/2.0/postgres/projects/{settings.project_id}/branches/{branch}"
            f"/databases/{settings.database}/cdf-configs")


def ensure_cdf(client: Any, settings: LakebaseSettings, branch: str) -> dict[str, Any]:
    path = cdf_path(settings, branch)
    try:
        response = client.api_client.do("GET", f"{path}/public")
        print("REUSE_CDF", json.dumps(response, sort_keys=True))
        return response
    except Exception as exc:
        if getattr(exc, "error_code", None) not in {"NOT_FOUND", "RESOURCE_DOES_NOT_EXIST"}:
            raise
    body = {"catalog": settings.catalog, "schema": settings.schema,
            "postgres_schema": "public"}
    response = client.api_client.do("POST", path, query={"cdf_config_id": "public"}, body=body)
    print("CREATE_CDF", json.dumps(response, sort_keys=True))
    return response


def poll_cdf(client: Any, settings: LakebaseSettings, branch: str, timeout: int) -> dict[str, Any]:
    path = f"{cdf_path(settings, branch)}/public/cdf-statuses"
    deadline = time.monotonic() + timeout
    while True:
        response = client.api_client.do("GET", path)
        print("CDF_STATUS", json.dumps(response, sort_keys=True))
        statuses = response.get("cdf_statuses", [])
        states = {str(item.get("state", "")).upper() for item in statuses}
        expected = {item.get("postgres_table") for item in statuses}
        if {"statement_results", "field_feedback"}.issubset(expected) and states == {"CDF_STATE_STREAMING"}:
            return response
        if "CDF_STATE_TERMINATED" in states:
            raise RuntimeError(f"CDF unhealthy: {response}")
        if time.monotonic() >= deadline:
            raise TimeoutError(f"CDF did not become healthy in {timeout}s; last response: {response}")
        time.sleep(10)


def provision(settings: LakebaseSettings, timeout: int) -> None:
    client = _client(settings)
    ensure_project(client, settings)
    branch, endpoint, host = resolve_runtime(client, settings)
    print("RUNTIME", json.dumps({"branch": branch, "endpoint": endpoint, "host": host}, sort_keys=True))
    ensure_uc_schema(client, settings)
    apply_ddl(client, endpoint, host, settings)
    ensure_cdf(client, settings, branch)
    poll_cdf(client, settings, branch, timeout)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cdf-timeout-seconds", type=int, default=600)
    args = parser.parse_args()
    provision(get_lakebase_settings(), args.cdf_timeout_seconds)


if __name__ == "__main__":
    main()
