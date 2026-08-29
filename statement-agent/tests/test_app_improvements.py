"""Tests for the three app-improvements changes:

1. GENERIC bank routing — ``Bank.GENERIC`` resolves to the same prompt/schema
   as AXIS (axis.txt / axis.json), appearing as a fifth bank in all routing
   tables and the Bank enum.
2. DBFS override loading — ``resolve_prompt`` and ``load_schema_for_bank``
   check a DBFS override first and fall back to the bundled file when the
   SDK is unavailable or the file is missing.
3. New API endpoints — ``GET /api/prompt/{bank}``, ``POST /api/prompt/{bank}``,
   and ``POST /api/parse-custom`` for the prompt/schema editor and re-run flow.

All tests are stdlib-only (no pip deps). The DBFS helpers import
``databricks-sdk`` function-local and return ``None``/``False`` when the SDK
is absent, so they are testable without mocking the SDK — we assert the
fallback path. For the DBFS-override path we patch ``read_dbfs_text`` to
simulate a file on DBFS.
"""

import json
import unittest
from unittest.mock import patch

from contracts.models import Bank
from graph.routing import resolve_prompt
from rules.routing import (
    PROMPT_BY_BANK,
    SCHEMA_BY_BANK,
    load_schema_for_bank,
)
from harness.dbfs import (
    BANKS_DBFS_DIR,
    bank_prompt_dbfs_path,
    bank_schema_dbfs_path,
)


# ---------------------------------------------------------------------------
# 1. GENERIC bank routing
# ---------------------------------------------------------------------------

class GenericBankRoutingTest(unittest.TestCase):
    """GENERIC is a fifth bank that reuses AXIS's prompt and schema."""

    def test_generic_exists_in_bank_enum(self) -> None:
        self.assertIn(Bank.GENERIC, Bank)

    def test_generic_in_prompt_by_bank(self) -> None:
        """PROMPT_BY_BANK has an entry for GENERIC."""
        self.assertIn(Bank.GENERIC, PROMPT_BY_BANK)

    def test_generic_in_schema_by_bank(self) -> None:
        """SCHEMA_BY_BANK has an entry for GENERIC."""
        self.assertIn(Bank.GENERIC, SCHEMA_BY_BANK)

    def test_generic_prompt_same_path_as_axis(self) -> None:
        self.assertEqual(PROMPT_BY_BANK[Bank.GENERIC], PROMPT_BY_BANK[Bank.AXIS])

    def test_generic_schema_same_path_as_axis(self) -> None:
        self.assertEqual(SCHEMA_BY_BANK[Bank.GENERIC], SCHEMA_BY_BANK[Bank.AXIS])

    def test_generic_resolves_to_nonempty_prompt(self) -> None:
        prompt = resolve_prompt(Bank.GENERIC)
        self.assertGreater(len(prompt.strip()), 0)

    def test_generic_prompt_text_matches_axis(self) -> None:
        """GENERIC and AXIS load the same prompt text from the same file."""
        self.assertEqual(resolve_prompt(Bank.GENERIC), resolve_prompt(Bank.AXIS))

    def test_generic_schema_matches_axis(self) -> None:
        """GENERIC and AXIS load the same schema from the same file."""
        gen = load_schema_for_bank(Bank.GENERIC)
        axis = load_schema_for_bank(Bank.AXIS)
        self.assertEqual(gen, axis)

    def test_generic_schema_is_valid_json_dict(self) -> None:
        schema = load_schema_for_bank(Bank.GENERIC)
        self.assertIsInstance(schema, dict)
        # A valid JSON schema has "properties" and "type" at minimum.
        self.assertIn("properties", schema)

    def test_all_five_banks_have_prompt_and_schema(self) -> None:
        """Every Bank enum value must have entries in both routing tables."""
        self.assertEqual(set(PROMPT_BY_BANK), set(Bank))
        self.assertEqual(set(SCHEMA_BY_BANK), set(Bank))


# ---------------------------------------------------------------------------
# 2. DBFS override loading
# ---------------------------------------------------------------------------

class DbfsOverrideTest(unittest.TestCase):
    """resolve_prompt and load_schema_for_bank check DBFS before bundled files."""

    def test_prompt_dbfs_path_format(self) -> None:
        path = bank_prompt_dbfs_path("HDFC")
        self.assertEqual(path, f"{BANKS_DBFS_DIR}/HDFC/prompt.txt")

    def test_schema_dbfs_path_format(self) -> None:
        path = bank_schema_dbfs_path("ICICI")
        self.assertEqual(path, f"{BANKS_DBFS_DIR}/ICICI/schema.json")

    def test_prompt_dbfs_path_for_generic(self) -> None:
        path = bank_prompt_dbfs_path("GENERIC")
        self.assertEqual(path, f"{BANKS_DBFS_DIR}/GENERIC/prompt.txt")

    def test_resolve_prompt_falls_back_when_dbfs_empty(self) -> None:
        """When read_dbfs_text returns None (no SDK or no file), the bundled
        file is used. This is the default path on a stdlib-only machine."""
        with patch("graph.routing.read_dbfs_text", return_value=None):
            prompt = resolve_prompt(Bank.HDFC)
        self.assertGreater(len(prompt.strip()), 0)

    def test_resolve_prompt_uses_dbfs_override(self) -> None:
        """When read_dbfs_text returns non-empty text, that text is used
        instead of the bundled file."""
        fake_prompt = "DBFS override prompt for HDFC"
        with patch("graph.routing.read_dbfs_text", return_value=fake_prompt):
            prompt = resolve_prompt(Bank.HDFC)
        self.assertEqual(prompt, fake_prompt)

    def test_resolve_prompt_ignores_whitespace_only_dbfs_override(self) -> None:
        """A whitespace-only DBFS override is treated as no override."""
        with patch("graph.routing.read_dbfs_text", return_value="   \n  "):
            prompt = resolve_prompt(Bank.HDFC)
        self.assertNotEqual(prompt.strip(), "")
        # Should be the bundled HDFC prompt, not whitespace.
        self.assertGreater(len(prompt.strip()), 10)

    def test_load_schema_falls_back_when_dbfs_empty(self) -> None:
        with patch("rules.routing.read_dbfs_text", return_value=None):
            schema = load_schema_for_bank(Bank.SBI)
        self.assertIsInstance(schema, dict)
        self.assertIn("properties", schema)

    def test_load_schema_uses_dbfs_override(self) -> None:
        fake_schema = {"properties": {"fake": True}, "type": "object"}
        with patch("rules.routing.read_dbfs_text",
                   return_value=json.dumps(fake_schema)):
            schema = load_schema_for_bank(Bank.SBI)
        self.assertEqual(schema, fake_schema)

    def test_load_schema_falls_back_on_invalid_json_dbfs(self) -> None:
        """If the DBFS override exists but is invalid JSON, the bundled file
        is used as a fallback."""
        with patch("rules.routing.read_dbfs_text", return_value="{not valid json"):
            schema = load_schema_for_bank(Bank.HDFC)
        self.assertIsInstance(schema, dict)
        self.assertIn("properties", schema)

    def test_resolve_prompt_for_generic_with_dbfs_override(self) -> None:
        """GENERIC can have its own DBFS override separate from AXIS."""
        fake_prompt = "Custom generic prompt"
        with patch("graph.routing.read_dbfs_text", return_value=fake_prompt):
            prompt = resolve_prompt(Bank.GENERIC)
        self.assertEqual(prompt, fake_prompt)


# ---------------------------------------------------------------------------
# 3. API endpoints (GET/POST /api/prompt/{bank}, POST /api/parse-custom)
# ---------------------------------------------------------------------------

class PromptSchemaEndpointTest(unittest.TestCase):
    """Test the GET/POST /api/prompt/{bank} endpoints via the FastAPI app.

    Uses TestClient (from fastapi.testclient if available, else starlette).
    If neither is installed, these tests are skipped — they require the
    web framework to create a test client.
    """

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

    def test_get_prompt_schema_returns_both(self) -> None:
        """GET /api/prompt/{bank} returns {'prompt': ..., 'schema': ...}."""
        resp = self.client.get("/api/prompt/HDFC")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("prompt", data)
        self.assertIn("schema", data)
        self.assertGreater(len(data["prompt"].strip()), 0)
        self.assertIsInstance(data["schema"], dict)
        self.assertIn("properties", data["schema"])

    def test_get_prompt_schema_for_generic(self) -> None:
        """GET /api/prompt/GENERIC returns the GENERIC prompt/schema (= axis)."""
        resp = self.client.get("/api/prompt/GENERIC")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreater(len(data["prompt"].strip()), 0)
        self.assertIsInstance(data["schema"], dict)

    def test_get_prompt_schema_matches_axis_for_generic(self) -> None:
        """GENERIC and AXIS return identical prompt and schema."""
        gen = self.client.get("/api/prompt/GENERIC").json()
        axis = self.client.get("/api/prompt/AXIS").json()
        self.assertEqual(gen["prompt"], axis["prompt"])
        self.assertEqual(gen["schema"], axis["schema"])

    def test_get_prompt_unknown_bank_falls_back_to_generic(self) -> None:
        """Unknown bank names return the GENERIC prompt/schema, not a 400."""
        resp = self.client.get("/api/prompt/UNKNOWN_BANK")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreater(len(data["prompt"].strip()), 0)
        self.assertIsInstance(data["schema"], dict)
        # Should match the GENERIC prompt/schema.
        generic = self.client.get("/api/prompt/GENERIC").json()
        self.assertEqual(data["prompt"], generic["prompt"])
        self.assertEqual(data["schema"], generic["schema"])

    def test_post_prompt_schema_saves_to_dbfs(self) -> None:
        """POST /api/prompt/{bank} writes prompt+schema to DBFS."""
        with patch("harness.dbfs.write_dbfs_text", return_value=True) as mock_write, \
             patch("harness.dbfs.read_dbfs_registry", return_value=[]), \
             patch("harness.dbfs.write_dbfs_registry", return_value=True) as registry_write:
            resp = self.client.post(
                "/api/prompt/HDFC",
                json={"prompt": "test prompt", "schema": {"type": "object"}},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["bank"], "HDFC")
        # write_dbfs_text called twice: prompt + schema
        self.assertEqual(mock_write.call_count, 2)

    def test_post_prompt_missing_fields_400(self) -> None:
        resp = self.client.post("/api/prompt/HDFC", json={"prompt": "only"})
        self.assertEqual(resp.status_code, 400)

    def test_post_prompt_unknown_bank_saves_to_own_path(self) -> None:
        """Unknown bank names save to their own (upper-cased) DBFS path under
        the dynamic-bank layout, not a 400 and not the GENERIC path. The bank
        is registered so it is discoverable through GET /api/banks.
        """
        with patch("harness.dbfs.write_dbfs_text", return_value=True) as mock_write, \
             patch("harness.dbfs.read_dbfs_registry", return_value=[]), \
             patch("harness.dbfs.write_dbfs_registry", return_value=True) as registry_write:
            resp = self.client.post(
                "/api/prompt/unknown",
                json={"prompt": "p", "schema": {"type": "object"}},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["bank"], "UNKNOWN")
        # write_dbfs_text called twice: prompt + schema; registry is updated.
        self.assertEqual(mock_write.call_count, 2)
        registry_write.assert_called_once_with(["UNKNOWN"])

    def test_post_prompt_dbfs_failure_502(self) -> None:
        with patch("harness.dbfs.write_dbfs_text", return_value=False):
            resp = self.client.post(
                "/api/prompt/HDFC",
                json={"prompt": "p", "schema": {"type": "object"}},
            )
        self.assertEqual(resp.status_code, 502)

    def test_post_prompt_non_dict_schema_400(self) -> None:
        """A schema that is valid JSON but not a dict (e.g. list, string,
        null) must be rejected with 400."""
        for bad_schema in ([], "hello", None, 42, True):
            with self.subTest(schema=bad_schema):
                resp = self.client.post(
                    "/api/prompt/HDFC",
                    json={"prompt": "p", "schema": bad_schema},
                )
                self.assertEqual(resp.status_code, 400)


class ParseCustomEndpointTest(unittest.TestCase):
    """Test POST /api/parse-custom endpoint."""

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

    def test_parse_custom_returns_request_id(self) -> None:
        """POST /api/parse-custom with a file and bank returns a request_id."""
        # We must mock _run_parse so it doesn't actually start the pipeline.
        with patch("app.main._run_parse"):
            resp = self.client.post(
                "/api/parse-custom",
                files={"file": ("test.pdf", b"%PDF-1.4 fake", "application/pdf")},
                data={"bank": "HDFC"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("request_id", data)
        self.assertIsInstance(data["request_id"], str)

    def test_parse_custom_with_overrides(self) -> None:
        """POST /api/parse-custom accepts prompt_override and schema_override."""
        with patch("app.main._run_parse"):
            resp = self.client.post(
                "/api/parse-custom",
                files={"file": ("test.pdf", b"%PDF-1.4 fake", "application/pdf")},
                data={
                    "bank": "ICICI",
                    "prompt_override": "custom prompt text",
                    "schema_override": json.dumps({"type": "object"}),
                },
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("request_id", resp.json())

    def test_parse_custom_unknown_bank_accepted(self) -> None:
        """Unknown bank names are accepted (GENERIC fallback), not 400."""
        with patch("app.main._run_parse"):
            resp = self.client.post(
                "/api/parse-custom",
                files={"file": ("test.pdf", b"%PDF-1.4 fake", "application/pdf")},
                data={"bank": "UNKNOWN"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("request_id", resp.json())

    def test_parse_custom_empty_file_400(self) -> None:
        resp = self.client.post(
            "/api/parse-custom",
            files={"file": ("empty.pdf", b"", "application/pdf")},
            data={"bank": "HDFC"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_parse_custom_invalid_schema_json_400(self) -> None:
        resp = self.client.post(
            "/api/parse-custom",
            files={"file": ("test.pdf", b"%PDF-1.4 fake", "application/pdf")},
            data={
                "bank": "HDFC",
                "schema_override": "{not valid json",
            },
        )
        self.assertEqual(resp.status_code, 400)

    def test_parse_custom_non_dict_schema_400(self) -> None:
        """A schema_override that is valid JSON but not a dict (e.g. list,
        string, null) must be rejected with 400."""
        for bad_schema in ("[]", '"hello"', "null", "42", "true"):
            with self.subTest(schema=bad_schema):
                resp = self.client.post(
                    "/api/parse-custom",
                    files={"file": ("test.pdf", b"%PDF-1.4 fake", "application/pdf")},
                    data={
                        "bank": "HDFC",
                        "schema_override": bad_schema,
                    },
                )
                self.assertEqual(resp.status_code, 400)

    def test_parse_custom_for_generic_bank(self) -> None:
        """POST /api/parse-custom accepts GENERIC as a valid bank."""
        with patch("app.main._run_parse"):
            resp = self.client.post(
                "/api/parse-custom",
                files={"file": ("test.pdf", b"%PDF-1.4 fake", "application/pdf")},
                data={"bank": "GENERIC"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("request_id", resp.json())


# ---------------------------------------------------------------------------
# 4. DBFS helper unit tests (no SDK needed)
# ---------------------------------------------------------------------------

class DbfsHelperTest(unittest.TestCase):
    """DBFS helpers return None/False when the SDK is absent."""

    def test_read_dbfs_text_returns_none_without_sdk(self) -> None:
        from harness.dbfs import read_dbfs_text
        # databricks-sdk is not installed locally; the function-local import
        # raises ImportError and the function returns None.
        result = read_dbfs_text(bank_prompt_dbfs_path("HDFC"))
        self.assertIsNone(result)

    def test_write_dbfs_text_returns_false_without_sdk(self) -> None:
        from harness.dbfs import write_dbfs_text
        result = write_dbfs_text(
            bank_prompt_dbfs_path("HDFC"), "content"
        )
        self.assertFalse(result)

    def test_dbfs_dirs_are_absolute_paths(self) -> None:
        self.assertTrue(BANKS_DBFS_DIR.startswith("/"))

    def test_prompt_dbfs_dir_value(self) -> None:
        self.assertEqual(BANKS_DBFS_DIR, "/Workspace/savesage-bank-configs/banks")

    def test_schema_dbfs_dir_value(self) -> None:
        self.assertEqual(
            bank_schema_dbfs_path("HDFC"),
            "/Workspace/savesage-bank-configs/banks/HDFC/schema.json",
        )


if __name__ == "__main__":
    unittest.main()
