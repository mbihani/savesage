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
from harness.dbfs import read_dbfs_text, schema_dbfs_path

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


def load_schema_for_bank(bank: Bank) -> dict:
    """Load and return the per-bank extraction schema (stdlib json).

    Checks a DBFS override first (``/savesage/schemas/<bank>.json``); if the
    SDK is unavailable or the file does not exist, falls back to the bundled
    file. Reads from disk each call (schemas are small, ~4-10KB) so a schema
    edit — whether on DBFS or the bundled file — is picked up without a
    process restart, mirroring :func:`graph.routing.resolve_prompt`.
    Raises ``KeyError`` if ``bank`` is not in :data:`SCHEMA_BY_BANK` (a
    configuration defect, not something to silently route around).
    """
    # DBFS override (best-effort; None when SDK unavailable or file missing).
    dbfs_text = read_dbfs_text(schema_dbfs_path(bank.value))
    if dbfs_text:
        try:
            return json.loads(dbfs_text)
        except json.JSONDecodeError:
            pass  # fall back to bundled file
    return json.loads(SCHEMA_BY_BANK[bank].read_text(encoding="utf-8"))
