"""Declarative GT payload invariants; workstream 2 supplies the validator."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ValidationRule:
    name: str
    paths: tuple[str, ...]
    constraint: str


VALIDATION_RULES = (
    ValidationRule("last_four_digits", ("/cards/*/cardMeta/lastFourDigit",), "null or exactly four ASCII digits"),
    ValidationRule("amount_direction", ("/transactions/*/amount", "/transactions/*/direction"), "amount is non-negative; direction carries debit/credit sign semantics"),
    ValidationRule("closing_points_arithmetic", ("/rewards/openingPoints", "/rewards/pointsEarnedThisCycle", "/rewards/pointsRedeemedThisCycle", "/rewards/bonusPointsThisCycle", "/rewards/closingPoints"), "when all values are present: closing = opening + earned + bonus - redeemed"),
    ValidationRule("transaction_date", ("/transactions/*/date",), "null or DD/MM/YYYY"),
)
