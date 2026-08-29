"""Per-bank schema tests: routing coverage, superset gate, and per-bank wiring.

Stdlib-only (no jsonschema/pydantic/langgraph on the test path). Asserts the
reconcile rule from the schema-per-bank workstream:

* ``SCHEMA_BY_BANK`` has an entry for every ``Bank`` enum value; each target
  file exists, is valid JSON, and is an object with the 5 top-level sections.
* SUPERSET GATE: for each per-bank schema, every field path AND every
  ``required`` entry present in ``gt_schema.json`` is also present (same path,
  same required-ness), and no enum/type/additionalProperties constraint is
  narrowed vs ``gt_schema.json``. This is the enforceable form of "structural
  superset" -- a real assertion, not vacuous.
* ``axis.json`` is json-equal to ``gt_schema.json`` (decision B1).
* The validation node / ``validate_payload`` selects and validates against the
  per-bank schema for a given ``Bank``.
* The extraction adapter resolves the per-bank schema for the request's bank.
"""

import copy
import io
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from contracts.models import Bank, ExtractionResult, ParseRequest
from rules.routing import SCHEMA_BY_BANK, load_schema_for_bank

AGENT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = AGENT_ROOT / "schema"
TOP_LEVEL_SECTIONS = ("statementMeta", "statementLevelSummary", "cards", "transactions", "rewards")


# --------------------------------------------------------------------------- #
# Schema-tree collector (the engine behind the superset gate)
# --------------------------------------------------------------------------- #

def _norm_type(t):
    """Normalise a JSON-Schema ``type`` decl to a frozenset for order-insensitive
    comparison (``["string","null"]`` == ``["null","string"]``)."""
    if isinstance(t, list):
        return frozenset(t)
    return frozenset([t])


def _collect(node, path, acc):
    """Walk a schema node, collecting property paths, required entries, enums,
    types, and additionalProperties into ``acc`` (mutated in place)."""
    if not isinstance(node, dict):
        return
    if "type" in node:
        acc["type"][path] = _norm_type(node["type"])
    if "enum" in node:
        acc["enum"][path] = frozenset(node["enum"])
    if "additionalProperties" in node:
        acc["addl"][path] = node["additionalProperties"]
    for key in node.get("required", []):
        acc["required"].add((path, key))
    props = node.get("properties")
    if isinstance(props, dict):
        for k, v in props.items():
            acc["prop"].add(f"{path}.{k}")
            _collect(v, f"{path}.{k}", acc)
    items = node.get("items")
    if isinstance(items, dict):
        _collect(items, f"{path}[]", acc)


def _collect_schema(node):
    acc = {"prop": set(), "required": set(), "enum": {}, "type": {}, "addl": {}}
    _collect(node, "$", acc)
    return acc


def _gt():
    return json.loads((SCHEMA_DIR / "gt_schema.json").read_text(encoding="utf-8"))


def _bank_schema(bank):
    return json.loads(SCHEMA_BY_BANK[bank].read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 1. SCHEMA_BY_BANK routing coverage
# --------------------------------------------------------------------------- #

class SchemaByBankRoutingTest(unittest.TestCase):
    def test_schema_by_bank_covers_every_bank_enum_value(self) -> None:
        # Every Bank enum value must have a schema mapped (mirrors PROMPT_BY_BANK).
        self.assertEqual(set(SCHEMA_BY_BANK), set(Bank))

    def test_every_schema_file_exists_and_is_valid_json(self) -> None:
        for bank, path in SCHEMA_BY_BANK.items():
            self.assertTrue(path.exists(), f"{bank.value}: schema file missing at {path}")
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertIsInstance(data, dict, f"{bank.value}: schema is not a JSON object")

    def test_every_schema_has_the_five_top_level_sections(self) -> None:
        for bank in Bank:
            data = _bank_schema(bank)
            self.assertEqual(
                set(data.get("properties", {})), set(TOP_LEVEL_SECTIONS),
                f"{bank.value}: top-level properties != the 5 sections",
            )
            self.assertEqual(
                set(data.get("required", [])), set(TOP_LEVEL_SECTIONS),
                f"{bank.value}: top-level required != the 5 sections",
            )

    def test_schema_by_bank_paths_match_prompt_by_bank_pattern(self) -> None:
        # SCHEMA_BY_BANK keys must match PROMPT_BY_BANK keys (same Bank set).
        from rules.routing import PROMPT_BY_BANK
        self.assertEqual(set(SCHEMA_BY_BANK), set(PROMPT_BY_BANK))


# --------------------------------------------------------------------------- #
# 2. SUPERSET GATE (the enforceable reconcile rule)
# --------------------------------------------------------------------------- #

class SupersetGateTest(unittest.TestCase):
    """Each per-bank schema must be a structural superset of gt_schema.json.

    This is the enforceable form of the reconcile rule: every field path and
    every ``required`` entry in gt_schema.json is present in the per-bank
    schema, and no enum/type/additionalProperties constraint is narrowed.
    """

    def setUp(self) -> None:
        self._gt = _collect_schema(_gt())

    def test_every_gt_property_path_present_in_each_bank_schema(self) -> None:
        # No field from gt_schema.json may be dropped anywhere in the tree.
        for bank in Bank:
            bank_acc = _collect_schema(_bank_schema(bank))
            missing = self._gt["prop"] - bank_acc["prop"]
            self.assertFalse(missing, f"{bank.value}: dropped property paths: {sorted(missing)}")

    def test_every_gt_required_entry_present_in_each_bank_schema(self) -> None:
        # No `required` entry from gt_schema.json may be dropped. This is the
        # critical guard for downstream persistence/judge which assume the GT
        # field set (rawStatementId, statementPeriod*, bigPicture, bonusPoints).
        for bank in Bank:
            bank_acc = _collect_schema(_bank_schema(bank))
            missing = self._gt["required"] - bank_acc["required"]
            self.assertFalse(
                missing, f"{bank.value}: dropped required entries: {sorted(missing)}",
            )

    def test_no_enum_narrowed_vs_gt(self) -> None:
        # A per-bank enum must be a SUPERSET of the gt enum at the same path:
        # the bank schema must not reject a value gt_schema.json accepts.
        for bank in Bank:
            bank_acc = _collect_schema(_bank_schema(bank))
            for path, gt_enum in self._gt["enum"].items():
                self.assertIn(path, bank_acc["enum"], f"{bank.value}: lost enum at {path}")
                narrowed = gt_enum - bank_acc["enum"][path]
                self.assertFalse(
                    narrowed,
                    f"{bank.value}: enum at {path} narrowed (dropped {narrowed})",
                )

    def test_no_type_narrowed_vs_gt(self) -> None:
        # Types must match gt_schema exactly (no narrowing of ["string","null"]
        # to "string", etc.) -- a narrower type would reject gt-valid payloads.
        for bank in Bank:
            bank_acc = _collect_schema(_bank_schema(bank))
            for path, gt_type in self._gt["type"].items():
                self.assertIn(path, bank_acc["type"], f"{bank.value}: lost type at {path}")
                self.assertEqual(
                    bank_acc["type"][path], gt_type,
                    f"{bank.value}: type at {path} changed from {set(gt_type)} to {set(bank_acc['type'][path])}",
                )

    def test_additionalProperties_false_preserved_vs_gt(self) -> None:
        # Where gt_schema seals an object with additionalProperties:false, the
        # per-bank schema must keep it sealed (relaxing to true would let the
        # model emit stray fields; tightening isn't possible beyond false).
        for bank in Bank:
            bank_acc = _collect_schema(_bank_schema(bank))
            for path, gt_addl in self._gt["addl"].items():
                if gt_addl is False:
                    self.assertIs(
                        bank_acc["addl"].get(path), False,
                        f"{bank.value}: additionalProperties at {path} not sealed false",
                    )

    def test_gt_required_fields_absent_from_gemini_are_present_per_bank(self) -> None:
        # The GEMINI sources DROP these; the reconcile rule REQUIRES them. This
        # is the pointed assertion that the reconcile (not replace) decision was
        # honoured for the specific fields the GEMINI schemas lack.
        gt_required_absent_in_gemini = {
            ("$.statementMeta", "statementPeriodStart"),
            ("$.statementMeta", "statementPeriodEnd"),
            ("$.statementMeta", "rawStatementId"),
            ("$.cards[]", "bigPicture"),
            ("$.rewards", "bonusPointsThisCycle"),
        }
        for bank in Bank:
            bank_acc = _collect_schema(_bank_schema(bank))
            for entry in gt_required_absent_in_gemini:
                self.assertIn(
                    entry, bank_acc["required"],
                    f"{bank.value}: reconcile rule broken -- {entry} not required",
                )


# --------------------------------------------------------------------------- #
# 3. AXIS is an exact copy of gt_schema.json (decision B1)
# --------------------------------------------------------------------------- #

class AxisIsGtCopyTest(unittest.TestCase):
    def test_axis_json_equal_to_gt_schema(self) -> None:
        axis = json.loads((SCHEMA_DIR / "axis.json").read_text(encoding="utf-8"))
        self.assertEqual(axis, _gt())

    def test_axis_byte_identical_to_gt_schema(self) -> None:
        # The task permits json-equal, but we generated a byte copy; assert it
        # so a future regeneration does not silently diverge in formatting.
        axis_bytes = (SCHEMA_DIR / "axis.json").read_bytes()
        gt_bytes = (SCHEMA_DIR / "gt_schema.json").read_bytes()
        self.assertEqual(axis_bytes, gt_bytes)

    def test_axis_has_no_descriptions(self) -> None:
        # gt_schema.json has no descriptions; axis (its copy) must have none.
        # NB: a "description" KEY also exists as a transaction FIELD NAME; the
        # schema keyword is a string, the field name maps to a dict sub-schema.
        axis = _bank_schema(Bank.AXIS)

        def _has_desc_keyword(node):
            if isinstance(node, dict):
                if isinstance(node.get("description"), str):
                    return True
                for v in node.values():
                    if _has_desc_keyword(v):
                        return True
            elif isinstance(node, list):
                return any(_has_desc_keyword(v) for v in node)
            return False

        self.assertFalse(_has_desc_keyword(axis), "axis.json carries a description keyword (it must be a pure copy)")


# --------------------------------------------------------------------------- #
# 4. Per-bank descriptions layered in from the GEMINI sources
# --------------------------------------------------------------------------- #

class PerBankDescriptionsTest(unittest.TestCase):
    """Proves the bank-specific guidance was overlaid (the point of the split)."""

    def _desc(self, schema, *path) -> str | None:
        """Navigate to a field's schema and return its `description` keyword.

        ``items`` is a path segment that descends into an array's item schema
        (``node["items"]``); every other segment is a property name under
        ``node["properties"]``.
        """
        node = schema
        for key in path:
            if key == "items":
                node = node["items"]
            else:
                node = node["properties"][key]
        return node.get("description")

    def test_hdfc_carries_hdfc_specific_descriptions(self) -> None:
        s = _bank_schema(Bank.HDFC)
        self.assertIn("HDFC", self._desc(s, "cards", "items", "cardMeta", "productFamily"))
        # HDFC also tuned isPrimaryCard + the two expiring-points fields.
        self.assertIsNotNone(self._desc(s, "cards", "items", "cardMeta", "isPrimaryCard"))
        self.assertIsNotNone(self._desc(s, "rewards", "pointsExpiringNext30Days"))
        self.assertIsNotNone(self._desc(s, "rewards", "pointsExpiringNext60Days"))

    def test_icici_carries_icici_specific_descriptions(self) -> None:
        s = _bank_schema(Bank.ICICI)
        self.assertIn("ICICI", self._desc(s, "cards", "items", "cardMeta", "productFamily"))
        self.assertIsNotNone(self._desc(s, "statementLevelSummary", "totalCreditLimit"))
        self.assertIsNotNone(self._desc(s, "transactions", "items", "txnType"))
        self.assertIsNotNone(self._desc(s, "rewards", "openingPoints"))

    def test_sbi_carries_sbi_specific_descriptions(self) -> None:
        s = _bank_schema(Bank.SBI)
        self.assertIn("SBI", self._desc(s, "cards", "items", "cardMeta", "productFamily"))
        self.assertIsNotNone(self._desc(s, "transactions", "items", "txnType"))
        self.assertIsNotNone(self._desc(s, "rewards", "pointsExpiringNext30Days"))

    def test_load_schema_for_bank_returns_the_right_file(self) -> None:
        # The resolver must return the bank's own schema (not the shared one).
        for bank in (Bank.HDFC, Bank.ICICI, Bank.SBI):
            resolved = load_schema_for_bank(bank)
            on_disk = _bank_schema(bank)
            self.assertEqual(resolved, on_disk, f"{bank.value}: resolver returned a different schema")
        # AXIS resolves to the gt schema content (exact copy).
        self.assertEqual(load_schema_for_bank(Bank.AXIS), _gt())


# --------------------------------------------------------------------------- #
# 5. Validation selects + validates against the per-bank schema
# --------------------------------------------------------------------------- #

def _valid_payload():
    """A gt-shaped payload valid against every per-bank schema (all supersets)."""
    return {
        "statementMeta": {
            "issuerName": "SYNTHETIC BANK", "statementDate": "01/04/2026",
            "dueDate": "20/04/2026", "statementPeriodStart": "01/03/2026",
            "statementPeriodEnd": "31/03/2026", "rawStatementId": "synthetic-001",
        },
        "statementLevelSummary": {
            "totalAmountDue": 3.0, "totalMinimumAmountDue": 1.0,
            "totalCreditLimit": 100000.0, "availableCreditLimit": 99997.0,
        },
        "cards": [{
            "cardMeta": {"cardDisplayName": "SYNTHETIC", "productFamily": "SYNTHETIC",
                         "lastFourDigit": "0000", "network": "VISA", "isPrimaryCard": True},
            "bigPicture": {"cardCreditLimit": 100000.0, "cardAvailableCreditLimit": 99997.0},
        }],
        "transactions": [
            {"date": "05/03/2026", "description": "PURCHASE", "amount": 1.0,
             "direction": "DEBIT", "txnType": "PURCHASE",
             "rewardPointsOnThisTransaction": 1, "currency": "INR"},
        ],
        "rewards": {
            "programType": "SYNTHETIC", "openingPoints": 0,
            "pointsEarnedThisCycle": 1, "pointsRedeemedThisCycle": 0,
            "closingPoints": 1, "pointsExpiringNext30Days": 0,
            "pointsExpiringNext60Days": 0, "bonusPointsThisCycle": 0,
        },
    }


class ValidationPerBankTest(unittest.TestCase):
    def test_valid_payload_validates_against_each_bank_schema(self) -> None:
        from graph.validation import validate_payload
        for bank in Bank:
            report = validate_payload(_valid_payload(), load_schema_for_bank(bank))
            self.assertTrue(
                report.schema_valid, f"{bank.value}: {report.all_errors}",
            )

    def test_missing_gt_required_field_fails_against_per_bank_schema(self) -> None:
        # The superset rule is ENFORCED: a field gt_schema requires (and the
        # GEMINI sources dropped) must still be required in the per-bank schema.
        from graph.validation import validate_payload, validate_schema_conformance
        for bank in (Bank.HDFC, Bank.ICICI, Bank.SBI):
            schema = load_schema_for_bank(bank)
            for field in ("statementPeriodStart", "statementPeriodEnd", "rawStatementId"):
                p = _valid_payload()
                del p["statementMeta"][field]
                errors = validate_schema_conformance(p, schema)
                self.assertTrue(
                    any(field in e and "missing required" in e for e in errors),
                    f"{bank.value}: dropping {field} should fail the per-bank schema",
                )
            # Dropping bigPicture (required by gt, absent from GEMINI) must fail.
            p = _valid_payload()
            del p["cards"][0]["bigPicture"]
            self.assertTrue(
                any("bigPicture" in e and "missing required" in e for e in validate_schema_conformance(p, schema)),
                f"{bank.value}: dropping bigPicture should fail the per-bank schema",
            )
            # Dropping bonusPointsThisCycle must fail.
            p = _valid_payload()
            del p["rewards"]["bonusPointsThisCycle"]
            self.assertTrue(
                any("bonusPointsThisCycle" in e and "missing required" in e for e in validate_schema_conformance(p, schema)),
                f"{bank.value}: dropping bonusPointsThisCycle should fail the per-bank schema",
            )

    def test_validate_payload_default_schema_still_gt_for_back_compat(self) -> None:
        # No schema arg -> load_gt_schema() (back-compat). A gt-valid payload passes.
        from graph.validation import validate_payload
        report = validate_payload(_valid_payload())
        self.assertTrue(report.schema_valid, report.all_errors)


class ValidationNodePerBankTest(unittest.TestCase):
    """The validation node resolves the request's bank schema and validates."""

    def test_validate_node_resolves_per_bank_schema(self) -> None:
        from graph.nodes import validate_node, NodeDeps
        from graph.fakes import FakeExtractionAdapter, make_synthetic_request, _synthetic_valid_payload
        from graph.state import GraphState, Stage
        from contracts.models import ExtractionResult

        for bank in (Bank.HDFC, Bank.ICICI, Bank.SBI, Bank.AXIS):
            state = GraphState(request=make_synthetic_request(bank))
            state.extraction = ExtractionResult(
                request_id=state.request_id, payload=_synthetic_valid_payload(),
                model_id="synthetic", latency_ms=1.0,
            )
            with patch("graph.nodes.load_schema_for_bank") as mock_resolve:
                mock_resolve.return_value = load_schema_for_bank(bank)
                validate_node(state, NodeDeps(extraction=FakeExtractionAdapter()))
                mock_resolve.assert_called_once_with(bank)
            self.assertTrue(state.schema_valid, f"{bank.value}: {state.validation_errors}")
            self.assertEqual(state.stage, Stage.VALIDATED)

    def test_validate_node_per_bank_schema_rejects_dropped_gt_field(self) -> None:
        # The node must enforce the per-bank (superset) schema: a payload missing
        # a gt-required field is rejected, proving the node uses the bank schema
        # (which requires that field) rather than a looser GEMINI-shape schema.
        from graph.nodes import validate_node, NodeDeps
        from graph.fakes import FakeExtractionAdapter, make_synthetic_request, _synthetic_valid_payload
        from graph.state import GraphState

        state = GraphState(request=make_synthetic_request(Bank.HDFC))
        payload = _synthetic_valid_payload()
        del payload["statementMeta"]["rawStatementId"]  # gt-required, GEMINI-dropped
        state.extraction = ExtractionResult(
            request_id=state.request_id, payload=payload,
            model_id="synthetic", latency_ms=1.0,
        )
        validate_node(state, NodeDeps(extraction=FakeExtractionAdapter()))
        self.assertFalse(state.schema_valid)
        self.assertTrue(any("rawStatementId" in e for e in state.validation_errors))


# --------------------------------------------------------------------------- #
# 6. Extraction adapter resolves the per-bank schema
# --------------------------------------------------------------------------- #

class ExtractionAdapterPerBankTest(unittest.TestCase):
    def _fake_urlopen(self, resp):
        body = json.dumps(resp).encode()
        class _Ctx:
            def __init__(self, b):
                self._b = b
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return self._b
        return MagicMock(return_value=_Ctx(body))

    def _luna_resp(self):
        return {"id": "x", "model": "databricks-gpt-5-6-luna",
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(_valid_payload())}}]}

    def test_extract_resolves_schema_for_request_bank(self) -> None:
        from harness.extraction_adapter import LunaExtractionAdapter
        from harness.policy import RetryPolicy

        class _Settings:
            extraction_endpoint = "databricks-gpt-5-6-luna"
            def endpoint_url(self, e):
                return f"https://host/serving-endpoints/{e}/invocations"

        for bank in (Bank.HDFC, Bank.ICICI, Bank.SBI, Bank.AXIS):
            req = ParseRequest(b"%PDF", "synthetic.pdf", bank, "r1")
            adapter = LunaExtractionAdapter(
                retry_policy=RetryPolicy(max_attempts=1, initial_backoff_seconds=0.0),
                settings=_Settings(),
                token_provider=lambda: "tok",
                urlopen=self._fake_urlopen(self._luna_resp()),
            )
            with patch("harness.extraction_adapter.load_schema_for_bank") as mock_resolve:
                mock_resolve.return_value = load_schema_for_bank(bank)
                adapter.extract(req)
                mock_resolve.assert_called_once_with(bank)

    def test_extract_sends_the_resolved_per_bank_schema_in_response_format(self) -> None:
        # The schema handed to transports (response_format.json_schema.schema)
        # must be the bank's per-bank schema, not the shared gt_schema.
        from harness.extraction_adapter import LunaExtractionAdapter
        from harness.policy import RetryPolicy

        class _Settings:
            extraction_endpoint = "databricks-gpt-5-6-luna"
            def endpoint_url(self, e):
                return f"https://host/serving-endpoints/{e}/invocations"

        captured = {}
        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data)
            body = json.dumps(self._luna_resp()).encode()
            class _Ctx:
                def __enter__(self): return self
                def __exit__(self, *a): return False
                def read(self): return body
            return _Ctx()

        req = ParseRequest(b"%PDF", "synthetic.pdf", Bank.SBI, "r1")
        adapter = LunaExtractionAdapter(
            retry_policy=RetryPolicy(max_attempts=1, initial_backoff_seconds=0.0),
            settings=_Settings(),
            token_provider=lambda: "tok",
            urlopen=fake_urlopen,
        )
        adapter.extract(req)
        sent_schema = captured["body"]["response_format"]["json_schema"]["schema"]
        self.assertEqual(sent_schema, load_schema_for_bank(Bank.SBI))
        # And it carries the SBI-specific guidance (not the bare gt schema).
        self.assertIn("SBI", sent_schema["properties"]["cards"]["items"]["properties"]
                      ["cardMeta"]["properties"]["productFamily"]["description"])


if __name__ == "__main__":
    unittest.main()
