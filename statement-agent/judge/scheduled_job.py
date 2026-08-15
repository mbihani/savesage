"""Databricks scheduled-job entry point for the post-hoc judge evaluation.

This script is the task body for a Databricks Job that runs the judge
evaluation every 6 hours. It imports :func:`run_judge_evaluation` from
:mod:`judge.scorer` and calls it with the default sample size of 10.

Job configuration (create on fevm-stable via the Databricks CLI or UI):

.. code-block:: yaml

    name: savesage-judge-evaluation
    schedule:
      quartz_cron_expression: "0 0 */6 * * ?"   # every 6 hours
      timezone_id: UTC
    tasks:
      - task_key: run-judge
        spark_python_task:
          python_file: judge/scheduled_job.py
        libraries:
          - pypi: mlflow[databricks]==3.2.0
    permissions:
      - user: "{{run_as}}"
        permission_level: CAN_MANAGE

Run it ad-hoc:

.. code-block:: bash

    python judge/scheduled_job.py --sample-size 10
"""

from __future__ import annotations

import logging
import sys

_LOGGER = logging.getLogger("statement-agent.scheduled_job")


def main() -> None:
    """Run the judge evaluation with the configured sample size."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    sample_size = 10
    # Allow CLI override for ad-hoc runs: --sample-size N
    if "--sample-size" in sys.argv:
        idx = sys.argv.index("--sample-size")
        if idx + 1 < len(sys.argv):
            sample_size = int(sys.argv[idx + 1])

    _LOGGER.info("starting judge evaluation (sample_size=%d)", sample_size)

    from judge.scorer import run_judge_evaluation

    result = run_judge_evaluation(sample_size=sample_size)

    _LOGGER.info(
        "judge evaluation complete: %d judged, %d errors, strict=%s, forgiven=%s",
        result.get("count_judged", 0),
        result.get("count_errors", 0),
        result.get("overall_strict"),
        result.get("overall_narration_forgiven"),
    )
    # Print the JSON summary so the job log captures it.
    import json
    print(json.dumps(result, default=str, indent=2))


if __name__ == "__main__":
    main()
