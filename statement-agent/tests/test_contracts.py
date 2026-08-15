from datetime import UTC, datetime
import inspect
import unittest

from contracts.models import ComparisonOutcome, FieldComparison, FieldScope
from contracts.ports import ExtractionAdapter, FeedbackStore, JudgeAdapter, ResultStore, TraceSink
from memory.session import MemoryStore


class ContractTest(unittest.TestCase):
    def test_all_abstract_methods_are_typed(self) -> None:
        for cls in (ExtractionAdapter, JudgeAdapter, ResultStore, FeedbackStore, TraceSink, MemoryStore):
            for name in cls.__abstractmethods__:
                signature = inspect.signature(getattr(cls, name))
                self.assertNotEqual(signature.return_annotation, inspect.Signature.empty, f"{cls.__name__}.{name}")
                for parameter in list(signature.parameters.values())[1:]:
                    self.assertNotEqual(parameter.annotation, inspect.Signature.empty, f"{cls.__name__}.{name}.{parameter.name}")

    def test_abc_bodies_raise_not_implemented(self) -> None:
        calls = (
            (ExtractionAdapter.extract, (None, None)),
            (JudgeAdapter.judge, (None, None, None)),
            (ResultStore.save_extraction, (None, None)),
            (ResultStore.save_verdict, (None, None)),
            (ResultStore.get_extraction, (None, "request")),
            (FeedbackStore.append_feedback, (None, None)),
            (FeedbackStore.list_feedback, (None, "request")),
            (TraceSink.record, (None, None)),
            (MemoryStore.read, (None, "request")),
            (MemoryStore.write, (None, None)),
            (MemoryStore.delete, (None, "request")),
        )
        for method, args in calls:
            with self.subTest(method=method.__qualname__), self.assertRaises(NotImplementedError):
                method(*args)

    def test_only_seven_judged_paths_are_admitted(self) -> None:
        valid = FieldComparison("transactions[].amount", 1.0, 1.0, ComparisonOutcome.AGREE, FieldScope.TRANSACTION_ROW, "description_similarity_1to1")
        self.assertEqual(valid.outcome, ComparisonOutcome.AGREE)
        with self.assertRaises(ValueError):
            FieldComparison("transactions[].direction", "DEBIT", "DEBIT", ComparisonOutcome.AGREE, FieldScope.TRANSACTION_ROW, "description_similarity_1to1")

    def test_datetime_runtime_supports_utc(self) -> None:
        self.assertIsNotNone(datetime.now(UTC).tzinfo)


if __name__ == "__main__":
    unittest.main()
