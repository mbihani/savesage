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
import threading
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


def call_bounded(
    action: str, timeout_seconds: float, fn: Callable[..., Any], *args: Any, **kwargs: Any
) -> Any:
    """Run ``fn`` in a daemon worker thread and RETURN WITHIN ``timeout_seconds``.

    This is the hard time bound the production hang taught us telemetry needs:
    a synchronous MLflow / tracking-server call with no timeout froze the graph
    thread forever. Here the call runs off the caller's thread; if it does not
    finish within ``timeout_seconds`` the caller stops waiting, logs a WARNING,
    and returns ``None`` — the orphaned worker thread is abandoned (Python cannot
    kill a thread) but it can no longer stall the caller.

    Failures are swallowed like :func:`best_effort` (telemetry must never break
    the caller). Because the work runs on a separate thread, an in-worker control
    exception cannot cross back to the caller, so it is logged rather than
    re-raised — the caller's own Ctrl-C handling is unaffected.

    When ``timeout_seconds`` is falsy (``None``/``<= 0``) the call runs inline via
    :func:`best_effort` (no worker thread, no bound) — used by tests/paths that
    explicitly disable the bound.
    """
    if not timeout_seconds or timeout_seconds <= 0:
        return best_effort(action, fn, *args, **kwargs)

    box: dict[str, Any] = {}
    done = threading.Event()

    def _runner() -> None:
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - worker thread swallows everything
            box["error"] = exc
        finally:
            done.set()

    worker = threading.Thread(target=_runner, name=f"telemetry-{action}"[:80], daemon=True)
    worker.start()
    if not done.wait(timeout_seconds):
        _LOGGER.warning(
            "telemetry op [%s] exceeded %.1fs; abandoning worker, caller continues",
            action, timeout_seconds,
        )
        return None
    err = box.get("error")
    if err is not None:
        _LOGGER.warning("telemetry best-effort swallow [%s]: %s", action, err)
        return None
    return box.get("value")
