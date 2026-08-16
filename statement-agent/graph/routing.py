"""Bank -> prompt resolution (stdlib-only, unit-testable).

The caller PASSES the bank; there is no auto-detection and none is added. AXIS
intentionally resolves to the generic Luna prompt (``prompts/axis.txt``) -- that
is correct, not a bug, and must not be "fixed".

This module is a thin wrapper over :data:`rules.routing.PROMPT_BY_BANK` that
loads the prompt text and validates it is non-empty, keeping file I/O in one
place so the graph node and the skill share a single code path.

:func:`get_prompt_version` returns a short, stable version id (``<BANK>:<sha256[:8]>``)
for a bank's prompt so each MLflow run/trace can be tagged with the exact prompt
version that produced it -- the version changes whenever the prompt text changes.
"""

import hashlib

from contracts.models import Bank
from rules.routing import PROMPT_BY_BANK


class RoutingError(RuntimeError):
    """Raised when a bank cannot be resolved to a non-empty prompt."""


def resolve_prompt(bank: Bank) -> str:
    """Return the non-empty prompt text for ``bank``.

    Loads from disk each call (prompts are small) so a prompt edit during a long
    session is picked up without a process restart. Raises :class:`RoutingError`
    if the bank is unknown or the prompt file is empty/missing -- a bank with no
    prompt is a configuration defect, not something to silently route around.
    """
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


def get_prompt_version(bank: Bank) -> str:
    """Return a short, stable version id for ``bank``'s prompt.

    The id is ``<BANK>:<sha256[:8]>`` -- the bank enum value plus the first 8 hex
    chars of the resolved prompt's SHA-256. It changes whenever the prompt text
    changes (so two runs with different prompts get different ids) but is stable
    across runs that use the same prompt. Used to tag MLflow runs/spans with the
    exact prompt version that produced an extraction.
    """
    prompt_text = resolve_prompt(bank)
    digest = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:8]
    return f"{bank.value}:{digest}"


def resolve_prompt_for_all_banks() -> dict[Bank, str]:
    """Return every bank's prompt; used by tests and the manifest check."""
    return {bank: resolve_prompt(bank) for bank in Bank}
