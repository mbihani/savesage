#!/usr/bin/env python3
"""Run the post-hoc judge over a sample of recent, unjudged MLflow traces."""

from __future__ import annotations

import argparse
import json
import os


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sample-size",
        type=int,
        default=10,
        help="Maximum number of unjudged traces to sample (default: 10)",
    )
    parser.add_argument(
        "--experiment-id",
        default=os.getenv("MLFLOW_EXPERIMENT_ID"),
        help="MLflow experiment ID (default: MLFLOW_EXPERIMENT_ID)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.sample_size < 1:
        raise SystemExit("--sample-size must be at least 1")
    if not args.experiment_id:
        raise SystemExit(
            "MLflow experiment ID is required; pass --experiment-id or set "
            "MLFLOW_EXPERIMENT_ID"
        )

    # Imports are local so --help and static checks do not require cluster packages.
    import mlflow

    # run_judge_evaluation owns sampling, artifact download, Opus invocation,
    # comparison, metric logging, and the per-trace error boundary. Importing
    # verdict_to_metrics here also pins this job to the canonical metric mapping.
    from harness.tracing_judge import verdict_to_metrics  # noqa: F401
    from judge.scorer import run_judge_evaluation

    mlflow.set_tracking_uri("databricks")
    os.environ["MLFLOW_EXPERIMENT_ID"] = str(args.experiment_id)

    summary = run_judge_evaluation(sample_size=args.sample_size)
    print(json.dumps(summary, indent=2, default=str))

    if summary.get("count_judged", 0) == 0 and not summary.get("errors"):
        print("No unjudged traces found; nothing to do.")
        return 0
    if summary.get("errors"):
        print(
            f"Judge completed with {len(summary['errors'])} trace error(s); "
            "other traces were processed successfully."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
