"""Retry policy for local assessment jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class RetryPolicy:
    """Small retry policy for the v1 local queue."""

    max_attempts: int = 2
    base_delay_seconds: int = 60

    def should_retry(self, attempt: int) -> bool:
        """Return True when another attempt is allowed."""
        return attempt < self.max_attempts

    def next_not_before(self, attempt: int) -> str:
        """Return the earliest UTC time for the next retry."""
        delay = self.base_delay_seconds * max(attempt, 1)
        return (datetime.now(UTC) + timedelta(seconds=delay)).isoformat()

