"""Tests for dynamic bank CRUD: runtime-added banks with prompt + schema
persistence to DBFS, picked up by the router without a redeploy.

Two layers are covered:

1. **DBFS helpers + routing** (stdlib-only, no pip deps). The new
   ``/savesage-statement-agent/banks/<BANK>/`` layout, the registry, and the
   resolution order in :func:`graph.routing.resolve_prompt`,
   :func:`rules.routing.load_schema_for_bank`, and
   :func:`graph.routing.detect_bank`. DBFS reads are patched to simulate
   files on DBFS; when unpatched the helpers return ``None``/``[]`` (no SDK
   locally) so the fallback path is exercised.
2. **API endpoints** (``GET/POST /api/banks``, ``GET/POST /api/schema/{bank}``).
   These need FastAPI + httpx; if either is missing the class is skipped —
   the same pattern as ``test_app_improvements.py``.
"""

import json
import unittest
from unittest.mock import patch

from contracts.models import Bank, bank_name
from graph.routing import RoutingError, coerce_request_bank, detect_bank, resolve_prompt
from harness.dbfs import (
    BANKS_DBFS_DIR,
    bank_prompt_dbfs_path,
    bank_schema_dbfs_path,
    read_dbfs_registry,
    registry_dbfs_path,
)
from rules.routing import load_schema_for_bank


# ---------------------------------------------------------------------------
# 1. DBFS path helpers (no SDK needed)
# ---------------------------------------------------------------------------


class DbfsPathTest(unittest.TestCase):
    def test_banks_dir_value(self) -> None:
        self.assertEqual(BANKS_DBFS_DIR, "/savesage-statement-agent/banks")

    def test_bank_prompt_dbfs_path(self) -> None:
        self.assertEqual(
            bank_prompt_dbfs_path("KOTAK"),
            "/savesage-statement-agent/banks/KOTAK/prompt.txt",
        )

    def test_bank_schema_dbfs_path(self) -> None:
        self.assertEqual(
            bank_schema_dbfs_path("RBL"),
            "/savesage-statement-agent/banks/RBL/schema.json",
        )

    def test_registry_dbfs_path(self) -> None:
        self.assertEqual(
            registry_dbfs_path(),
            "/savesage-statement-agent/banks/registry.json",
        )

    def test_paths_are_absolute(self) -> None:
        self.assertTrue(bank_prompt_dbfs_path("X").startswith("/"))
        self.assertTrue(bank_schema_dbfs_path("X").startswith("/"))
        self.assertTrue(registry_dbfs_path().startswith("/"))


class DbfsRegistryTest(unittest.TestCase):
    """read_dbfs_registry returns [] without the SDK (stdlib-only machine)."""

    def test_read_registry_empty_without_sdk(self) -> None:
        # databricks-sdk is not installed locally; read_dbfs_text returns None
        # so read_dbfs_registry yields [].
        self.assertEqual(read_dbfs_registry(), [])

    def test_read_registry_parses_a_list(self) -> None:
        with patch("harness.dbfs.read_dbfs_text", return_value='["KOTAK", "RBL"]'):
            self.assertEqual(read_dbfs_registry(), ["KOTAK", "RBL"])

    def test_read_registry_tolerates_non_list(self) -> None:
        for bad in ('{"not": "a list"}', "42", '"hello"', "null", "garbage"):
            with self.subTest(value=bad):
                with patch("harness.dbfs.read_dbfs_text", return_value=bad):
                    self.assertEqual(read_dbfs_registry(), [])

    def test_read_registry_tolerates_missing_file(self) -> None:
        with patch("harness.dbfs.read_dbfs_text", return_value=None):
            self.assertEqual(read_dbfs_registry(), [])


# ---------------------------------------------------------------------------
# 2. Routing resolution for dynamic banks
# ---------------------------------------------------------------------------


class ResolvePromptDynamicTest(unittest.TestCase):
    def test_dynamic_bank_resolves_from_dbfs(self) -> None:
        """A bank not in the Bank enum resolves from its DBFS prompt file."""
        override = "KOTAK extraction prompt"

        def fake_read(path: str) -> str | None:
            if path == bank_prompt_dbfs_path("KOTAK"):
                return override
            return None

        with patch("graph.routing.read_dbfs_text", side_effect=fake_read):
            self.assertEqual(resolve_prompt("KOTAK"), override)

    def test_dynamic_bank_whitespace_override_falls_through(self) -> None:
        """A whitespace-only DBFS override is treated as absent."""
        with patch("graph.routing.read_dbfs_text", return_value="   \n  "):
            # Not built-in, not registered, no real override -> GENERIC fallback.
            self.assertEqual(resolve_prompt("KOTAK"), resolve_prompt(Bank.GENERIC))

    def test_unknown_bank_falls_back_to_generic(self) -> None:
        with patch("graph.routing.read_dbfs_text", return_value=None), \
             patch("graph.routing.read_dbfs_registry", return_value=[]):
            self.assertEqual(resolve_prompt("NOPE"), resolve_prompt(Bank.GENERIC))

    def test_registered_bank_with_missing_prompt_raises(self) -> None:
        """A registered dynamic bank whose prompt file is missing is a loud
        error, not a silent GENERIC fallback."""
        with patch("graph.routing.read_dbfs_text", return_value=None), \
             patch("graph.routing.read_dbfs_registry", return_value=["KOTAK"]):
            with self.assertRaises(RoutingError):
                resolve_prompt("KOTAK")

    def test_built_in_bank_still_uses_bundled_when_no_override(self) -> None:
        with patch("graph.routing.read_dbfs_text", return_value=None):
            self.assertGreater(len(resolve_prompt(Bank.HDFC).strip()), 0)


class LoadSchemaDynamicTest(unittest.TestCase):
    def test_dynamic_bank_resolves_schema_from_dbfs(self) -> None:
        fake_schema = {"properties": {"issuerName": {}}, "type": "object"}

        def fake_read(path: str) -> str | None:
            if path == bank_schema_dbfs_path("KOTAK"):
                return json.dumps(fake_schema)
            return None

        with patch("rules.routing.read_dbfs_text", side_effect=fake_read):
            self.assertEqual(load_schema_for_bank("KOTAK"), fake_schema)

    def test_unknown_bank_falls_back_to_generic(self) -> None:
        with patch("rules.routing.read_dbfs_text", return_value=None), \
             patch("rules.routing.read_dbfs_registry", return_value=[]):
            self.assertEqual(
                load_schema_for_bank("NOPE"), load_schema_for_bank(Bank.GENERIC)
            )

    def test_registered_bank_with_missing_schema_raises(self) -> None:
        with patch("rules.routing.read_dbfs_text", return_value=None), \
             patch("rules.routing.read_dbfs_registry", return_value=["KOTAK"]):
            with self.assertRaises(RuntimeError):
                load_schema_for_bank("KOTAK")

    def test_built_in_bank_still_uses_bundled_when_no_override(self) -> None:
        with patch("rules.routing.read_dbfs_text", return_value=None):
            schema = load_schema_for_bank(Bank.SBI)
            self.assertIsInstance(schema, dict)
            self.assertIn("properties", schema)


class DetectBankTest(unittest.TestCase):
    def test_built_in_patterns(self) -> None:
        self.assertEqual(detect_bank("HDFC BANK statement"), "HDFC")
        self.assertEqual(detect_bank("issued by ICICI bank"), "ICICI")
        self.assertEqual(detect_bank("SBI Card"), "SBI")
        self.assertEqual(detect_bank("AXIS bank ltd"), "AXIS")

    def test_generic_fallback(self) -> None:
        self.assertEqual(detect_bank("nothing matches here"), "GENERIC")
        self.assertEqual(detect_bank(""), "GENERIC")

    def test_dynamic_bank_from_registry(self) -> None:
        with patch("graph.routing.read_dbfs_registry", return_value=["KOTAK", "RBL"]):
            self.assertEqual(detect_bank("Kotak Mahindra statement"), "KOTAK")
            self.assertEqual(detect_bank("RBL bank card"), "RBL")

    def test_built_in_takes_precedence_over_registry(self) -> None:
        # A registered dynamic bank named "HDFC" should still resolve as the
        # built-in HDFC (built-in patterns are checked first).
        with patch("graph.routing.read_dbfs_registry", return_value=["HDFC"]):
            self.assertEqual(detect_bank("HDFC statement"), "HDFC")

    def test_no_registry_match_falls_back(self) -> None:
        with patch("graph.routing.read_dbfs_registry", return_value=["KOTAK"]):
            self.assertEqual(detect_bank("some unknown issuer"), "GENERIC")


# ---------------------------------------------------------------------------
# 2b. coerce_request_bank + bank_name helpers
# ---------------------------------------------------------------------------


class CoerceRequestBankTest(unittest.TestCase):
    """coerce_request_bank preserves dynamic names as plain strings while
    collapsing built-in strings to the Bank enum (unlike try_bank which
    normalises unknowns to GENERIC)."""

    def test_built_in_string_uppercased_to_enum(self) -> None:
        self.assertIs(coerce_request_bank("hdfc"), Bank.HDFC)
        self.assertIs(coerce_request_bank("  icici "), Bank.ICICI)

    def test_built_in_enum_passes_through(self) -> None:
        self.assertIs(coerce_request_bank(Bank.SBI), Bank.SBI)

    def test_dynamic_bank_preserved_as_string(self) -> None:
        result = coerce_request_bank("KOTAK")
        self.assertEqual(result, "KOTAK")
        self.assertNotIsInstance(result, Bank)

    def test_dynamic_bank_uppercased_and_trimmed(self) -> None:
        self.assertEqual(coerce_request_bank("  kotak  "), "KOTAK")

    def test_unknown_built_in_not_collapsed_to_generic(self) -> None:
        """The whole point: try_bank would return GENERIC here; coerce must
        preserve the real name so the router can resolve the dynamic prompt."""
        result = coerce_request_bank("UNKNOWNBANK")
        self.assertEqual(result, "UNKNOWNBANK")


class BankNameHelperTest(unittest.TestCase):
    """bank_name() extracts the string value from Bank or str uniformly.

    Critical because str(Bank.HDFC) returns 'Bank.HDFC' on Python 3.11+
    (frozen enum), not 'HDFC'.
    """

    def test_enum_returns_value(self) -> None:
        self.assertEqual(bank_name(Bank.HDFC), "HDFC")
        self.assertEqual(bank_name(Bank.GENERIC), "GENERIC")

    def test_plain_string_passes_through(self) -> None:
        self.assertEqual(bank_name("KOTAK"), "KOTAK")

    def test_enum_str_is_not_just_str(self) -> None:
        """Guard against the Python 3.11+ frozen-enum trap: str(Bank.HDFC)
        is 'Bank.HDFC', not 'HDFC' — which is why bank_name uses .value."""
        self.assertNotEqual(str(Bank.HDFC), "HDFC")
        self.assertEqual(bank_name(Bank.HDFC), "HDFC")


# ---------------------------------------------------------------------------
# 3. API endpoints (skipped without FastAPI + httpx)
# ---------------------------------------------------------------------------


class BankEndpointsTest(unittest.TestCase):
    """GET/POST /api/banks and GET/POST /api/schema/{bank}."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            import httpx  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("httpx not installed")
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            try:
                from starlette.testclient import TestClient
            except ImportError:
                raise unittest.SkipTest("fastapi/starlette not installed")
        from app.main import create_app
        cls.client = TestClient(create_app())

    def test_get_banks_returns_builtin_only_without_registry(self) -> None:
        """With no dynamic registry, only the 5 built-in banks are returned."""
        with patch("harness.dbfs.read_dbfs_registry", return_value=[]):
            resp = self.client.get("/api/banks")
        self.assertEqual(resp.status_code, 200)
        banks = resp.json()
        names = [b["name"] for b in banks]
        self.assertEqual(set(names), {b.value for b in Bank})
        self.assertTrue(all(b["dynamic"] is False for b in banks))

    def test_get_banks_merges_dynamic(self) -> None:
        with patch("harness.dbfs.read_dbfs_registry", return_value=["KOTAK", "RBL"]):
            resp = self.client.get("/api/banks")
        self.assertEqual(resp.status_code, 200)
        banks = {b["name"]: b["dynamic"] for b in resp.json()}
        self.assertFalse(banks["HDFC"])
        self.assertTrue(banks["KOTAK"])
        self.assertTrue(banks["RBL"])

    def test_get_banks_dedupes_builtin_in_registry(self) -> None:
        # A built-in name in the registry must not appear twice.
        with patch("harness.dbfs.read_dbfs_registry", return_value=["HDFC", "KOTAK"]):
            resp = self.client.get("/api/banks")
        names = [b["name"] for b in resp.json()]
        self.assertEqual(names.count("HDFC"), 1)
        self.assertIn("KOTAK", names)

    def test_post_banks_creates_dynamic_bank(self) -> None:
        with patch("harness.dbfs.read_dbfs_registry", return_value=[]) as reg, \
             patch("harness.dbfs.write_dbfs_text", return_value=True) as wr, \
             patch("harness.dbfs.write_dbfs_registry", return_value=True) as wreg, \
             patch("harness.dbfs.mkdirs_dbfs", return_value=True):
            resp = self.client.post(
                "/api/banks",
                json={"name": "kotak", "prompt": "p", "schema": {"type": "object"}},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        data = resp.json()
        self.assertEqual(data, {"name": "KOTAK", "dynamic": True})
        # prompt + schema written, then registry updated with KOTAK appended.
        self.assertEqual(wr.call_count, 2)
        wreg.assert_called_once()
        self.assertIn("KOTAK", wreg.call_args.args[0])

    def test_post_banks_rejects_builtin_name(self) -> None:
        with patch("harness.dbfs.read_dbfs_registry", return_value=[]):
            resp = self.client.post(
                "/api/banks",
                json={"name": "HDFC", "prompt": "p", "schema": {"type": "object"}},
            )
        self.assertEqual(resp.status_code, 409)

    def test_post_banks_rejects_duplicate_dynamic(self) -> None:
        with patch("harness.dbfs.read_dbfs_registry", return_value=["KOTAK"]):
            resp = self.client.post(
                "/api/banks",
                json={"name": "kotak", "prompt": "p", "schema": {"type": "object"}},
            )
        self.assertEqual(resp.status_code, 409)

    def test_post_banks_requires_name_prompt_schema(self) -> None:
        for body in ({}, {"name": "X"}, {"name": "X", "prompt": "p"}):
            with self.subTest(body=body):
                resp = self.client.post("/api/banks", json=body)
                self.assertEqual(resp.status_code, 400)

    def test_post_banks_accepts_schema_string(self) -> None:
        with patch("harness.dbfs.read_dbfs_registry", return_value=[]), \
             patch("harness.dbfs.write_dbfs_text", return_value=True), \
             patch("harness.dbfs.write_dbfs_registry", return_value=True), \
             patch("harness.dbfs.mkdirs_dbfs", return_value=True):
            resp = self.client.post(
                "/api/banks",
                json={"name": "RBL", "prompt": "p", "schema": '{"type": "object"}'},
            )
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_post_banks_dbfs_failure_502(self) -> None:
        with patch("harness.dbfs.read_dbfs_registry", return_value=[]), \
             patch("harness.dbfs.write_dbfs_text", return_value=False), \
             patch("harness.dbfs.mkdirs_dbfs", return_value=True):
            resp = self.client.post(
                "/api/banks",
                json={"name": "RBL", "prompt": "p", "schema": {"type": "object"}},
            )
        self.assertEqual(resp.status_code, 502)

    def test_get_schema_returns_schema(self) -> None:
        fake = {"properties": {"x": {}}, "type": "object"}
        with patch("rules.routing.load_schema_for_bank", return_value=fake):
            resp = self.client.get("/api/schema/HDFC")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), fake)

    def test_post_schema_saves_to_dbfs(self) -> None:
        with patch("harness.dbfs.write_dbfs_text", return_value=True) as wr, \
             patch("harness.dbfs.mkdirs_dbfs", return_value=True):
            resp = self.client.post(
                "/api/schema/kotak",
                json={"schema": {"type": "object"}},
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["bank"], "KOTAK")
        wr.assert_called_once()

    def test_post_schema_rejects_non_dict(self) -> None:
        for bad in ([], "hello", None, 42):
            with self.subTest(schema=bad):
                resp = self.client.post("/api/schema/HDFC", json={"schema": bad})
                self.assertEqual(resp.status_code, 400)

    def test_post_schema_dbfs_failure_502(self) -> None:
        with patch("harness.dbfs.write_dbfs_text", return_value=False), \
             patch("harness.dbfs.mkdirs_dbfs", return_value=True):
            resp = self.client.post(
                "/api/schema/HDFC", json={"schema": {"type": "object"}}
            )
        self.assertEqual(resp.status_code, 502)


if __name__ == "__main__":
    unittest.main()
