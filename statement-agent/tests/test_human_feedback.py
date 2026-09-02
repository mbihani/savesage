"""Stdlib-only coverage for best-effort MLflow human feedback logging."""

import sys
import types
import unittest
from unittest.mock import patch

from app.main import _log_human_feedback


class HumanFeedbackTest(unittest.TestCase):
    def _modules(self, *, run_id="run-1", trace_id="tr-1", fail=False):
        calls = []
        mlflow = types.ModuleType("mlflow")

        def log_feedback(**kwargs):
            if fail:
                raise RuntimeError("tracking store unavailable")
            calls.append(kwargs)

        mlflow.log_feedback = log_feedback
        entities = types.ModuleType("mlflow.entities")

        class AssessmentSource:
            def __init__(self, source_type, source_id):
                self.source_type = source_type
                self.source_id = source_id

        entities.AssessmentSource = AssessmentSource
        scorer = types.ModuleType("judge.scorer")
        scorer.resolve_run_id = lambda request_id: run_id
        trace = types.SimpleNamespace(
            info=types.SimpleNamespace(trace_id=trace_id) if trace_id else None,
        )
        scorer._resolve_trace_for_run = lambda resolved_run_id: trace
        return calls, {
            "mlflow": mlflow,
            "mlflow.entities": entities,
            "judge.scorer": scorer,
        }

    def test_logs_all_feedback_fields_as_metadata(self):
        calls, modules = self._modules()
        feedback = {
            "field_path": "cards.0.cardMeta.cardDisplayName",
            "accepted": False,
            "corrected_value": "Platinum",
            "original_value": "Platinun",
        }
        with patch.dict(sys.modules, modules):
            result = _log_human_feedback("req-0123456789ab", feedback)

        self.assertEqual(result, {"status": "logged"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["trace_id"], "tr-1")
        self.assertEqual(calls[0]["value"], False)
        self.assertEqual(calls[0]["metadata"], {
            "request_id": "req-0123456789ab",
            **feedback,
        })

    def test_missing_trace_is_skipped(self):
        calls, modules = self._modules(run_id=None)
        with patch.dict(sys.modules, modules):
            result = _log_human_feedback("req-0123456789ab", {
                "field_path": "rewards.closingPoints", "accepted": True,
            })
        self.assertEqual(result, {"status": "skipped", "reason": "trace not found"})
        self.assertEqual(calls, [])

    def test_mlflow_failure_is_skipped_not_raised(self):
        _, modules = self._modules(fail=True)
        with patch.dict(sys.modules, modules):
            result = _log_human_feedback("req-0123456789ab", {
                "field_path": "transactions.0.amount", "accepted": True,
            })
        self.assertEqual(result["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
