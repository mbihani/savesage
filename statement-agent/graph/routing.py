"""Bank -> prompt resolution (stdlib-only, unit-testable).

The caller PASSES the bank; there is no auto-detection and none is added. AXIS
intentionally resolves to the generic Luna prompt (``prompts/axis.txt``) -- that
is correct, not a bug, and must not be "fixed".

This module is a thin wrapper over :data:`rules.routing.PROMPT_BY_BANK` that
loads the prompt text and validates it is non-empty, keeping file I/O in one
place so the graph node and the skill share a single code path.

:func:`get_prompt_version` returns a short, stable version id (``<BANK>:<sha256[:8]>``)
for the prompt TEXT passed to it (the caller hands in exactly what was traced/sent)
so each MLflow run/trace can be tagged with the exact prompt version that produced
it -- the version changes whenever the prompt text changes.
"""

import hashlib

from contracts.models import Bank
from harness.dbfs import (
    bank_prompt_dbfs_path,
    read_dbfs_registry,
    read_dbfs_text,
)
from rules.routing import PROMPT_BY_BANK


class RoutingError(RuntimeError):
    """Raised when a bank cannot be resolved to a non-empty prompt."""


def try_bank(bank: Bank | str) -> Bank:
    """Normalise ``bank`` to a :class:`Bank` enum, falling back to GENERIC.

    Accepts a :class:`Bank` enum (returned as-is) or an arbitrary string.
    Known bank strings (``"HDFC"``, ``"ICICI"``, …) map to their enum; any
    unknown string maps to :data:`Bank.GENERIC` so the generic prompt/schema
    is used.  This lets the UI and API accept arbitrary bank names without
    changing the closed :class:`Bank` enum.
    """
    if isinstance(bank, Bank):
        return bank
    try:
        return Bank(bank)
    except (ValueError, TypeError):
        return Bank.GENERIC


def coerce_request_bank(bank: Bank | str) -> Bank | str:
    """Normalise ``bank`` to its real identity for a parse request.

    Unlike :func:`try_bank` (which collapses unknown names to GENERIC), this
    preserves a dynamically added bank's name as a plain string so the
    routing layer can resolve its DBFS prompt/schema. Built-in bank strings
    map to their :class:`Bank` enum; the name is upper-cased and trimmed.
    """
    if isinstance(bank, Bank):
        return bank
    name = str(bank).strip().upper()
    if not name:
        return Bank.GENERIC
    try:
        return Bank(name)
    except (ValueError, TypeError):
        return name  # dynamic / unknown — keep the real name


def effective_bank(bank: Bank | str) -> Bank | str:
    """Return the effective bank, reverting completely unknown banks to GENERIC.

    Unlike :func:`coerce_request_bank` (which preserves an unknown name as a
    plain string so the routing layer can resolve its DBFS prompt/schema), this
    collapses a bank that is **neither a built-in :class:`Bank` nor a registered
    dynamic bank** to :data:`Bank.GENERIC`.  It is the identity for known banks:

    * a :class:`Bank` enum is returned as-is;
    * a built-in bank string (``"HDFC"``, …) maps to its enum;
    * a string present in the DBFS registry (a dynamically added bank) is kept
      as-is so its own prompt/schema/name is preserved;
    * anything else (a completely unknown bank that :func:`resolve_prompt` would
      serve the GENERIC prompt for) maps to :data:`Bank.GENERIC`.

    Used by :func:`graph.nodes.route_node` and the ``/api/v1/parse`` route so
    downstream nodes, traces, and the API response all report the bank that was
    actually used — not the unknown name the caller passed.
    """
    if isinstance(bank, Bank):
        return bank
    bank_str = str(bank).strip().upper()
    if not bank_str:
        return Bank.GENERIC
    try:
        return Bank(bank_str)
    except (ValueError, TypeError):
        pass
    if bank_str in read_dbfs_registry():
        return bank_str  # registered dynamic bank — keep its real name
    return Bank.GENERIC


def detect_bank(text: str) -> str:
    """Detect a bank name from free text (e.g. a statement header).

    Checks built-in bank names first (substring, case-insensitive), then the
    DBFS registry of dynamically added banks. Returns the matched bank name
    string (e.g. ``"HDFC"``, ``"KOTAK"``), or :data:`Bank.GENERIC.value`
    (``"GENERIC"``) when nothing matches. The caller PASSES the bank in the
    normal flow; this is a utility for callers that only have raw text.
    """
    text = str(text).strip().upper()
    if not text:
        return Bank.GENERIC.value
    # Built-in banks (GENERIC is the fallback, not a pattern to match).
    for bank in Bank:
        if bank.value == Bank.GENERIC.value:
            continue
        if bank.value in text:
            return bank.value
    # Dynamically added banks from the DBFS registry.
    for name in read_dbfs_registry():
        normalized = name.strip().upper()
        if normalized and normalized in text:
            return normalized
    return Bank.GENERIC.value


def resolve_prompt(bank: Bank | str) -> str:
    """Return the non-empty prompt text for ``bank``.

    Accepts a :class:`Bank` enum or an arbitrary string. Resolution order:
    (1) shared config file ``banks/<BANK>/prompt.txt`` for any bank name;
    (2) built-in banks fall back to their bundled prompt if startup seeding did
    not succeed; (3) a registered dynamic bank whose prompt file is missing raises
    :class:`RoutingError`; (4) a completely unknown bank falls back to
    :data:`Bank.GENERIC` (the generic Luna prompt).  Loads from disk each call
    (prompts are small) so a prompt edit is picked up without a restart.
    """
    bank_str = bank.value if isinstance(bank, Bank) else str(bank).strip().upper()

    # 1. Shared bank config (works for both dynamic and built-in names).
    try:
        dbfs_text = read_dbfs_text(bank_prompt_dbfs_path(bank_str))
        if dbfs_text and dbfs_text.strip():
            return dbfs_text
    except (AttributeError, TypeError):
        pass  # bank is not a string-like; fall through to the built-in path

    # 2. Built-in bank: bundled fallback when startup seeding did not succeed.
    try:
        bank_enum = Bank(bank_str)
    except (ValueError, TypeError):
        bank_enum = None
    if bank_enum is not None:
        try:
            path = PROMPT_BY_BANK[bank_enum]
        except KeyError as exc:  # pragma: no cover - exhaustive enum, defensive
            raise RoutingError(f"no prompt mapped for bank {bank_enum!r}") from exc
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RoutingError(f"cannot read prompt {path}: {exc}") from exc
        if not text.strip():
            raise RoutingError(f"prompt for {bank_enum.value} is empty: {path}")
        return text

    # 3. Registered dynamic bank with missing/empty prompt -> loud error.
    if bank_str in read_dbfs_registry():
        raise RoutingError(
            f"dynamic bank {bank_str!r} is registered but its prompt file "
            f"is missing or empty on DBFS at {bank_prompt_dbfs_path(bank_str)}"
        )

    # 4. Completely unknown bank -> GENERIC fallback (generic Luna prompt).
    return resolve_prompt(Bank.GENERIC)


def get_prompt_version(prompt_text: str, bank: Bank | str) -> str:
    """Return a short, stable version id for ``prompt_text``.

    The id is ``<BANK>:<sha256[:8]>`` -- the bank's canonical name plus the first
    8 hex chars of the SHA-256 of ``prompt_text``. ``bank`` may be a
    :class:`Bank` enum (built-in) or a plain string (dynamically added bank);
    :func:`contracts.models.bank_name` extracts the name either way. The caller
    passes the EXACT prompt text that was traced/sent to the model (not the
    bank alone) so the version hashes what was actually used: this avoids the
    resolve-then-version race where :func:`resolve_prompt` is called twice
    (once for the trace text, once for the version) and the prompt file is
    edited between the two reads, leaving the traced text and the version
    silently disagreeing. ``bank`` is kept only to prefix the id. The version
    changes whenever the prompt text changes but is stable across runs that
    use the same text. Used to tag MLflow runs/spans with the exact prompt
    version that produced an extraction.
    """
    from contracts.models import bank_name

    digest = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:8]
    return f"{bank_name(bank)}:{digest}"


def resolve_prompt_for_all_banks() -> dict[Bank, str]:
    """Return every bank's prompt; used by tests and the manifest check."""
    return {bank: resolve_prompt(bank) for bank in Bank}
