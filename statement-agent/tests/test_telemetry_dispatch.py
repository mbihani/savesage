"""Unit tests for the single-consumer telemetry dispatcher.

The dispatcher is the mechanism that keeps MLflow off the parse graph thread:
producers enqueue with a NON-BLOCKING put (dropping if full) and a single serial
daemon consumer drains the queue. These tests pin the two properties the parse
path depends on — the producer never blocks, and there is exactly one consumer
thread regardless of load.
"""

import threading
import time
import unittest

from harness.telemetry_dispatch import TelemetryDispatcher, get_dispatcher


class TelemetryDispatcherTest(unittest.TestCase):
    def test_tasks_run_serially_on_one_consumer_thread(self):
        disp = TelemetryDispatcher(maxsize=16)
        seen_threads = set()
        order = []

        def _task(i):
            seen_threads.add(threading.current_thread().name)
            order.append(i)

        for i in range(5):
            self.assertTrue(disp.submit(_task, i))
        self.assertTrue(disp.join(timeout=5.0))
        self.assertEqual(order, [0, 1, 2, 3, 4])          # serial, in order
        self.assertEqual(seen_threads, {"telemetry-consumer"})  # ONE thread
        disp.stop()

    def test_submit_never_blocks_and_drops_when_full(self):
        # A consumer stuck on the first task must not make submit() block; once
        # the bounded queue fills, further submits are dropped (not queued).
        disp = TelemetryDispatcher(maxsize=2)
        gate = threading.Event()
        disp.submit(lambda: gate.wait())  # occupies the consumer

        # Fill the 2 queue slots, then over-submit — none of these block.
        results = []
        start = time.monotonic()
        for _ in range(10):
            results.append(disp.submit(lambda: None))
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, 1.0, "submit blocked — must be non-blocking")
        self.assertIn(True, results)   # some enqueued
        self.assertIn(False, results)  # some dropped (queue full)
        self.assertGreaterEqual(disp.dropped, 1)
        gate.set()
        disp.stop()

    def test_one_failing_task_does_not_kill_the_consumer(self):
        disp = TelemetryDispatcher(maxsize=8)
        ran = []
        disp.submit(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        disp.submit(lambda: ran.append("after"))
        self.assertTrue(disp.join(timeout=5.0))
        self.assertEqual(ran, ["after"])  # consumer survived the exception
        disp.stop()

    def test_get_dispatcher_is_a_singleton(self):
        self.assertIs(get_dispatcher(), get_dispatcher())


if __name__ == "__main__":
    unittest.main()
