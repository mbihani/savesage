"""Shared timeout and bounded exponential retry policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    # Per-attempt request timeout (seconds) and attempt cap. Defaults match the
    # config defaults (REQUEST_TIMEOUT_SECONDS / MAX_ATTEMPTS) so a bare
    # ``RetryPolicy()`` and the config-wired policy agree on the budget. The
    # small budget (2 attempts x 60s) keeps a stuck Luna call from ballooning the
    # total wall time — a runaway retry ladder was one contributor to requests
    # outliving the sync API timeout.
    timeout_seconds: float = 60.0
    max_attempts: int = 2
    initial_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0
    retry_statuses: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})

    @classmethod
    def from_settings(cls, settings: Any) -> "RetryPolicy":
        """Build a policy from ``config.Settings`` — the SINGLE wiring point.

        Reads ``request_timeout_seconds`` (per-attempt timeout) and
        ``max_attempts`` from settings so the operator-facing
        ``REQUEST_TIMEOUT_SECONDS`` / ``MAX_ATTEMPTS`` env vars actually govern
        the extraction adapter's retry behaviour (previously ignored — the
        adapter built a hardcoded default policy).
        """
        return cls(
            timeout_seconds=float(settings.request_timeout_seconds),
            max_attempts=int(settings.max_attempts),
        )

    def backoff_for_attempt(self, attempt: int) -> float:
        if attempt < 1:
            raise ValueError("attempt is one-based")
        return min(self.initial_backoff_seconds * (2 ** (attempt - 1)), self.max_backoff_seconds)

    def total_budget_seconds(self) -> float:
        """Upper bound on wall time for the full retry ladder (attempts + backoffs).

        ``max_attempts`` request timeouts plus the ``max_attempts - 1`` backoff
        sleeps between them. Used to align the synchronous API timeout with the
        adapter's own budget so the worker cannot outlive the API wait (no
        orphaned thread still retrying after the caller gave up).
        """
        attempts_time = self.timeout_seconds * self.max_attempts
        backoff_time = sum(
            self.backoff_for_attempt(a) for a in range(1, self.max_attempts)
        )
        return attempts_time + backoff_time
