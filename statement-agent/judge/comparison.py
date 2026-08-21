"""Convert independent Opus ground truth and candidate extraction into comparisons."""

from decimal import Decimal

from contracts.models import (
    ComparisonOutcome, FieldComparison, FieldScope, MatchMethod, ParseRequest,
)
from judge.matching import THRESHOLDS, match_transactions
from judge.normalization import norm_date, norm_desc, norm_key, norm_last_four, norm_num

# Per-card scalar fields, grouped by the sub-object within each card.  The
# group is paired with the dig key (cardMeta / bigPicture) so build_comparisons
# can dig the right nested object per card.
CARD_META_PATHS = (
    "cards[].cardMeta.cardDisplayName",
    "cards[].cardMeta.lastFourDigit",
    "cards[].cardMeta.productFamily",
    "cards[].cardMeta.network",
)
CARD_BIGPICTURE_PATHS = (
    "cards[].bigPicture.cardCreditLimit",
    "cards[].bigPicture.cardAvailableCreditLimit",
)
# Top-level nested-object scalar fields, grouped by their parent object.  The
# leaf is the final path segment; the parent is the segment before it.
_STATEMENT_META_PATHS = (
    "statementMeta.issuerName",
    "statementMeta.statementDate",
    "statementMeta.dueDate",
    "statementMeta.statementPeriodStart",
    "statementMeta.statementPeriodEnd",
)
_STATEMENT_SUMMARY_PATHS = (
    "statementLevelSummary.totalAmountDue",
    "statementLevelSummary.totalMinimumAmountDue",
    "statementLevelSummary.totalCreditLimit",
    "statementLevelSummary.availableCreditLimit",
)
_REWARDS_PATHS = (
    "rewards.pointsEarnedThisCycle",
    "rewards.closingPoints",
    "rewards.programType",
    "rewards.openingPoints",
    "rewards.pointsRedeemedThisCycle",
    "rewards.pointsExpiringNext30Days",
    "rewards.pointsExpiringNext60Days",
    "rewards.bonusPointsThisCycle",
)
TRANSACTION_PATHS = (
    "transactions[].date",
    "transactions[].description",
    "transactions[].amount",
    "transactions[].direction",
    "transactions[].rewardPointsOnThisTransaction",
)
# All scalar (non-transaction) judged paths, in a stable order.  Used by
# judge_error_comparisons to build one ABSENT_IN_PDF sentinel per scalar field
# (23 here + 5 transaction = 28, matching contracts.models.JUDGED_FIELDS).
SCALAR_PATHS = (
    *CARD_META_PATHS,
    *CARD_BIGPICTURE_PATHS,
    *_STATEMENT_META_PATHS,
    *_STATEMENT_SUMMARY_PATHS,
    *_REWARDS_PATHS,
)

# Explicit normalizer-routing sets.  Suffix heuristics (".date", ".amount")
# and the former "rewards." prefix are insufficient once judged fields span
# nested objects whose leaf names carry neither suffix
# (statementMeta.statementDate, statementLevelSummary.totalAmountDue,
# cards[].bigPicture.cardCreditLimit, transactions[].rewardPointsOnThis-
# Transaction).  These explicit sets are checked BEFORE the suffix
# heuristics so every numeric/date field routes to the right normalizer.
# The "rewards." prefix heuristic is DROPPED (superseded by NUMERIC_PATHS) so
# rewards.programType — a string — falls through to norm_desc instead of being
# mis-compared as a number (two different program-type strings would both
# norm_num to None and wrongly AGREE).
DATE_PATHS = frozenset({
    "transactions[].date",
    "statementMeta.statementDate",
    "statementMeta.dueDate",
    "statementMeta.statementPeriodStart",
    "statementMeta.statementPeriodEnd",
})
NUMERIC_PATHS = frozenset({
    "transactions[].amount",
    "transactions[].rewardPointsOnThisTransaction",
    "cards[].bigPicture.cardCreditLimit",
    "cards[].bigPicture.cardAvailableCreditLimit",
    "statementLevelSummary.totalAmountDue",
    "statementLevelSummary.totalMinimumAmountDue",
    "statementLevelSummary.totalCreditLimit",
    "statementLevelSummary.availableCreditLimit",
    "rewards.pointsEarnedThisCycle",
    "rewards.closingPoints",
    "rewards.openingPoints",
    "rewards.pointsRedeemedThisCycle",
    "rewards.pointsExpiringNext30Days",
    "rewards.pointsExpiringNext60Days",
    "rewards.bonusPointsThisCycle",
})


def _null(value: object) -> bool:
    return value is None or isinstance(value, str) and not value.strip()


def _canonical(path: str, value: object):
    if path in DATE_PATHS or path.endswith(".date"):
        return norm_date(value)
    if path in NUMERIC_PATHS or path.endswith(".amount"):
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
    if path in NUMERIC_PATHS or path.endswith(".amount"):
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
    # Per-card scalar fields (cardMeta.*, bigPicture.*) — one comparison per
    # card per field, keyed by card_index.
    expected_cards = expected.get("cards") if isinstance(expected.get("cards"), list) else []
    actual_cards = actual.get("cards") if isinstance(actual.get("cards"), list) else []
    for card_index in range(max(1, len(expected_cards), len(actual_cards))):
        expected_card = expected_cards[card_index] if card_index < len(expected_cards) else {}
        actual_card = actual_cards[card_index] if card_index < len(actual_cards) else {}
        for group, parent in ((CARD_META_PATHS, "cardMeta"), (CARD_BIGPICTURE_PATHS, "bigPicture")):
            for path in group:
                leaf = path.split(".")[-1]
                expected_value = _dig(expected_card, parent, leaf)
                actual_value = _dig(actual_card, parent, leaf)
                comparisons.append(FieldComparison(path, expected_value, actual_value,
                    _outcome(path, expected_value, actual_value), FieldScope.SCALAR,
                    MatchMethod.DIRECT, card_index=card_index))
    # Top-level nested-object scalar fields (statementMeta.*,
    # statementLevelSummary.*, rewards.*) — one comparison per field, dug from
    # the parent object in both expected and actual.
    for parent, paths in (
        ("statementMeta", _STATEMENT_META_PATHS),
        ("statementLevelSummary", _STATEMENT_SUMMARY_PATHS),
        ("rewards", _REWARDS_PATHS),
    ):
        for path in paths:
            leaf = path.split(".")[-1]
            expected_value, actual_value = _dig(expected, parent, leaf), _dig(actual, parent, leaf)
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
    """28 unscored sentinels (23 scalar + 5 transaction) make judge failure
    explicit without blaming extraction — one ABSENT_IN_PDF per judged field."""
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
