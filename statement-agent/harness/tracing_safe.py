"""Best-effort telemetry: no telemetry operation may ever break the parse path.

This is the single chokepoint enforcing workstream 4 requirement 6: if MLflow is
unreachable, misconfigured, its API shape differs, OR a payload-construction bug
raises, telemetry failure degrades to a logged warning and never propagates to
the caller. The parse, persistence, and result return must always succeed.

BaseException decision (explicit, per review B1):

We catch ``BaseException`` (not just ``Exception``) so that a payload bug, a
``RecursionError`` from a cycle, a ``MemoryError`` under pressure, or any other
non-control failure is swallowed and never kills a customer's parse. We RE-RAISE
the three genuine process-control exceptions — ``KeyboardInterrupt``,
``SystemExit``, ``GeneratorExit`` — because those are operator/process intent
(Ctrl-C, ``sys.exit()``, generator close), not telemetry bugs, and swallowing
them would hide a user's stop request.

This helper is the INNER boundary for individual mlflow calls. The OUTER boundary
(``MLflowTraceSink._guard``) wraps each entire public method and additionally
*disables* telemetry after a hard non-control failure so a recurring bug does not
spam warnings on every subsequent request.
"""

import logging
from typing import Any, Callable

_LOGGER = logging.getLogger("statement-agent.tracing")

# Exceptions that represent operator/process intent, not telemetry bugs. These
# ALWAYS propagate and are never swallowed.
_CONTROL_EXCEPTIONS = (KeyboardInterrupt, SystemExit, GeneratorExit)


def best_effort(action: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run ``fn(*args, **kwargs)``; on non-control failure, log a warning and return None.

    Catches ``BaseException`` (so payload/RecursionError/MemoryError bugs cannot
    break the parse) but RE-RAISES ``KeyboardInterrupt`` / ``SystemExit`` /
    ``GeneratorExit`` (genuine process control). ``action`` is a short label used
    in the warning so failures are attributable.
    """
    try:
        return fn(*args, **kwargs)
    except _CONTROL_EXCEPTIONS:
        raise  # never swallow operator/process intent
    except BaseException as exc:  # noqa: BLE001 - telemetry must never break the parse
        _LOGGER.warning("telemetry best-effort swallow [%s]: %s", action, exc)
        return None
