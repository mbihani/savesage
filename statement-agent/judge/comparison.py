"""Convert independent Opus ground truth and candidate extraction into comparisons."""

from decimal import Decimal

from contracts.models import (
    ComparisonOutcome, FieldComparison, FieldScope, MatchMethod, ParseRequest,
)
from judge.matching import THRESHOLDS, match_transactions
from judge.normalization import norm_date, norm_desc, norm_key, norm_last_four, norm_num

SCALAR_PATHS = (
    "cards[].cardMeta.cardDisplayName",
    "cards[].cardMeta.lastFourDigit",
    "rewards.pointsEarnedThisCycle",
    "rewards.closingPoints",
)
TRANSACTION_PATHS = (
    "transactions[].date", "transactions[].description", "transactions[].amount",
)


def _null(value: object) -> bool:
    return value is None or isinstance(value, str) and not value.strip()


def _canonical(path: str, value: object):
    if path.endswith(".date"):
        return norm_date(value)
    if path.endswith(".amount") or path.startswith("rewards."):
        return norm_num(value)
    if path.endswith("lastFourDigit"):
        return norm_last_four(value)
    if path == "cards[].cardMeta.cardDisplayName":
        return norm_key(value)
    return norm_desc(value)


def _numeric_equal(expected: object, actual: object) -> bool:
    expected_number, actual_number = norm_num(expected), norm_num(actual)
    if expected_number is None or actual_number is None:
        return expected_number is None and actual_number is None
    expected_decimal, actual_decimal = Decimal(str(expected_number)), Decimal(str(actual_number))
    tolerance = max(Decimal("0.01"), abs(expected_decimal) * Decimal("0.000001"))
    return abs(expected_decimal - actual_decimal) <= tolerance


def _values_equal(path: str, expected: object, actual: object) -> bool:
    if path.endswith(".amount") or path.startswith("rewards."):
        return _numeric_equal(expected, actual)
    expected_canonical, actual_canonical = _canonical(path, expected), _canonical(path, actual)
    if path == "cards[].cardMeta.cardDisplayName":
        return bool(expected_canonical and actual_canonical
                    and (expected_canonical in actual_canonical or actual_canonical in expected_canonical))
    return expected_canonical == actual_canonical


def _outcome(path: str, expected: object, actual: object) -> ComparisonOutcome:
    if _null(expected):
        return ComparisonOutcome.ABSENT_IN_PDF
    if _null(actual):
        return ComparisonOutcome.DISAGREE
    if not _values_equal(path, expected, actual):
        return ComparisonOutcome.DISAGREE
    return ComparisonOutcome.AGREE if expected == actual else ComparisonOutcome.FORMAT_ONLY


def _dig(root: object, *parts: str):
    current = root
    for part in parts:
        current = current.get(part) if isinstance(current, dict) else None
    return current


def build_comparisons(request: ParseRequest, expected: dict, actual: dict) -> tuple[FieldComparison, ...]:
    comparisons: list[FieldComparison] = []
    expected_cards = expected.get("cards") if isinstance(expected.get("cards"), list) else []
    actual_cards = actual.get("cards") if isinstance(actual.get("cards"), list) else []
    for card_index in range(max(1, len(expected_cards), len(actual_cards))):
        expected_card = expected_cards[card_index] if card_index < len(expected_cards) else {}
        actual_card = actual_cards[card_index] if card_index < len(actual_cards) else {}
        for path, leaf in ((SCALAR_PATHS[0], "cardDisplayName"), (SCALAR_PATHS[1], "lastFourDigit")):
            expected_value = _dig(expected_card, "cardMeta", leaf)
            actual_value = _dig(actual_card, "cardMeta", leaf)
            comparisons.append(FieldComparison(path, expected_value, actual_value,
                _outcome(path, expected_value, actual_value), FieldScope.SCALAR,
                MatchMethod.DIRECT, card_index=card_index))
    for path in SCALAR_PATHS[2:]:
        leaf = path.split(".")[-1]
        expected_value, actual_value = _dig(expected, "rewards", leaf), _dig(actual, "rewards", leaf)
        comparisons.append(FieldComparison(path, expected_value, actual_value,
            _outcome(path, expected_value, actual_value), FieldScope.SCALAR))

    expected_rows = expected.get("transactions") if isinstance(expected.get("transactions"), list) else []
    actual_rows = actual.get("transactions") if isinstance(actual.get("transactions"), list) else []
    pairs, unmatched_actual, unmatched_expected = match_transactions(actual_rows, expected_rows, THRESHOLDS[request.bank])
    for actual_index, expected_index, similarity in pairs:
        expected_row = expected_rows[expected_index] if isinstance(expected_rows[expected_index], dict) else {}
        actual_row = actual_rows[actual_index] if isinstance(actual_rows[actual_index], dict) else {}
        for path in TRANSACTION_PATHS:
            leaf = path.split(".")[-1]
            expected_value, actual_value = expected_row.get(leaf), actual_row.get(leaf)
            comparisons.append(FieldComparison(path, expected_value, actual_value,
                _outcome(path, expected_value, actual_value), FieldScope.TRANSACTION_ROW,
                MatchMethod.DESCRIPTION_SIMILARITY_1TO1, expected_row_index=expected_index,
                actual_row_index=actual_index, similarity=similarity))
    for expected_index in unmatched_expected:
        expected_row = expected_rows[expected_index] if isinstance(expected_rows[expected_index], dict) else {}
        for path in TRANSACTION_PATHS:
            leaf = path.split(".")[-1]
            comparisons.append(FieldComparison(path, expected_row.get(leaf), None,
                ComparisonOutcome.UNMATCHED_ROW, FieldScope.TRANSACTION_ROW,
                MatchMethod.DESCRIPTION_SIMILARITY_1TO1, expected_row_index=expected_index,
                rationale="PDF row had no description-similar extraction row"))
    for actual_index in unmatched_actual:
        actual_row = actual_rows[actual_index] if isinstance(actual_rows[actual_index], dict) else {}
        for path in TRANSACTION_PATHS:
            leaf = path.split(".")[-1]
            comparisons.append(FieldComparison(path, None, actual_row.get(leaf),
                ComparisonOutcome.UNMATCHED_ROW, FieldScope.TRANSACTION_ROW,
                MatchMethod.DESCRIPTION_SIMILARITY_1TO1, actual_row_index=actual_index,
                rationale="Extraction row had no description-similar PDF row"))
    return tuple(comparisons)


def judge_error_comparisons(rationale: str) -> tuple[FieldComparison, ...]:
    """Seven unscored sentinels make judge failure explicit without blaming extraction."""
    comparisons = [
        FieldComparison(path, None, None, ComparisonOutcome.ABSENT_IN_PDF,
                        FieldScope.SCALAR, MatchMethod.DIRECT, rationale=rationale)
        for path in SCALAR_PATHS
    ]
    comparisons.extend(
        FieldComparison(path, None, None, ComparisonOutcome.ABSENT_IN_PDF,
                        FieldScope.TRANSACTION_ROW, MatchMethod.DESCRIPTION_SIMILARITY_1TO1,
                        rationale=rationale)
        for path in TRANSACTION_PATHS
    )
    return tuple(comparisons)
