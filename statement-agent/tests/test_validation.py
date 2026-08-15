"""Validation tests: JSON-Schema conformance + declarative GT rules (stdlib)."""

import copy
import unittest

from graph.validation import (
    ValidationReport,
    load_gt_schema,
    rule_names,
    validate_payload,
    validate_rules,
    validate_schema_conformance,
)
from rules.validation import VALIDATION_RULES

SYNTHETIC = {
    "statementMeta": {
        "issuerName": "SYNTHETIC BANK",
        "statementDate": "01/04/2026",
        "dueDate": "20/04/2026",
        "statementPeriodStart": "01/03/2026",
        "statementPeriodEnd": "31/03/2026",
        "rawStatementId": "synthetic-001",
    },
    "statementLevelSummary": {
        "totalAmountDue": 3.0,
        "totalMinimumAmountDue": 1.0,
        "totalCreditLimit": 100000.0,
        "availableCreditLimit": 99997.0,
    },
    "cards": [{
        "cardMeta": {
            "cardDisplayName": "SYNTHETIC CARDHOLDER",
            "productFamily": "SYNTHETIC",
            "lastFourDigit": "0000",
            "network": "VISA",
            "isPrimaryCard": True,
        },
        "bigPicture": {"cardCreditLimit": 100000.0, "cardAvailableCreditLimit": 99997.0},
    }],
    "transactions": [
        {"date": "05/03/2026", "description": "SYNTHETIC PURCHASE", "amount": 1.0,
         "direction": "DEBIT", "txnType": "PURCHASE",
         "rewardPointsOnThisTransaction": 1, "currency": "INR"},
        {"date": "06/03/2026", "description": "SYNTHETIC PAYMENT", "amount": 2.0,
         "direction": "CREDIT", "txnType": "PAYMENT",
         "rewardPointsOnThisTransaction": 0, "currency": "INR"},
    ],
    "rewards": {
        "programType": "SYNTHETIC",
        "openingPoints": 0,
        "pointsEarnedThisCycle": 1,
        "pointsRedeemedThisCycle": 0,
        "closingPoints": 1,
        "pointsExpiringNext30Days": 0,
        "pointsExpiringNext60Days": 0,
        "bonusPointsThisCycle": 0,
    },
}


def _clone() -> dict:
    return copy.deepcopy(SYNTHETIC)


class SchemaConformanceTest(unittest.TestCase):
    def test_synthetic_payload_is_schema_valid(self) -> None:
        self.assertEqual(validate_schema_conformance(_clone()), [])

    def test_missing_required_top_level_key(self) -> None:
        p = _clone()
        del p["rewards"]
        errors = validate_schema_conformance(p)
        self.assertTrue(any("missing required key 'rewards'" in e for e in errors), errors)

    def test_missing_required_nested_key(self) -> None:
        p = _clone()
        del p["statementMeta"]["statementDate"]
        errors = validate_schema_conformance(p)
        self.assertTrue(any("statementDate" in e for e in errors), errors)

    def test_additional_property_rejected(self) -> None:
        p = _clone()
        p["statementMeta"]["unexpected"] = "x"
        errors = validate_schema_conformance(p)
        self.assertTrue(any("additional property not allowed" in e for e in errors), errors)

    def test_wrong_type_for_number(self) -> None:
        p = _clone()
        p["statementLevelSummary"]["totalAmountDue"] = "3.0"
        errors = validate_schema_conformance(p)
        self.assertTrue(any("totalAmountDue" in e and "expected type" in e for e in errors), errors)

    def test_bool_is_not_number(self) -> None:
        # bool is a subclass of int; schema says "number" -- a bool must NOT pass.
        p = _clone()
        p["statementLevelSummary"]["totalAmountDue"] = True
        errors = validate_schema_conformance(p)
        self.assertTrue(any("totalAmountDue" in e for e in errors), errors)

    def test_null_allowed_for_nullable_field(self) -> None:
        p = _clone()
        p["statementMeta"]["issuerName"] = None
        p["cards"][0]["cardMeta"]["lastFourDigit"] = None
        self.assertEqual(validate_schema_conformance(p), [])

    def test_invalid_enum_direction(self) -> None:
        p = _clone()
        p["transactions"][0]["direction"] = "WIDGET"
        errors = validate_schema_conformance(p)
        self.assertTrue(any("direction" in e and "enum" in e for e in errors), errors)

    def test_direction_enum_allows_null(self) -> None:
        p = _clone()
        p["transactions"][0]["direction"] = None
        self.assertEqual(validate_schema_conformance(p), [])

    def test_non_object_payload(self) -> None:
        errors = validate_schema_conformance([1, 2, 3])
        self.assertTrue(any("expected type" in e for e in errors), errors)

    def test_card_array_item_checked(self) -> None:
        p = _clone()
        p["cards"][0]["cardMeta"]["isPrimaryCard"] = "yes"  # string, not boolean
        errors = validate_schema_conformance(p)
        self.assertTrue(any("isPrimaryCard" in e for e in errors), errors)

    def test_loads_gt_schema(self) -> None:
        schema = load_gt_schema()
        self.assertEqual(schema["type"], "object")
        self.assertIn("statementMeta", schema["properties"])


class RuleValidationTest(unittest.TestCase):
    def test_synthetic_payload_passes_all_rules(self) -> None:
        self.assertEqual(validate_rules(_clone()), [])

    def test_last_four_must_be_four_digits_or_null(self) -> None:
        for bad in ("000", "00000", "abcd", 1234, True):
            p = _clone()
            p["cards"][0]["cardMeta"]["lastFourDigit"] = bad
            errors = validate_rules(p)
            self.assertTrue(any("lastFourDigit" in e for e in errors), f"{bad!r}: {errors}")

    def test_last_four_null_is_allowed(self) -> None:
        p = _clone()
        p["cards"][0]["cardMeta"]["lastFourDigit"] = None
        self.assertEqual(validate_rules(p), [])

    def test_negative_amount_violates_amount_direction(self) -> None:
        p = _clone()
        p["transactions"][0]["amount"] = -1.0
        errors = validate_rules(p)
        self.assertTrue(any("amount" in e and "non-negative" in e for e in errors), errors)

    def test_zero_amount_is_allowed(self) -> None:
        p = _clone()
        p["transactions"][0]["amount"] = 0
        self.assertEqual(validate_rules(p), [])

    def test_closing_points_arithmetic_must_hold(self) -> None:
        p = _clone()
        # 0 + 5 + 2 - 1 = 6, but set closing to 99
        p["rewards"]["pointsEarnedThisCycle"] = 5
        p["rewards"]["bonusPointsThisCycle"] = 2
        p["rewards"]["pointsRedeemedThisCycle"] = 1
        p["rewards"]["closingPoints"] = 99
        errors = validate_rules(p)
        self.assertTrue(any("closingPoints" in e and "arithmetic" in e for e in errors), errors)

    def test_closing_points_arithmetic_skipped_when_any_absent(self) -> None:
        p = _clone()
        p["rewards"]["bonusPointsThisCycle"] = None  # one absent -> rule not applied
        self.assertEqual(validate_rules(p), [])

    def test_txn_date_must_be_ddmmyyyy_or_null(self) -> None:
        for bad in ("2026-03-05", "5/3/26", "05-03-2026", 3052026, True):
            p = _clone()
            p["transactions"][0]["date"] = bad
            errors = validate_rules(p)
            self.assertTrue(any("date" in e for e in errors), f"{bad!r}: {errors}")

    def test_txn_date_null_is_allowed(self) -> None:
        p = _clone()
        p["transactions"][0]["date"] = None
        self.assertEqual(validate_rules(p), [])

    def test_rule_names_match_declarative_rules(self) -> None:
        # Parity: graph.validation must check exactly the rules in rules.validation.
        self.assertEqual(rule_names(), tuple(r.name for r in VALIDATION_RULES))


class ValidatePayloadTest(unittest.TestCase):
    def test_valid_payload_returns_ok_report(self) -> None:
        report = validate_payload(_clone())
        self.assertIsInstance(report, ValidationReport)
        self.assertTrue(report.ok)
        self.assertTrue(report.schema_valid)
        self.assertEqual(report.schema_errors, [])
        self.assertEqual(report.rule_errors, [])

    def test_schema_failure_does_not_run_rules(self) -> None:
        # If the schema is invalid, rules are skipped (they assume a well-shaped dict).
        p = _clone()
        del p["rewards"]
        report = validate_payload(p)
        self.assertFalse(report.schema_valid)
        self.assertEqual(report.rule_errors, [])

    def test_partial_valid_schema_with_rule_error(self) -> None:
        p = _clone()
        p["transactions"][0]["amount"] = -5.0
        report = validate_payload(p)
        self.assertTrue(report.schema_valid)  # schema is fine
        self.assertFalse(report.ok)  # but rules fail
        self.assertEqual(report.schema_errors, [])
        self.assertGreater(len(report.rule_errors), 0)

    def test_non_dict_payload(self) -> None:
        report = validate_payload("not a dict")
        self.assertFalse(report.schema_valid)
        self.assertFalse(report.ok)

    def test_never_raises(self) -> None:
        # Any garbage input must produce a report, not an exception.
        for garbage in (None, [], 42, {"only": "partial"}, "string"):
            report = validate_payload(garbage)
            self.assertIsInstance(report, ValidationReport)


if __name__ == "__main__":
    unittest.main()
