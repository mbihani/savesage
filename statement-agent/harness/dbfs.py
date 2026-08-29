"""DBFS read/write helpers for prompt/schema overrides.

Imports ``databricks-sdk`` function-local so this module is importable in a
stdlib-only environment (the contract-test gate). Each function returns
``None``/``False`` on any failure — SDK missing, file not found, network
error — so callers silently fall back to the bundled file.

Two on-DBFS layouts coexist:

* **Built-in bank overrides** (legacy, kept for back-compat with prompts/schemas
  saved before dynamic banks existed):
  ``/savesage/prompts/<BANK>.txt`` and ``/savesage/schemas/<BANK>.json``.
* **Dynamic banks** (added at runtime via the UI/API): one directory per bank
  holding its prompt, schema, and a top-level registry listing every dynamic
  bank name::

      /savesage-statement-agent/banks/registry.json   # ["KOTAK", "RBL", ...]
      /savesage-statement-agent/banks/<BANK>/prompt.txt
      /savesage-statement-agent/banks/<BANK>/schema.json
"""

import base64
import io
import json
import logging
import re

_LOGGER = logging.getLogger(__name__)

# DBFS override directories for built-in bank prompts/schemas (legacy layout).
PROMPT_DBFS_DIR = "/savesage/prompts"
SCHEMA_DBFS_DIR = "/savesage/schemas"

# DBFS root for dynamically added banks (one subdirectory per bank, plus a
# top-level registry.json listing every dynamic bank name).
BANKS_DBFS_DIR = "/savesage-statement-agent/banks"

_BANK_NAME_RE = re.compile(r"^[A-Z0-9_-]+$")


def validate_bank_name(name: str) -> str:
    """Return a canonical, path-safe bank name or raise ``ValueError``."""
    if not isinstance(name, str):
        raise ValueError("bank name must be a string")
    normalized = name.strip().upper()
    if not normalized:
        raise ValueError("bank name must not be empty")
    if ".." in normalized or not _BANK_NAME_RE.fullmatch(normalized):
        raise ValueError(
            "bank name may contain only letters, numbers, underscores, and hyphens"
        )
    return normalized


def read_dbfs_text(dbfs_path: str) -> str | None:
    """Read a UTF-8 text file from DBFS.

    Returns the file content as a string, or ``None`` on any failure
    (SDK not installed, file not found, network error). Callers fall back
    to the bundled file when this returns ``None``.
    """
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError:
        return None
    try:
        client = WorkspaceClient()
        resp = client.dbfs.read(path=dbfs_path)
        if not resp.data:
            return None
        raw = base64.b64decode(resp.data)
        return raw.decode("utf-8")
    except Exception as exc:  # noqa: BLE001 — best-effort, never raises
        _LOGGER.debug("DBFS read failed for %s: %s", dbfs_path, exc)
        return None


def write_dbfs_text(dbfs_path: str, content: str) -> bool:
    """Write a UTF-8 text file to DBFS, overwriting if it exists.

    Returns ``True`` on success, ``False`` on any failure. The directory
    must already exist (the DBFS API does not create intermediate dirs on
    upload); ``/savesage`` is created by the app's deployment bundle.
    """
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError:
        return False
    try:
        client = WorkspaceClient()
        client.dbfs.upload(
            path=dbfs_path,
            contents=io.BytesIO(content.encode("utf-8")),
            overwrite=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort, never raises
        _LOGGER.warning("DBFS write failed for %s: %s", dbfs_path, exc)
        return False


def prompt_dbfs_path(bank_value: str) -> str:
    """Return the DBFS path for a bank's prompt override."""
    return f"{PROMPT_DBFS_DIR}/{validate_bank_name(bank_value)}.txt"


def schema_dbfs_path(bank_value: str) -> str:
    """Return the DBFS path for a built-in bank's schema override."""
    return f"{SCHEMA_DBFS_DIR}/{validate_bank_name(bank_value)}.json"


# ---------------------------------------------------------------------------
# Dynamic-bank helpers (the /savesage-statement-agent/banks/<BANK>/ layout).
# ---------------------------------------------------------------------------


def bank_dbfs_dir(bank_value: str) -> str:
    """Return the DBFS directory for a path-safe canonical bank name."""
    return f"{BANKS_DBFS_DIR}/{validate_bank_name(bank_value)}"


def bank_prompt_dbfs_path(bank_value: str) -> str:
    """Return the DBFS path for a (dynamic or overridden) bank's prompt."""
    return f"{bank_dbfs_dir(bank_value)}/prompt.txt"


def bank_schema_dbfs_path(bank_value: str) -> str:
    """Return the DBFS path for a (dynamic or overridden) bank's schema."""
    return f"{bank_dbfs_dir(bank_value)}/schema.json"


def registry_dbfs_path() -> str:
    """Return the DBFS path for the dynamic-bank registry."""
    return f"{BANKS_DBFS_DIR}/registry.json"


def mkdirs_dbfs(dbfs_path: str) -> bool:
    """Create a DBFS directory (and any missing parents), idempotent.

    Returns ``True`` on success, ``False`` on any failure. Needed because
    :func:`write_dbfs_text` does not create intermediate directories, so a
    per-bank subdirectory (``…/banks/<BANK>``) must be created before the
    prompt/schema files are uploaded.
    """
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError:
        return False
    try:
        client = WorkspaceClient()
        client.dbfs.mkdirs(path=dbfs_path)
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort, never raises
        _LOGGER.debug("DBFS mkdirs failed for %s: %s", dbfs_path, exc)
        return False


def read_dbfs_registry() -> list[str]:
    """Return the list of dynamic bank names from the DBFS registry.

    Returns ``[]`` on any failure (SDK missing, file not found, invalid
    JSON) so callers treat a missing registry as "no dynamic banks" and
    fall back gracefully.
    """
    text = read_dbfs_text(registry_dbfs_path())
    if not text:
        return []
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    names = []
    for name in data:
        if not isinstance(name, str):
            continue
        try:
            names.append(validate_bank_name(name))
        except ValueError:
            _LOGGER.warning("Ignoring unsafe bank name in DBFS registry: %r", name)
    return names


def write_dbfs_registry(names: list[str]) -> bool:
    """Overwrite the DBFS registry with ``names`` (a list of bank names).

    The parent directory is created first (best-effort). Returns ``True`` on
    success, ``False`` on any failure.
    """
    mkdirs_dbfs(BANKS_DBFS_DIR)
    return write_dbfs_text(
        registry_dbfs_path(),
        json.dumps(names, ensure_ascii=False),
    )
