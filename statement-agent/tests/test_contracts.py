from datetime import UTC, datetime
import inspect
import unittest

from contracts.models import ComparisonOutcome, FieldComparison, FieldScope, MatchMethod
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
            (ResultStore.save_extraction, (None, None, None)),
            (ResultStore.save_verdict, (None, None)),
            (ResultStore.get_extraction, (None, "request")),
            (ResultStore.get_verdict, (None, "request")),
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

    def test_only_judged_paths_are_admitted(self) -> None:
        valid = FieldComparison(
            "transactions[].amount", 1.0, 1.0, ComparisonOutcome.AGREE,
            FieldScope.TRANSACTION_ROW, MatchMethod.DESCRIPTION_SIMILARITY_1TO1,
        )
        self.assertEqual(valid.outcome, ComparisonOutcome.AGREE)
        with self.assertRaises(ValueError):
            FieldComparison(
                "cards[].cardMeta.isPrimaryCard", True, True,
                ComparisonOutcome.AGREE, FieldScope.SCALAR,
                MatchMethod.DIRECT,
            )

    def test_transaction_comparison_rejects_direct_match(self) -> None:
        with self.assertRaises(ValueError):
            FieldComparison(
                "transactions[].date", "01/01/2026", "01/01/2026",
                ComparisonOutcome.AGREE, FieldScope.TRANSACTION_ROW,
                MatchMethod.DIRECT,
            )

    def test_scalar_comparison_defaults_to_direct_match(self) -> None:
        comparison = FieldComparison(
            "rewards.closingPoints", 100, 100,
            ComparisonOutcome.AGREE, FieldScope.SCALAR,
        )
        self.assertIs(comparison.match_method, MatchMethod.DIRECT)

    def test_datetime_runtime_supports_utc(self) -> None:
        self.assertIsNotNone(datetime.now(UTC).tzinfo)

    def test_scorer_and_models_judged_fields_are_in_sync(self) -> None:
        """The two independent JUDGED_FIELDS definitions must contain exactly
        the same 28 paths.  ``contracts.models.JUDGED_FIELDS`` (frozenset)
        drives ``FieldComparison.__post_init__`` validation; ``judge.scorer
        .JUDGED_FIELDS`` (tuple) drives per-field iteration order in the
        scorer.  A mismatch between the two would silently skip a field or
        admit an invalid path."""
        from contracts.models import JUDGED_FIELDS as models_fields
        from judge.scorer import JUDGED_FIELDS as scorer_fields

        self.assertEqual(set(scorer_fields), set(models_fields))
        self.assertEqual(len(scorer_fields), 28)
        self.assertEqual(len(models_fields), 28)

    def test_parse_ground_truth_allow_list_covers_exactly_judged_fields(self) -> None:
        """The judge ground-truth parser's allow-list (``judge.opus``) must
        accept EXACTLY the 28 ``JUDGED_FIELDS`` leaves — no more, no less.

        Before this change ``parse_ground_truth`` allow-listed only the
        original 7 fields, so the 21 fields added in PR #47 were rejected and
        every new metric stayed vacuously ABSENT_IN_PDF (the prompt emitted
        only the 7 and the parser rejected the rest).  This bidirectional pin
        groups the 28 judged paths into the parser's container/leaf structure
        and asserts each allow-list equals the judged leaves for that
        container, so the parser can never drift from the judged roster
        again — in either direction."""
        import json
        from contracts.models import JUDGED_FIELDS
        from judge import opus

        card_meta: set[str] = set()
        card_big: set[str] = set()
        stmt_meta: set[str] = set()
        stmt_sum: set[str] = set()
        rewards: set[str] = set()
        txns: set[str] = set()
        for path in JUDGED_FIELDS:
            clean = path.replace("[]", "")
            parts = clean.split(".")
            if parts[0] == "cards" and parts[1] == "cardMeta":
                card_meta.add(parts[2])
            elif parts[0] == "cards" and parts[1] == "bigPicture":
                card_big.add(parts[2])
            elif parts[0] == "statementMeta":
                stmt_meta.add(parts[1])
            elif parts[0] == "statementLevelSummary":
                stmt_sum.add(parts[1])
            elif parts[0] == "rewards":
                rewards.add(parts[1])
            elif parts[0] == "transactions":
                txns.add(parts[1])
        # Bidirectional: each parser allow-list is EXACTLY the judged leaves.
        self.assertEqual(opus._CARD_META_KEYS, card_meta)
        self.assertEqual(opus._CARD_BIGPICTURE_KEYS, card_big)
        self.assertEqual(opus._STATEMENT_META_KEYS, stmt_meta)
        self.assertEqual(opus._STATEMENT_SUMMARY_KEYS, stmt_sum)
        self.assertEqual(opus._REWARD_KEYS, rewards)
        self.assertEqual(opus._TRANSACTION_KEYS, txns)
        self.assertEqual(
            opus._TOP_LEVEL_KEYS,
            {"cards", "rewards", "transactions", "statementMeta",
             "statementLevelSummary"})
        # And the full 28-field shape round-trips through the parser.
        gt = {
            "cards": [{"cardMeta": {k: "x" for k in card_meta},
                       "bigPicture": {k: "x" for k in card_big}}],
            "rewards": {k: "x" for k in rewards},
            "transactions": [{k: "x" for k in txns}],
            "statementMeta": {k: "x" for k in stmt_meta},
            "statementLevelSummary": {k: "x" for k in stmt_sum},
        }
        opus.parse_ground_truth(json.dumps(gt))  # must not raise


if __name__ == "__main__":
    unittest.main()
