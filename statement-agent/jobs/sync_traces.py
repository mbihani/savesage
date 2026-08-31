#!/usr/bin/env python3
"""Upsert recent MLflow trace and run metadata into a Delta table."""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

DEFAULT_TABLE = "savesage.ops.traces"
_TABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours-back", type=float, default=24)
    parser.add_argument(
        "--experiment-id", default=os.getenv("MLFLOW_EXPERIMENT_ID")
    )
    parser.add_argument("--table", default=DEFAULT_TABLE)
    return parser.parse_args()


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def _trace_values(trace: Any) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    info = getattr(trace, "info", None)
    metadata = dict(getattr(info, "request_metadata", None) or {})
    inputs = _as_dict(metadata.get("mlflow.traceInputs"))
    outputs = _as_dict(metadata.get("mlflow.traceOutputs"))
    return info, metadata, {**inputs, **outputs}


def _run_value(run: Any, collection: str, key: str) -> Any:
    data = getattr(run, "data", None)
    values = getattr(data, collection, None) or {}
    return values.get(key)


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _to_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _trace_row(trace: Any, client: Any) -> dict[str, Any] | None:
    info, metadata, values = _trace_values(trace)
    trace_id = getattr(info, "trace_id", None) or getattr(info, "request_id", None)
    run_id = metadata.get("mlflow.sourceRun")
    try:
        run = client.get_run(run_id) if run_id else None
    except Exception:
        return None

    timestamp_ms = (
        getattr(info, "timestamp_ms", None)
        or getattr(info, "request_time", None)
        or getattr(info, "start_time_ms", None)
    )
    duration_ms = (
        getattr(info, "execution_time_ms", None)
        or getattr(info, "execution_duration", None)
    )
    judged_raw = _run_value(run, "tags", "judged") if run else None

    return {
        "trace_id": str(trace_id),
        "request_id": values.get("request_id") or _run_value(run, "tags", "request_id"),
        "timestamp": (
            datetime.fromtimestamp(float(timestamp_ms) / 1000, tz=timezone.utc)
            if timestamp_ms is not None else None
        ),
        "duration_ms": _to_int(duration_ms),
        "model_id": _run_value(run, "params", "model_id"),
        "input_tokens": _to_int(_run_value(run, "metrics", "input_tokens")),
        "output_tokens": _to_int(_run_value(run, "metrics", "output_tokens")),
        "cost_usd": _to_float(_run_value(run, "metrics", "cost_usd")),
        "bank": values.get("bank") or _run_value(run, "params", "bank"),
        "outcome": values.get("outcome") or _run_value(run, "params", "outcome"),
        "schema_valid": _to_bool(values.get("schema_valid")),
        "judged": str(judged_raw).strip().lower() if judged_raw is not None else None,
        "judge_score": _to_float(_run_value(run, "metrics", "judge.accuracy")),
        "run_id": run_id,
    }


def _search_recent_traces(client: Any, experiment_id: str, start_ms: int) -> list[Any]:
    traces: list[Any] = []
    page_token = None
    while True:
        page = client.search_traces(
            experiment_ids=[experiment_id],
            filter_string=f"timestamp_ms >= {start_ms}",
            max_results=1000,
            page_token=page_token,
            order_by=["timestamp_ms ASC"],
            include_spans=False,
        )
        traces.extend(page)
        page_token = getattr(page, "token", None)
        if not page_token:
            return traces


def main() -> int:
    args = parse_args()
    if args.hours_back <= 0:
        raise SystemExit("--hours-back must be greater than zero")
    if not args.experiment_id:
        raise SystemExit(
            "MLflow experiment ID is required; pass --experiment-id or set "
            "MLFLOW_EXPERIMENT_ID"
        )
    if not _TABLE_NAME.fullmatch(args.table):
        raise SystemExit(f"Invalid Delta table name: {args.table!r}")

    import mlflow
    from mlflow.tracking import MlflowClient
    from pyspark.sql import SparkSession
    from pyspark.sql.types import (
        BooleanType, DoubleType, LongType, StringType, StructField,
        StructType, TimestampType,
    )

    mlflow.set_tracking_uri("databricks")
    client = MlflowClient()
    spark = SparkSession.builder.getOrCreate()
    start = datetime.now(timezone.utc) - timedelta(hours=args.hours_back)
    traces = _search_recent_traces(client, str(args.experiment_id), int(start.timestamp() * 1000))

    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS {args.table} (
          trace_id STRING NOT NULL,
          request_id STRING,
          timestamp TIMESTAMP,
          duration_ms BIGINT,
          model_id STRING,
          input_tokens BIGINT,
          output_tokens BIGINT,
          cost_usd DOUBLE,
          bank STRING,
          outcome STRING,
          schema_valid BOOLEAN,
          judged STRING,
          judge_score DOUBLE,
          run_id STRING
        ) USING DELTA
    """)

    if not traces:
        print("No MLflow traces found in the requested time window; nothing to sync.")
        return 0

    rows = [row for trace in traces if (row := _trace_row(trace, client)) is not None]
    rows = [row for row in rows if row["trace_id"] not in {"None", ""}]
    schema = StructType([
        StructField("trace_id", StringType(), False),
        StructField("request_id", StringType()),
        StructField("timestamp", TimestampType()),
        StructField("duration_ms", LongType()),
        StructField("model_id", StringType()),
        StructField("input_tokens", LongType()),
        StructField("output_tokens", LongType()),
        StructField("cost_usd", DoubleType()),
        StructField("bank", StringType()),
        StructField("outcome", StringType()),
        StructField("schema_valid", BooleanType()),
        StructField("judged", StringType()),
        StructField("judge_score", DoubleType()),
        StructField("run_id", StringType()),
    ])
    source_view = "_savesage_trace_sync_source"
    spark.createDataFrame(rows, schema=schema).createOrReplaceTempView(source_view)
    spark.sql(f"""
        MERGE INTO {args.table} AS target
        USING {source_view} AS source
        ON target.trace_id = source.trace_id
        WHEN MATCHED THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
    """)
    print(f"Synced {len(rows)} MLflow trace(s) into {args.table}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
