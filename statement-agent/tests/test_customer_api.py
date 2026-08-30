"""Stdlib-only tests for the customer-deployable surface (workstream 7).

Covers:
* ``/api/v1/parse`` input validation + response building (pure helpers, no
  FastAPI/TestClient required — the contract-test gate imports these without
  fastapi/httpx installed).
* The background judge scheduler: env-var resolution, disable-on-zero,
  daemon-thread lifecycle, and the shared-slot skip path.

These mirror the discipline of ``test_app_ws6``: the route handlers are thin
wrappers over pure module-level helpers, so the logic is unit-testable in a
stdlib-only environment.
"""

import os
import unittest
from unittest.mock import patch

from app.main import (
    MAX_SAMPLE_SIZE,
    RequestContext,
    _build_v1_response,
    _judge_interval_hours,
    _judge_sample_size_env,
    _JudgeScheduler,
    _summarize_judge_result,
    _validate_v1_pdf,
)
from harness.dbfs import validate_bank_name


# ---------------------------------------------------------------------------
# /api/v1/parse — PDF validation
# ---------------------------------------------------------------------------

class V1PdfValidationTest(unittest.TestCase):
    def test_valid_pdf_passes(self) -> None:
        # %PDF magic bytes prefix — the rest of the body is irrelevant.
        _validate_v1_pdf(b"%PDF-1.4\n%binary junk...")
        _validate_v1_pdf(b"%PDF-1.7")

    def test_empty_bytes_rejected(self) -> None:
        with self.assertRaises(ValueError) as cm:
            _validate_v1_pdf(b"")
        self.assertIn("empty", str(cm.exception))

    def test_non_pdf_rejected(self) -> None:
        with self.assertRaises(ValueError) as cm:
            _validate_v1_pdf(b"Not a PDF -- just text")
        self.assertIn("%PDF", str(cm.exception))
        self.assertIn("magic bytes", str(cm.exception))

    def test_pdf_magic_must_be_at_start(self) -> None:
        # Leading whitespace before %PDF is not a valid PDF upload.
        with self.assertRaises(ValueError):
            _validate_v1_pdf(b" %PDF-1.4")

    def test_does_not_raise_on_valid(self) -> None:
        # A large-ish valid-looking PDF header passes the cheap check.
        _validate_v1_pdf(b"%PDF-1.4" + b"\x00" * 1024)


# ---------------------------------------------------------------------------
# /api/v1/parse — response building
# ---------------------------------------------------------------------------

class V1ResponseBuilderTest(unittest.TestCase):
    """_build_v1_response maps a completed RequestContext to (status, body)."""

    @staticmethod
    def _ctx(request_id: str) -> RequestContext:
        ctx = RequestContext(request_id)
        return ctx

    def test_success_returns_200_with_extraction(self) -> None:
        ctx = self._ctx("req-success0001")
        ctx.extraction_data = {
            "payload": {"cards": [], "transactions": []},
            "model_id": "databricks-gpt-5-6-luna",
            "schema_valid": True,
        }
        ctx.complete_data = {
            "request_id": "req-success0001",
            "outcome": "SUCCESS",
            "stage": "FINALIZE",
            "schema_valid": True,
            "validation_errors": [],
        }
        ctx.outcome = "SUCCESS"
        status, body = _build_v1_response(ctx, "req-success0001", "HDFC")
        self.assertEqual(status, 200)
        self.assertEqual(body["request_id"], "req-success0001")
        self.assertEqual(body["bank"], "HDFC")
        self.assertEqual(body["status"], "SUCCESS")
        self.assertEqual(body["verdict"], None)
        ext = body["extraction"]
        self.assertEqual(ext["model_id"], "databricks-gpt-5-6-luna")
        self.assertTrue(ext["schema_valid"])
        self.assertEqual(ext["validation_errors"], [])
        self.assertEqual(ext["payload"], {"cards": [], "transactions": []})

    def test_partial_is_still_200_with_validation_errors(self) -> None:
        ctx = self._ctx("req-partial0001")
        ctx.extraction_data = {
            "payload": {"cards": [{"x": 1}]},
            "model_id": "databricks-gpt-5-6-luna",
            "schema_valid": False,
        }
        ctx.complete_data = {"validation_errors": ["missing field X"]}
        ctx.outcome = "PARTIAL"
        status, body = _build_v1_response(ctx, "req-partial0001", "ICICI")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "PARTIAL")
        self.assertFalse(body["extraction"]["schema_valid"])
        self.assertEqual(body["extraction"]["validation_errors"], ["missing field X"])

    def test_error_returns_422(self) -> None:
        ctx = self._ctx("req-error000001")
        ctx.error = "Luna endpoint timed out"
        ctx.outcome = None
        status, body = _build_v1_response(ctx, "req-error000001", "HDFC")
        self.assertEqual(status, 422)
        self.assertEqual(body["status"], "EXTRACTION_FAILED")
        self.assertIsNone(body["extraction"])
        self.assertEqual(body["error"], "Luna endpoint timed out")
        self.assertIsNone(body["verdict"])

    def test_no_extraction_returns_422(self) -> None:
        ctx = self._ctx("req-noextr0001")
        # No error set, but no extraction_data either.
        status, body = _build_v1_response(ctx, "req-noextr0001", "SBI")
        self.assertEqual(status, 422)
        self.assertEqual(body["error"], "extraction produced no result")

    def test_extraction_failed_outcome_returns_422_even_with_data(self) -> None:
        ctx = self._ctx("req-extfail01")
        ctx.extraction_data = {
            "payload": {}, "model_id": "m", "schema_valid": False,
        }
        ctx.outcome = "EXTRACTION_FAILED"
        status, body = _build_v1_response(ctx, "req-extfail01", "HDFC")
        self.assertEqual(status, 422)
        self.assertEqual(body["status"], "EXTRACTION_FAILED")

    def test_missing_complete_data_yields_empty_validation_errors(self) -> None:
        ctx = self._ctx("req-nocompl01")
        ctx.extraction_data = {"payload": {}, "model_id": "m", "schema_valid": True}
        ctx.outcome = "SUCCESS"
        ctx.complete_data = None
        status, body = _build_v1_response(ctx, "req-nocompl01", "HDFC")
        self.assertEqual(status, 200)
        self.assertEqual(body["extraction"]["validation_errors"], [])

    def test_outcome_none_defaults_to_success_status(self) -> None:
        ctx = self._ctx("req-nostatus1")
        ctx.extraction_data = {"payload": {}, "model_id": "m", "schema_valid": True}
        ctx.outcome = None
        status, body = _build_v1_response(ctx, "req-nostatus1", "HDFC")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "SUCCESS")


# ---------------------------------------------------------------------------
# /api/v1/parse — bank-name validation (format gate used by the route)
# ---------------------------------------------------------------------------

class V1BankValidationTest(unittest.TestCase):
    def test_valid_builtin_banks_pass(self) -> None:
        for name in ("HDFC", "ICICI", "SBI", "AXIS"):
            self.assertEqual(validate_bank_name(name), name)

    def test_lowercase_is_upper_cased(self) -> None:
        self.assertEqual(validate_bank_name("hdfc"), "HDFC")

    def test_unknown_but_well_formed_passes(self) -> None:
        # validate_bank_name checks FORMAT, not existence; unknown banks fall
        # back to GENERIC inside the graph, not at the validation gate.
        self.assertEqual(validate_bank_name("KOTAK"), "KOTAK")

    def test_empty_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_bank_name("")

    def test_special_chars_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_bank_name("HDFC/../../etc")

    def test_path_traversal_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_bank_name("..")


# ---------------------------------------------------------------------------
# Judge scheduler — env-var resolution
# ---------------------------------------------------------------------------

class JudgeSchedulerConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        # Snapshot env so each test starts from a clean slate.
        self._saved_interval = os.environ.pop("JUDGE_INTERVAL_HOURS", None)
        self._saved_sample = os.environ.pop("JUDGE_SAMPLE_SIZE", None)

    def tearDown(self) -> None:
        for k, v in (("JUDGE_INTERVAL_HOURS", self._saved_interval),
                     ("JUDGE_SAMPLE_SIZE", self._saved_sample)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_default_interval_is_6(self) -> None:
        self.assertEqual(_judge_interval_hours(), 6.0)

    def test_default_sample_is_10(self) -> None:
        self.assertEqual(_judge_sample_size_env(), 10)

    def test_interval_zero_disables(self) -> None:
        os.environ["JUDGE_INTERVAL_HOURS"] = "0"
        self.assertEqual(_judge_interval_hours(), 0.0)

    def test_interval_negative_disables(self) -> None:
        os.environ["JUDGE_INTERVAL_HOURS"] = "-1"
        self.assertEqual(_judge_interval_hours(), -1.0)

    def test_interval_override(self) -> None:
        os.environ["JUDGE_INTERVAL_HOURS"] = "12"
        self.assertEqual(_judge_interval_hours(), 12.0)

    def test_interval_garbage_falls_back_to_default(self) -> None:
        os.environ["JUDGE_INTERVAL_HOURS"] = "every-six-hours"
        self.assertEqual(_judge_interval_hours(), 6.0)

    def test_sample_override(self) -> None:
        os.environ["JUDGE_SAMPLE_SIZE"] = "25"
        self.assertEqual(_judge_sample_size_env(), 25)

    def test_sample_capped_at_max(self) -> None:
        os.environ["JUDGE_SAMPLE_SIZE"] = str(MAX_SAMPLE_SIZE + 100)
        self.assertEqual(_judge_sample_size_env(), MAX_SAMPLE_SIZE)

    def test_sample_min_is_one(self) -> None:
        os.environ["JUDGE_SAMPLE_SIZE"] = "0"
        self.assertEqual(_judge_sample_size_env(), 1)
        os.environ["JUDGE_SAMPLE_SIZE"] = "-5"
        self.assertEqual(_judge_sample_size_env(), 1)

    def test_sample_garbage_falls_back_to_default(self) -> None:
        os.environ["JUDGE_SAMPLE_SIZE"] = "ten"
        self.assertEqual(_judge_sample_size_env(), 10)


# ---------------------------------------------------------------------------
# Judge scheduler — lifecycle + tick
# ---------------------------------------------------------------------------

class JudgeSchedulerTest(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_interval = os.environ.pop("JUDGE_INTERVAL_HOURS", None)
        self._saved_sample = os.environ.pop("JUDGE_SAMPLE_SIZE", None)

    def tearDown(self) -> None:
        for k, v in (("JUDGE_INTERVAL_HOURS", self._saved_interval),
                     ("JUDGE_SAMPLE_SIZE", self._saved_sample)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_disabled_when_interval_zero(self) -> None:
        """JUDGE_INTERVAL_HOURS=0 → start() is a no-op, no thread, inactive."""
        sched = _JudgeScheduler(0.0, 10)
        sched.start()
        self.assertFalse(sched.active)
        self.assertIsNone(sched._thread)
        self.assertIsNone(sched.next_run_at)
        status = sched.status()
        self.assertFalse(status["active"])
        self.assertEqual(status["interval_hours"], 0.0)

    def test_disabled_when_interval_negative(self) -> None:
        sched = _JudgeScheduler(-3.0, 10)
        sched.start()
        self.assertFalse(sched.active)
        self.assertIsNone(sched._thread)

    def test_enabled_starts_daemon_thread_and_schedules_next(self) -> None:
        # A huge interval so the loop's first _stop.wait() never elapses during
        # the test (no real judge tick / mlflow import happens).
        sched = _JudgeScheduler(1000.0, 7)
        sched.start()
        try:
            self.assertTrue(sched.active)
            self.assertIsNotNone(sched._thread)
            self.assertTrue(sched._thread.daemon)
            self.assertIsNotNone(sched.next_run_at)
            self.assertEqual(sched.sample_size, 7)
        finally:
            sched.stop()
            sched._thread.join(timeout=2.0)
        self.assertFalse(sched.active)

    def test_stop_signals_loop_to_exit(self) -> None:
        sched = _JudgeScheduler(1000.0, 5)
        sched.start()
        self.assertTrue(sched._thread.is_alive())
        sched.stop()
        sched._thread.join(timeout=2.0)
        self.assertFalse(sched._thread.is_alive())

    def test_tick_skips_when_judge_slot_busy(self) -> None:
        """When the shared slot is busy, _tick skips without running the judge."""
        sched = _JudgeScheduler(1.0, 10)
        with patch("app.main._acquire_judge_slot", return_value=False) as acq, \
             patch("app.main._run_judge_evaluation_bg") as run_bg:
            sched._tick()
            acq.assert_called_once()
            run_bg.assert_not_called()
        self.assertEqual(sched.last_summary["status"], "skipped")
        self.assertEqual(sched.last_summary["reason"], "judge already running")

    def test_tick_runs_judge_and_summarises_when_slot_free(self) -> None:
        sched = _JudgeScheduler(1.0, 12)
        fake_result = {
            "count_judged": 4, "count_errors": 1,
            "overall_strict": 0.88, "overall_narration_forgiven": 0.92,
            "per_field": {}, "per_bank": {}, "eval_run_id": "r-123",
        }
        with patch("app.main._acquire_judge_slot", return_value=True), \
             patch("app.main._run_judge_evaluation_bg") as run_bg, \
             patch("app.main._judge_result_cache", fake_result):
            sched._tick()
            run_bg.assert_called_once_with(12)
        self.assertEqual(sched.last_summary["count_judged"], 4)
        self.assertEqual(sched.last_summary["count_errors"], 1)
        self.assertEqual(sched.last_summary["overall_strict"], 0.88)
        self.assertEqual(sched.last_summary["eval_run_id"], "r-123")

    def test_tick_release_slot_is_delegated_to_runner(self) -> None:
        """_tick acquires the slot; _run_judge_evaluation_bg releases it (no
        double-release, no leak). We assert the scheduler does NOT release."""
        sched = _JudgeScheduler(1.0, 5)
        with patch("app.main._acquire_judge_slot", return_value=True), \
             patch("app.main._run_judge_evaluation_bg"), \
             patch("app.main._release_judge_slot") as rel:
            sched._tick()
            rel.assert_not_called()  # the runner owns the release

    def test_status_shape(self) -> None:
        sched = _JudgeScheduler(6.0, 10)
        status = sched.status()
        self.assertEqual(set(status.keys()), {
            "active", "interval_hours", "sample_size",
            "last_run_at", "next_run_at", "last_summary",
        })
        self.assertEqual(status["interval_hours"], 6.0)
        self.assertEqual(status["sample_size"], 10)
        self.assertIsNone(status["last_run_at"])
        self.assertIsNone(status["next_run_at"])
        self.assertIsNone(status["last_summary"])
        self.assertFalse(status["active"])


class JudgeResultSummaryTest(unittest.TestCase):
    def test_full_result_summarised(self) -> None:
        result = {
            "count_judged": 7, "count_errors": 2,
            "overall_strict": 0.9, "overall_narration_forgiven": 0.95,
            "per_field": {"x": 1}, "per_bank": {"HDFC": 2},
            "eval_run_id": "r-9", "_status": "done",
        }
        summary = _summarize_judge_result(result)
        self.assertEqual(summary["count_judged"], 7)
        self.assertEqual(summary["count_errors"], 2)
        self.assertEqual(summary["overall_strict"], 0.9)
        self.assertEqual(summary["eval_run_id"], "r-9")
        self.assertEqual(summary["_status"], "done")
        # The large per-field/per-bank maps are NOT carried into the summary.
        self.assertNotIn("per_field", summary)
        self.assertNotIn("per_bank", summary)

    def test_non_dict_result(self) -> None:
        self.assertEqual(_summarize_judge_result(None), {"status": "unknown"})

    def test_missing_keys_default_gracefully(self) -> None:
        summary = _summarize_judge_result({})
        self.assertEqual(summary["count_judged"], 0)
        self.assertEqual(summary["count_errors"], 0)
        self.assertIsNone(summary["overall_strict"])
        self.assertEqual(summary["_status"], "done")


# ---------------------------------------------------------------------------
# _start_judge_scheduler — the create_app() entry point
# ---------------------------------------------------------------------------

class StartJudgeSchedulerTest(unittest.TestCase):
    def setUp(self) -> None:
        import app.main as main_mod
        self._main = main_mod
        self._saved_scheduler = main_mod._judge_scheduler
        self._saved_interval = os.environ.pop("JUDGE_INTERVAL_HOURS", None)
        self._saved_sample = os.environ.pop("JUDGE_SAMPLE_SIZE", None)

    def tearDown(self) -> None:
        # Stop any scheduler we may have started and restore the global.
        sched = self._main._judge_scheduler
        if sched is not None and sched.active:
            sched.stop()
        self._main._judge_scheduler = self._saved_scheduler
        for k, v in (("JUDGE_INTERVAL_HOURS", self._saved_interval),
                     ("JUDGE_SAMPLE_SIZE", self._saved_sample)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_disabled_when_interval_zero(self) -> None:
        os.environ["JUDGE_INTERVAL_HOURS"] = "0"
        self._main._start_judge_scheduler()
        sched = self._main._judge_scheduler
        self.assertIsNotNone(sched)
        self.assertFalse(sched.active)
        self.assertIsNone(sched._thread)

    def test_enabled_starts_active_scheduler(self) -> None:
        # Huge interval so the loop never ticks during the test.
        os.environ["JUDGE_INTERVAL_HOURS"] = "1000"
        os.environ["JUDGE_SAMPLE_SIZE"] = "3"
        self._main._start_judge_scheduler()
        sched = self._main._judge_scheduler
        self.assertIsNotNone(sched)
        self.assertTrue(sched.active)
        self.assertEqual(sched.sample_size, 3)
        self.assertIsNotNone(sched._thread)


if __name__ == "__main__":
    unittest.main()
