"""Per-bank prompt and schema routing; AXIS uses the generic Luna assets.

``PROMPT_BY_BANK`` / ``SCHEMA_BY_BANK`` mirror each other: every Bank maps to a
prompt file and a schema file. AXIS intentionally reuses the generic shared
schema (``schema/axis.json`` is a byte copy of ``schema/gt_schema.json``) and
the generic Luna prompt -- that is correct, not a bug.

The per-bank schemas (``schema/{hdfc,icici,sbi}.json``) are RECONCILED from
``<bank>/gemini/GEMINI_SCHEMA.json``: a structural superset of
``schema/gt_schema.json`` (no field/required/constraint dropped or tightened)
with the bank-specific ``description`` strings layered in. ``gt_schema.json``
stays the default/fallback for back-compat. See ``schema/PROVENANCE.md``.
"""

import json
from pathlib import Path

from contracts.models import Bank
from harness.dbfs import (
    bank_schema_dbfs_path,
    read_dbfs_registry,
    read_dbfs_text,
    schema_dbfs_path,
)

_AGENT_DIR = Path(__file__).resolve().parents[1]
PROMPT_DIR = _AGENT_DIR / "prompts"
SCHEMA_DIR = _AGENT_DIR / "schema"

PROMPT_BY_BANK: dict[Bank, Path] = {
    Bank.HDFC: PROMPT_DIR / "hdfc.txt",
    Bank.ICICI: PROMPT_DIR / "icici.txt",
    Bank.SBI: PROMPT_DIR / "sbi.txt",
    Bank.AXIS: PROMPT_DIR / "axis.txt",
    # GENERIC reuses the generic Luna prompt (axis.txt is the generic prompt,
    # not an AXIS-specific one — that is correct, not a bug).
    Bank.GENERIC: PROMPT_DIR / "axis.txt",
}

SCHEMA_BY_BANK: dict[Bank, Path] = {
    Bank.HDFC: SCHEMA_DIR / "hdfc.json",
    Bank.ICICI: SCHEMA_DIR / "icici.json",
    Bank.SBI: SCHEMA_DIR / "sbi.json",
    Bank.AXIS: SCHEMA_DIR / "axis.json",
    # GENERIC reuses the shared gt schema (axis.json == gt_schema.json).
    Bank.GENERIC: SCHEMA_DIR / "axis.json",
}


def load_schema_for_bank(bank: Bank | str) -> dict:
    """Load and return the per-bank extraction schema (stdlib json).

    Accepts a :class:`Bank` enum or an arbitrary string. Resolution order:

    1. **Dynamic-bank DBFS file** ``/savesage-statement-agent/banks/<bank>/schema.json``
       — checked first for *any* bank name (covers dynamically added banks
       AND built-in banks whose schema was overridden via the new API).
    2. **Built-in banks** (names in the :class:`Bank` enum): legacy DBFS
       override ``/savesage/schemas/<bank>.json`` → bundled ``schema/<bank>.json``.
    3. **A bank registered as dynamic but whose DBFS schema file is missing**:
       raises a clear :class:`RuntimeError` so the misconfiguration is loud.
    4. **A completely unknown bank** (not built-in, not in the registry):
       falls back to :data:`Bank.GENERIC` (the shared generic schema), mirroring
       :func:`graph.routing.resolve_prompt`.

    Reads from disk each call (schemas are small, ~4-10KB) so a schema edit —
    whether on DBFS or the bundled file — is picked up without a process restart.
    """
    bank_str = bank.value if isinstance(bank, Bank) else str(bank)

    # 1. Dynamic-bank DBFS override (works for both dynamic and built-in names).
    dbfs_text = read_dbfs_text(bank_schema_dbfs_path(bank_str))
    if dbfs_text:
        try:
            return json.loads(dbfs_text)
        except json.JSONDecodeError:
            pass  # corrupt override — fall through to the next source

    # 2. Built-in bank: legacy DBFS override → bundled file.
    try:
        bank_enum = Bank(bank_str)
    except (ValueError, TypeError):
        bank_enum = None
    if bank_enum is not None:
        legacy = read_dbfs_text(schema_dbfs_path(bank_enum.value))
        if legacy:
            try:
                return json.loads(legacy)
            except json.JSONDecodeError:
                pass  # fall back to bundled file
        return json.loads(SCHEMA_BY_BANK[bank_enum].read_text(encoding="utf-8"))

    # 3. Registered dynamic bank whose DBFS schema is missing/corrupt → loud error.
    if bank_str in read_dbfs_registry():
        raise RuntimeError(
            f"dynamic bank {bank_str!r} is registered but its schema file "
            f"is missing or corrupt on DBFS at {bank_schema_dbfs_path(bank_str)}"
        )

    # 4. Completely unknown bank → GENERIC fallback (shared generic schema).
    return load_schema_for_bank(Bank.GENERIC)
