"""Shared timeout and bounded exponential retry policy."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    timeout_seconds: float = 180.0
    max_attempts: int = 4
    initial_backoff_seconds: float = 1.0
    max_backoff_seconds: float = 30.0
    retry_statuses: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})

    def backoff_for_attempt(self, attempt: int) -> float:
        if attempt < 1:
            raise ValueError("attempt is one-based")
        return min(self.initial_backoff_seconds * (2 ** (attempt - 1)), self.max_backoff_seconds)
