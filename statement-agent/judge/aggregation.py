"""Accuracy rollups with strict and narration-forgiven readings."""

from collections import defaultdict
from collections.abc import Iterable

from contracts.models import ComparisonOutcome, FieldComparison


def aggregate(comparisons: Iterable[FieldComparison]) -> dict[str, object]:
    grouped: dict[str, list[FieldComparison]] = defaultdict(list)
    all_items = list(comparisons)
    for comparison in all_items:
        grouped[comparison.field_path].append(comparison)

    def stats(items: list[FieldComparison], forgive_narration: bool = False) -> dict[str, object]:
        scored = [item for item in items if item.outcome is not ComparisonOutcome.ABSENT_IN_PDF]
        correct = sum(item.outcome in {ComparisonOutcome.AGREE, ComparisonOutcome.FORMAT_ONLY}
                      or forgive_narration and item.field_path == "transactions[].description"
                      and item.outcome is ComparisonOutcome.DISAGREE
                      for item in scored)
        return {"correct": correct, "scored": len(scored),
                "accuracy": correct / len(scored) if scored else None}

    return {
        "per_field": {path: stats(items) for path, items in sorted(grouped.items())},
        "strict": stats(all_items),
        "narration_forgiven": stats(all_items, forgive_narration=True),
    }
