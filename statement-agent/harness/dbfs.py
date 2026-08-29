"""DBFS read/write helpers for prompt/schema overrides.

Imports ``databricks-sdk`` function-local so this module is importable in a
stdlib-only environment (the contract-test gate). Each function returns
``None``/``False`` on any failure — SDK missing, file not found, network
error — so callers silently fall back to the bundled file.

The override directories mirror the PROMPT_BY_BANK / SCHEMA_BY_BANK layout:
``/savesage/prompts/<BANK>.txt`` and ``/savesage/schemas/<BANK>.json``.
"""

import base64
import io
import logging

_LOGGER = logging.getLogger(__name__)

# DBFS override directories for prompts and schemas.
PROMPT_DBFS_DIR = "/savesage/prompts"
SCHEMA_DBFS_DIR = "/savesage/schemas"


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
    return f"{PROMPT_DBFS_DIR}/{bank_value}.txt"


def schema_dbfs_path(bank_value: str) -> str:
    """Return the DBFS path for a bank's schema override."""
    return f"{SCHEMA_DBFS_DIR}/{bank_value}.json"
