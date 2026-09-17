"""Single background telemetry consumer + bounded queue.

The parse graph thread must NEVER block on MLflow — the production hang was a
synchronous ``mlflow.start_run()`` on the graph thread inside the ROUTE trace
callback. This dispatcher decouples telemetry from the parse entirely:

* **Producers** (the per-request :class:`app.main._ProgressTraceSink`) ENQUEUE a
  telemetry task with a NON-BLOCKING put — dropping it if the queue is full —
  and return immediately. A producer never waits on MLflow.
* **ONE** long-lived daemon consumer thread drains the queue serially and
  performs ALL MLflow work (create_run, log params/metrics/tags, span flush,
  trace linkage, set_terminated, artifact upload).

Why a single serial consumer (vs. round-1's thread-per-op + degrade latch):

* **No thread accumulation.** Exactly one consumer thread for the whole
  process, regardless of load or MLflow health. Round 1 spawned a fresh daemon
  worker for every MLflow op and abandoned it on timeout; under a persistent
  MLflow stall those leaked without bound.
* **The join key can't be corrupted.** The full MLflow run lifecycle for a
  request (``create_run`` → log → link trace via SOURCE_RUN → ``set_terminated``)
  runs IN ORDER on the SAME thread, so a run is created exactly once and always
  terminated in the same drain sequence. There is no late/async ``create_run``
  landing after the request finished, so no duplicate or orphaned RUNNING run
  and no ambiguous ``request_id`` → run mapping for the judge scorer to trip on.

Why the consumer can't stall a parse or wedge forever:

* Producers never wait on the consumer (non-blocking put), so a slow or stuck
  MLflow call only delays TELEMETRY — never a parse.
* Individual MLflow REST calls are bounded by ``MLFLOW_HTTP_REQUEST_TIMEOUT``
  (set in :func:`harness.tracing.configure_tracing`), so a hung tracking-server
  call is abandoned at the transport layer and the consumer moves on to the next
  task. A single transient bad call therefore self-heals — the next task runs.
* If the queue overflows while the consumer is busy, tasks are DROPPED (and
  counted/logged), never queued unboundedly. Telemetry is best-effort: a parse
  that loses its run/trace to a drop simply is not judged later — acceptable.

The consumer catches ``BaseException`` around each task so one failing task can
never kill the consumer thread; control exceptions (KeyboardInterrupt / etc.)
are handled by :func:`harness.tracing_safe.best_effort`.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable

from .tracing_safe import best_effort

_LOGGER = logging.getLogger("statement-agent.tracing")

# Default bound on the number of pending telemetry tasks. Bounds memory (a task
# may retain the source PDF bytes) so a stalled consumer cannot grow the queue
# without limit — excess tasks are dropped. CONFIGURE(ws4-telemetry-queue)
_DEFAULT_MAXSIZE = 256

# Rate-limit the "queue full, dropping" WARNING so a sustained stall does not
# spam the log on every dropped task.
_DROP_LOG_INTERVAL_SECONDS = 10.0

# Sentinel enqueued by ``stop()`` to unblock and end the consumer (tests only).
_STOP = object()


class TelemetryDispatcher:
    """Bounded queue + single daemon consumer for best-effort MLflow telemetry.

    Thread-safe. ``submit`` is called from the parse graph thread(s);
    ``_consume`` runs on the single owned daemon thread. The consumer is started
    lazily on the first ``submit`` so importing this module (and constructing the
    dispatcher) never spawns a thread.
    """

    def __init__(self, maxsize: int = _DEFAULT_MAXSIZE) -> None:
        # A non-positive maxsize would mean an UNBOUNDED queue (queue.Queue
        # semantics) — the opposite of what we want — so clamp to the default.
        self._queue: "queue.Queue[Any]" = queue.Queue(
            maxsize=maxsize if maxsize and maxsize > 0 else _DEFAULT_MAXSIZE
        )
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._dropped = 0
        self._last_drop_log = 0.0

    def _ensure_consumer(self) -> None:
        """Start the single consumer thread if it is not already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(
                target=self._consume, name="telemetry-consumer", daemon=True,
            )
            self._thread.start()

    def submit(self, fn: Callable[..., Any], *args: Any) -> bool:
        """Enqueue ``fn(*args)`` for the consumer; NON-BLOCKING, drop if full.

        Returns True if the task was enqueued, False if it was dropped because
        the queue was full. NEVER blocks and NEVER raises — the parse graph
        thread calls this and must proceed instantly regardless of MLflow health.
        """
        self._ensure_consumer()
        try:
            self._queue.put_nowait((fn, args))
            return True
        except queue.Full:
            self._note_drop()
            return False
        except BaseException as exc:  # noqa: BLE001 - enqueue must never break the parse
            _LOGGER.warning("telemetry enqueue failed: %s", exc)
            return False

    def _note_drop(self) -> None:
        self._dropped += 1
        now = time.monotonic()
        if now - self._last_drop_log >= _DROP_LOG_INTERVAL_SECONDS:
            self._last_drop_log = now
            _LOGGER.warning(
                "telemetry queue full; dropping task (%d dropped so far). "
                "Telemetry is best-effort — the affected parse is unaffected but "
                "may not be judged later.", self._dropped,
            )

    @property
    def dropped(self) -> int:
        """Total tasks dropped due to a full queue (monitoring / tests)."""
        return self._dropped

    def pending(self) -> int:
        """Approximate number of tasks not yet drained (monitoring / tests)."""
        return self._queue.qsize()

    def _consume(self) -> None:
        """Drain the queue forever, running each task under ``best_effort``."""
        while True:
            task = self._queue.get()
            try:
                if task is _STOP:
                    return
                fn, args = task
                # best_effort bounds each task against exceptions; a hung MLflow
                # REST call is bounded by MLFLOW_HTTP_REQUEST_TIMEOUT (transport).
                best_effort("telemetry.dispatch", fn, *args)
            except BaseException as exc:  # noqa: BLE001 - consumer must never die
                _LOGGER.warning("telemetry consumer task error: %s", exc)
            finally:
                self._queue.task_done()

    # --- test / shutdown helpers ---------------------------------------
    def join(self, timeout: float | None = None) -> bool:
        """Block until the queue is drained (or ``timeout`` elapses).

        Returns True if fully drained. Test-only: production never joins — the
        consumer runs for the process lifetime.
        """
        if timeout is None:
            self._queue.join()
            return True
        # queue.Queue.join has no timeout; poll unfinished_tasks instead.
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.01)
        return self._queue.unfinished_tasks == 0

    def stop(self, timeout: float = 2.0) -> None:
        """Signal the consumer to exit and wait briefly (test-only)."""
        if self._thread is None:
            return
        try:
            self._queue.put_nowait(_STOP)
        except queue.Full:
            # Drain one slot so the sentinel fits, then retry once.
            try:
                self._queue.get_nowait()
                self._queue.task_done()
                self._queue.put_nowait(_STOP)
            except BaseException:  # noqa: BLE001
                return
        self._thread.join(timeout)


# --- process-global singleton -----------------------------------------------
_DISPATCHER: TelemetryDispatcher | None = None
_DISPATCHER_LOCK = threading.Lock()


def get_dispatcher() -> TelemetryDispatcher:
    """Return the process-wide telemetry dispatcher (lazily constructed)."""
    global _DISPATCHER
    if _DISPATCHER is not None:
        return _DISPATCHER
    with _DISPATCHER_LOCK:
        if _DISPATCHER is None:
            maxsize = _resolve_maxsize()
            _DISPATCHER = TelemetryDispatcher(maxsize=maxsize)
    return _DISPATCHER


def _resolve_maxsize() -> int:
    """Read the queue bound from the tracing config (fail-safe → default)."""
    try:
        from .config_ws4 import get_tracing_config

        return int(get_tracing_config().telemetry_queue_maxsize)
    except BaseException:  # noqa: BLE001 - a config problem must not break telemetry
        return _DEFAULT_MAXSIZE
