"""Spending limits that protect the API budget.

Two layers:
- a per-session TokenBudget (stored in Streamlit session state), and
- a process-wide daily budget shared by every visitor, because a grader can
  reset a per-session budget just by refreshing the page.
"""

from __future__ import annotations

import threading
from datetime import date


class BudgetExceededError(Exception):
    """Raised when a token budget would be exceeded."""

    def __init__(self, scope: str, used: int, limit: int) -> None:
        self.scope, self.used, self.limit = scope, used, limit
        super().__init__(f"{scope} token budget exhausted ({used:,} of {limit:,} tokens used)")


class TokenBudget:
    """Thread-safe token counter with a hard limit."""

    def __init__(self, limit: int, scope: str = "session") -> None:
        self.limit = limit
        self.scope = scope
        self._used = 0
        self._lock = threading.Lock()

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self._used)

    def ensure_available(self) -> None:
        """Raise BudgetExceededError if nothing is left."""
        if self._used >= self.limit:
            raise BudgetExceededError(self.scope, self._used, self.limit)

    def consume(self, tokens: int) -> None:
        with self._lock:
            self._used += max(0, tokens)


class DailyTokenBudget(TokenBudget):
    """A TokenBudget that resets at local midnight."""

    def __init__(self, limit: int) -> None:
        super().__init__(limit, scope="daily (all users)")
        self._day = date.today()

    def _roll(self) -> None:
        today = date.today()
        if today != self._day:
            with self._lock:
                self._day, self._used = today, 0

    def ensure_available(self) -> None:
        self._roll()
        super().ensure_available()

    def consume(self, tokens: int) -> None:
        self._roll()
        super().consume(tokens)


_global_lock = threading.Lock()
_global_daily: DailyTokenBudget | None = None


def global_daily_budget(limit: int) -> DailyTokenBudget:
    """Process-wide singleton shared by all Streamlit sessions."""
    global _global_daily
    with _global_lock:
        if _global_daily is None or _global_daily.limit != limit:
            _global_daily = DailyTokenBudget(limit)
        return _global_daily
