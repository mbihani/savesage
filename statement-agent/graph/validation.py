"""Payload validation: JSON-Schema conformance + declarative GT rules.

Stdlib-only so the validation tests run without langgraph. Two layers:

1. *Schema conformance* -- a minimal hand-written validator over
   ``schema/gt_schema.json``. ``jsonschema`` is a third-party package and cannot
   be installed on this machine (pypi is blackholed), so we implement just enough
   of the subset our single schema uses: ``type`` (incl. ``["x","null"]``
   unions), ``enum``, ``required``, ``additionalProperties: false``, ``items``,
   ``properties``. This is deliberately schema-specific, not a general
   validator -- it will refuse to check constructs the GT schema never uses.

2. *Declarative GT rules* -- the four rules in :mod:`rules.validation`
   (last-four-digits, amount/direction, closing-points arithmetic, txn date
   shape). These are semantic invariants the schema cannot express.

A validation failure NEVER raises out of :func:`validate_payload`; it is
collected into the returned :class:`ValidationReport` so the graph can persist a
structured partial result and the UI can show what failed.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rules.validation import VALIDATION_RULES

_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "gt_schema.json"
_schema_cache: dict[str, Any] | None = None

_LAST_FOUR = re.compile(r"^\d{4}$")
_DATE_DDMMYYYY = re.compile(r"^\d{2}/\d{2}/\d{4}$")


def load_gt_schema() -> dict[str, Any]:
    """Load and cache the vendored GT schema (a small static JSON file)."""
    global _schema_cache
    if _schema_cache is None:
        _schema_cache = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return _schema_cache


@dataclass(slots=True)
class ValidationReport:
    """Outcome of validating one extraction payload."""

    schema_valid: bool = False
    schema_errors: list[str] = field(default_factory=list)
    rule_errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.schema_valid and not self.rule_errors

    @property
    def all_errors(self) -> list[str]:
        return [*self.schema_errors, *self.rule_errors]


# --------------------------------------------------------------------------- #
# 1. JSON-Schema conformance (schema-specific subset)
# --------------------------------------------------------------------------- #


def _type_ok(value: Any, types: list[str]) -> bool:
    # bool is a subclass of int in Python; the schema says "number"/"boolean"
    # distinctly, so a bool must NOT satisfy "number".
    for t in types:
        if t == "null":
            if value is None:
                return True
        elif t == "boolean":
            if isinstance(value, bool):
                return True
        elif t == "number":
            # Reject non-finite numbers: json.loads accepts NaN/Infinity literals,
            # and a NaN totalAmountDue would poison the Lakebase table and any
            # downstream arithmetic. math.isfinite(False) is True, but bool is
            # already excluded above.
            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
                return True
        elif t == "string":
            if isinstance(value, str):
                return True
        elif t == "array":
            if isinstance(value, list):
                return True
        elif t == "object":
            if isinstance(value, dict):
                return True
    return False


def _check_node(value: Any, node: dict[str, Any], path: str, errors: list[str]) -> None:
    # type (may be a single string or a list with "null")
    tdecl = node.get("type")
    if tdecl is None:
        # The GT schema always declares a type; absence is a schema bug.
        errors.append(f"{path}: schema node has no 'type'")
        return
    types = tdecl if isinstance(tdecl, list) else [tdecl]
    if not _type_ok(value, types):
        errors.append(f"{path}: expected type {tdecl}, got {type(value).__name__}")
        return

    # enum (nullable enums list null as a member, handled by _type_ok)
    if "enum" in node and value is not None and value not in node["enum"]:
        errors.append(f"{path}: {value!r} not in enum {node['enum']}")

    if isinstance(value, dict) and "properties" in node:
        props = node["properties"]
        for key in value:
            if key not in props:
                if node.get("additionalProperties") is False:
                    errors.append(f"{path}.{key}: additional property not allowed")
                continue
            _check_node(value[key], props[key], f"{path}.{key}", errors)
        required = node.get("required", ())
        for key in required:
            if key not in value:
                errors.append(f"{path}: missing required key {key!r}")

    if isinstance(value, list) and "items" in node:
        items = node["items"]
        for i, item in enumerate(value):
            _check_node(item, items, f"{path}[{i}]", errors)


def validate_schema_conformance(payload: Any, schema: dict[str, Any] | None = None) -> list[str]:
    """Return a list of schema-conformance errors (empty == valid)."""
    errors: list[str] = []
    _check_node(payload, schema or load_gt_schema(), "$", errors)
    return errors


# --------------------------------------------------------------------------- #
# 2. Declarative GT rules
# --------------------------------------------------------------------------- #


def _is_number(v: Any) -> bool:
    """True for a finite non-bool numeric value (NaN/inf are NOT numbers here)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _check_last_four(payload: dict[str, Any], errors: list[str]) -> None:
    cards = payload.get("cards")
    if not isinstance(cards, list):
        return  # schema conformance already flagged a non-array
    for i, card in enumerate(cards):
        if not isinstance(card, dict):
            continue
        meta = card.get("cardMeta")
        if not isinstance(meta, dict):
            continue
        lf = meta.get("lastFourDigit")
        if lf is None:
            continue
        if not isinstance(lf, str) or not _LAST_FOUR.fullmatch(lf):
            errors.append(f"cards[{i}].cardMeta.lastFourDigit: null or exactly four ASCII digits, got {lf!r}")


def _check_amount_direction(payload: dict[str, Any], errors: list[str]) -> None:
    txns = payload.get("transactions")
    if not isinstance(txns, list):
        return
    for i, txn in enumerate(txns):
        if not isinstance(txn, dict):
            continue
        amount = txn.get("amount")
        if amount is None:
            continue
        if _is_number(amount):
            if amount < 0:
                errors.append(
                    f"transactions[{i}].amount: must be non-negative, got {amount!r} "
                    f"(direction carries debit/credit sign semantics)"
                )
        elif isinstance(amount, (int, float)) and not isinstance(amount, bool):
            # Present and numeric but non-finite (NaN/inf/-inf); schema conformance
            # is the primary guard, but flag it here too so validate_rules alone
            # never silently accepts one.
            errors.append(f"transactions[{i}].amount: must be finite, got {amount!r}")


def _check_closing_points(payload: dict[str, Any], errors: list[str]) -> None:
    rewards = payload.get("rewards")
    if not isinstance(rewards, dict):
        return
    keys = ("openingPoints", "pointsEarnedThisCycle", "pointsRedeemedThisCycle",
            "bonusPointsThisCycle", "closingPoints")
    values = [rewards.get(k) for k in keys]
    if any(v is None for v in values):
        return  # rule only applies when all five are present
    if not all(_is_number(v) for v in values):
        return  # schema conformance already flagged non-numbers
    opening, earned, redeemed, bonus, closing = values
    expected = opening + earned + bonus - redeemed  # type: ignore[operator]
    if closing != expected:
        errors.append(
            "rewards.closingPoints: arithmetic violation closing != "
            "opening + earned + bonus - redeemed "
            f"({closing} != {opening} + {earned} + {bonus} - {redeemed})"
        )


def _check_txn_date(payload: dict[str, Any], errors: list[str]) -> None:
    txns = payload.get("transactions")
    if not isinstance(txns, list):
        return
    for i, txn in enumerate(txns):
        if not isinstance(txn, dict):
            continue
        d = txn.get("date")
        if d is None:
            continue
        if not isinstance(d, str) or not _DATE_DDMMYYYY.fullmatch(d):
            errors.append(f"transactions[{i}].date: null or DD/MM/YYYY, got {d!r}")


_RULE_CHECKERS = (
    ("last_four_digits", _check_last_four),
    ("amount_direction", _check_amount_direction),
    ("closing_points_arithmetic", _check_closing_points),
    ("transaction_date", _check_txn_date),
)


def validate_rules(payload: dict[str, Any]) -> list[str]:
    """Return a list of rule-violation messages (empty == all rules pass)."""
    errors: list[str] = []
    for name, checker in _RULE_CHECKERS:
        # Each rule's name is cross-checked against rules.validation.VALIDATION_RULES
        # so a renamed rule here fails loudly in tests.
        before = len(errors)
        checker(payload, errors)
        _ = before  # kept for future per-rule accounting
    return errors


def validate_payload(payload: Any) -> ValidationReport:
    """Full validation: schema conformance + GT rules. Never raises."""
    schema_errors: list[str] = []
    rule_errors: list[str] = []
    schema_valid = False
    if isinstance(payload, dict):
        schema_errors = validate_schema_conformance(payload)
        schema_valid = not schema_errors
        if schema_valid:
            rule_errors = validate_rules(payload)
    else:
        schema_errors = [f"$: expected object, got {type(payload).__name__}"]
    return ValidationReport(schema_valid=schema_valid, schema_errors=schema_errors, rule_errors=rule_errors)


def rule_names() -> tuple[str, ...]:
    """Return the declared rule names (parity check vs rules.validation)."""
    return tuple(r.name for r in VALIDATION_RULES)
