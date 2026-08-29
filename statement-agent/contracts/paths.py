"""Canonical field paths shared by feedback producers, stores, and consumers."""

import re

_INDEX = r"(?:0|[1-9][0-9]*)"
_FEEDBACK_PATH = re.compile(
    rf"^(?:"
    rf"cards\.{_INDEX}\.cardMeta\.(?:cardDisplayName|lastFourDigit|productFamily|network)"
    rf"|cards\.{_INDEX}\.bigPicture\.(?:cardCreditLimit|cardAvailableCreditLimit)"
    rf"|transactions\.{_INDEX}\.(?:date|description|amount|direction|rewardPointsOnThisTransaction)"
    rf"|rewards\.(?:pointsEarnedThisCycle|closingPoints|programType|openingPoints|pointsRedeemedThisCycle|pointsExpiringNext30Days|pointsExpiringNext60Days|bonusPointsThisCycle)"
    rf"|statementMeta\.(?:issuerName|statementDate|dueDate|statementPeriodStart|statementPeriodEnd)"
    rf"|statementLevelSummary\.(?:totalAmountDue|totalMinimumAmountDue|totalCreditLimit|availableCreditLimit)"
    rf")$"
)


def is_valid_feedback_path(path: str) -> bool:
    """Return whether `path` is a canonical concrete path to a judged field."""
    return bool(_FEEDBACK_PATH.fullmatch(path))


def canonical_feedback_path(
    judged_field: str,
    *,
    row_index: int | None = None,
    card_index: int | None = None,
) -> str:
    """Convert a judged-field template and required array index to canonical form."""
    if row_index is not None and (isinstance(row_index, bool) or not isinstance(row_index, int) or row_index < 0):
        raise ValueError("row_index must be a non-negative integer")
    if card_index is not None and (isinstance(card_index, bool) or not isinstance(card_index, int) or card_index < 0):
        raise ValueError("card_index must be a non-negative integer")
    if judged_field.startswith("transactions[]."):
        if row_index is None or card_index is not None:
            raise ValueError("transaction fields require only row_index")
        path = judged_field.replace("transactions[]", f"transactions.{row_index}", 1)
    elif judged_field.startswith("cards[]."):
        if card_index is None or row_index is not None:
            raise ValueError("card fields require only card_index")
        path = judged_field.replace("cards[]", f"cards.{card_index}", 1)
    elif judged_field.startswith(("rewards.", "statementMeta.", "statementLevelSummary.")):
        if row_index is not None or card_index is not None:
            raise ValueError("statement scalar fields take no index")
        path = judged_field
    else:
        raise ValueError(f"unsupported judged field: {judged_field}")
    if not is_valid_feedback_path(path):
        raise ValueError(f"unsupported judged field: {judged_field}")
    return path
