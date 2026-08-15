"""Best-effort telemetry: no MLflow call may ever break the parse path.

This is the single chokepoint enforcing workstream 4 requirement 6: if MLflow is
unreachable, misconfigured, or its API shape differs, telemetry failure degrades
to a logged warning and never propagates to the caller. The parse, persistence,
and result return must always succeed.
"""

import logging
from typing import Any, Callable

_LOGGER = logging.getLogger("statement-agent.tracing")


def best_effort(action: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run ``fn(*args, **kwargs)``; on ANY exception, log a warning and return None.

    ``action`` is a short label used in the warning so failures are attributable.
    Returns whatever ``fn`` returns, or None on failure.
    """
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - telemetry must never raise
        _LOGGER.warning("telemetry best-effort swallow [%s]: %s", action, exc)
        return None
