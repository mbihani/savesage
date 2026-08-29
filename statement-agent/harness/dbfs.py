"""Workspace Files read/write helpers for bank prompt/schema configs.

Imports ``databricks-sdk`` function-local so this module is importable in a
stdlib-only environment (the contract-test gate). Each function returns
``None``/``False`` on any failure — SDK missing, file not found, network
error — so callers fall back to the bundled file.

The directory ``/Workspace/savesage-bank-configs/`` must exist, and the app
service principal must have ``CAN_MANAGE`` permission on it.

Built-in and dynamic banks share one layout: one directory per bank holding
its prompt and schema, plus a top-level registry listing every dynamic bank::

      /Workspace/savesage-bank-configs/banks/registry.json
      /Workspace/savesage-bank-configs/banks/<BANK>/prompt.txt
      /Workspace/savesage-bank-configs/banks/<BANK>/schema.json
"""

import io
import json
import logging
import re

_LOGGER = logging.getLogger(__name__)

# Workspace Files root for all bank configs (one subdirectory per bank, plus a
# top-level registry.json listing every dynamic bank name).
BANKS_DBFS_DIR = "/Workspace/savesage-bank-configs/banks"

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
    """Read a UTF-8 text file from Workspace Files.

    Returns the file content as a string, or ``None`` on any failure
    (SDK not installed, file not found, network error). Callers fall back
    to the bundled file when this returns ``None``.
    """
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError as exc:
        _LOGGER.warning(
            "Workspace Files SDK unavailable for read of %s: %s", dbfs_path, exc
        )
        return None
    try:
        client = WorkspaceClient()
        resp = client.files.download(dbfs_path)
        return resp.contents.read().decode("utf-8")
    except Exception as exc:  # noqa: BLE001 — best-effort, never raises
        _LOGGER.warning("Workspace Files read failed for %s: %s", dbfs_path, exc)
        return None


def write_dbfs_text(dbfs_path: str, content: str) -> bool:
    """Write a UTF-8 text file to Workspace Files, overwriting if it exists.

    Returns ``True`` on success, ``False`` on any failure. The directory
    must already exist because upload does not create intermediate directories.
    """
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError as exc:
        _LOGGER.warning(
            "Workspace Files SDK unavailable for write of %s: %s", dbfs_path, exc
        )
        return False
    try:
        client = WorkspaceClient()
        client.files.upload(
            dbfs_path,
            contents=io.BytesIO(content.encode("utf-8")),
            overwrite=True,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort, never raises
        _LOGGER.warning(
            "Workspace Files write failed for %s (%s): %s",
            dbfs_path,
            type(exc).__name__,
            exc,
        )
        return False


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
    """Create a Workspace Files directory (and missing parents), idempotent.

    Returns ``True`` on success, ``False`` on any failure. Needed because
    :func:`write_dbfs_text` does not create intermediate directories, so a
    per-bank subdirectory (``…/banks/<BANK>``) must be created before the
    prompt/schema files are uploaded.
    """
    try:
        from databricks.sdk import WorkspaceClient
    except ImportError as exc:
        _LOGGER.warning(
            "Workspace Files SDK unavailable for mkdir of %s: %s", dbfs_path, exc
        )
        return False
    try:
        client = WorkspaceClient()
        client.files.create_directory(dbfs_path)
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort, never raises
        _LOGGER.error(
            "Workspace Files mkdir failed for %s (%s): %s",
            dbfs_path,
            type(exc).__name__,
            exc,
        )
        return False


def seed_builtin_configs() -> bool:
    """Seed missing built-in bank configs into the shared banks directory.

    Existing files are never overwritten. Failures are non-fatal to callers:
    ``False`` is returned and routing can still use the bundled assets.
    """
    from contracts.models import Bank
    from rules.routing import PROMPT_BY_BANK, SCHEMA_BY_BANK

    try:
        if not mkdirs_dbfs(BANKS_DBFS_DIR):
            return False
        for bank in Bank:
            directory = bank_dbfs_dir(bank.value)
            if not mkdirs_dbfs(directory):
                return False

            prompt_path = bank_prompt_dbfs_path(bank.value)
            if read_dbfs_text(prompt_path) is None:
                prompt = PROMPT_BY_BANK[bank].read_text(encoding="utf-8")
                if not write_dbfs_text(prompt_path, prompt):
                    return False

            schema_path = bank_schema_dbfs_path(bank.value)
            if read_dbfs_text(schema_path) is None:
                schema = SCHEMA_BY_BANK[bank].read_text(encoding="utf-8")
                if not write_dbfs_text(schema_path, schema):
                    return False
        return True
    except Exception as exc:  # noqa: BLE001 -- startup seeding is best-effort
        _LOGGER.warning("Built-in bank config seeding failed: %s", exc)
        return False


def read_dbfs_registry() -> list[str]:
    """Return the list of dynamic bank names from the Workspace Files registry.

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
            _LOGGER.warning(
                "Ignoring unsafe bank name in Workspace Files registry: %r", name
            )
    return names


def write_dbfs_registry(names: list[str]) -> bool:
    """Overwrite the Workspace Files registry with a list of bank names.

    The parent directory is created first (best-effort). Returns ``True`` on
    success, ``False`` on any failure.
    """
    mkdirs_dbfs(BANKS_DBFS_DIR)
    return write_dbfs_text(
        registry_dbfs_path(),
        json.dumps(names, ensure_ascii=False),
    )
