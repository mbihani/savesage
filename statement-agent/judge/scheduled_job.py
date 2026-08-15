"""Databricks scheduled-job entry point for the post-hoc judge evaluation.

This script is the task body for a Databricks Job that runs the judge
evaluation every 6 hours. It imports :func:`run_judge_evaluation` from
:mod:`judge.scorer` and calls it with the default sample size of 10.

**Python path configuration.**  When this script runs as a Databricks Job
``spark_python_task``, the task's working directory is the repo root, NOT
``statement-agent/``. The import ``from judge.scorer import ...`` therefore
fails unless ``statement-agent/`` is on ``sys.path``. There are two ways to
ensure this:

1. **DAB ``spark_python_task.source``** (preferred): set ``source: GIT`` in
   the job task and set ``python_file`` to
   ``statement-agent/judge/scheduled_job.py`` — Databricks adds the repo
   root to ``sys.path`` automatically, so the ``_ensure_path()`` call below
   is a no-op safety net.
2. **Manual ``sys.path`` insertion** (fallback for ad-hoc runs): the
   ``_ensure_path()`` helper below walks up from this file to find the
   ``statement-agent/`` directory and inserts it at ``sys.path[0]``.

Job configuration (create on fevm-stable via the Databricks CLI or UI):

.. code-block:: yaml

    name: savesage-judge-evaluation
    schedule:
      quartz_cron_expression: "0 0 */6 * * ?"   # every 6 hours
      timezone_id: UTC
    tasks:
      - task_key: run-judge
        spark_python_task:
          python_file: statement-agent/judge/scheduled_job.py
          source: GIT
          libraries:
            - pypi: mlflow[databricks]==3.2.0
    permissions:
      - user: "{{run_as}}"
        permission_level: CAN_MANAGE

Run it ad-hoc:

.. code-block:: bash

    cd statement-agent && python judge/scheduled_job.py --sample-size 10
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_LOGGER = logging.getLogger("statement-agent.scheduled_job")


def _ensure_path() -> None:
    """Ensure ``statement-agent/`` is on ``sys.path`` so ``judge.*`` resolves.

    Walks up from this file to find the nearest directory containing a
    ``judge/`` subpackage and inserts it at ``sys.path[0]``. This is a safety
    net for ad-hoc runs where the caller's CWD may not be ``statement-agent/``.
    """
    here = Path(__file__).resolve().parent  # judge/
    parent = here.parent                    # statement-agent/
    parent_str = str(parent)
    if parent_str not in sys.path:
        sys.path.insert(0, parent_str)


def main() -> None:
    """Run the judge evaluation with the configured sample size."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # Ensure statement-agent/ is importable when running as a Databricks Job
    # (CWD is the repo root, not statement-agent/).
    _ensure_path()

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
