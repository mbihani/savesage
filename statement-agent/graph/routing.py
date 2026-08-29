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
from harness.dbfs import prompt_dbfs_path, read_dbfs_text
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


def resolve_prompt(bank: Bank | str) -> str:
    """Return the non-empty prompt text for ``bank``.

    Accepts a :class:`Bank` enum or an arbitrary string; unknown strings
    fall back to :data:`Bank.GENERIC` (the generic Luna prompt) instead of
    raising :class:`RoutingError`.  Checks a DBFS override first
    (``/savesage/prompts/<bank>.txt``); if the SDK is unavailable or the
    file does not exist, falls back to the bundled file.  Loads from disk
    each call (prompts are small) so a prompt edit — whether on DBFS or
    the bundled file — is picked up without a process restart.  Raises
    :class:`RoutingError` if the prompt file is empty/missing.
    """
    bank = try_bank(bank)
    # DBFS override (best-effort; None when SDK unavailable or file missing).
    try:
        dbfs_text = read_dbfs_text(prompt_dbfs_path(bank.value))
        if dbfs_text and dbfs_text.strip():
            return dbfs_text
    except (AttributeError, TypeError):
        pass  # bank is not a Bank enum; fall through to the KeyError path
    try:
        path = PROMPT_BY_BANK[bank]
    except KeyError as exc:  # pragma: no cover - exhaustive enum, defensive
        raise RoutingError(f"no prompt mapped for bank {bank!r}") from exc
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RoutingError(f"cannot read prompt {path}: {exc}") from exc
    if not text.strip():
        raise RoutingError(f"prompt for {bank.value} is empty: {path}")
    return text


def get_prompt_version(prompt_text: str, bank: Bank) -> str:
    """Return a short, stable version id for ``prompt_text``.

    The id is ``<BANK>:<sha256[:8]>`` -- ``bank``'s enum value plus the first 8
    hex chars of the SHA-256 of ``prompt_text``. The caller passes the EXACT
    prompt text that was traced/sent to the model (not the bank alone) so the
    version hashes what was actually used: this avoids the resolve-then-version
    race where :func:`resolve_prompt` is called twice (once for the trace text,
    once for the version) and the prompt file is edited between the two reads,
    leaving the traced text and the version silently disagreeing. ``bank`` is
    kept only to prefix the id. The version changes whenever the prompt text
    changes but is stable across runs that use the same text. Used to tag MLflow
    runs/spans with the exact prompt version that produced an extraction.
    """
    digest = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:8]
    return f"{bank.value}:{digest}"


def resolve_prompt_for_all_banks() -> dict[Bank, str]:
    """Return every bank's prompt; used by tests and the manifest check."""
    return {bank: resolve_prompt(bank) for bank in Bank}
