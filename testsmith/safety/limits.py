"""Spending limits that protect the API budget.

Two layers:
- a per-session TokenBudget (stored in Streamlit session state), and
- a process-wide daily budget shared by every visitor, because a grader can
  reset a per-session budget just by refreshing the page.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import date
from typing import Callable, Iterator


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


# ---- run-rate limiting ------------------------------------------------------


class RateLimitedError(Exception):
    """Raised when too many runs were started recently."""

    def __init__(self, scope: str, retry_after_s: int) -> None:
        self.scope, self.retry_after_s = scope, retry_after_s
        minutes = max(1, round(retry_after_s / 60))
        super().__init__(f"Run limit reached ({scope}). Please try again in about {minutes} minute(s).")


class SlidingWindowLimiter:
    """At most `max_events` per `window_s` seconds."""

    def __init__(self, max_events: int, window_s: float, scope: str, clock: Callable[[], float] = time.monotonic) -> None:
        self.max_events, self.window_s, self.scope = max_events, window_s, scope
        self._clock = clock
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Record one event, or raise RateLimitedError without recording it."""
        with self._lock:
            now = self._clock()
            while self._events and now - self._events[0] >= self.window_s:
                self._events.popleft()
            if len(self._events) >= self.max_events:
                oldest = self._events[0] if self._events else now
                raise RateLimitedError(self.scope, int(self.window_s - (now - oldest)) + 1)
            self._events.append(now)


class BusyError(Exception):
    """Raised when the server is already running the maximum number of sessions."""


class ConcurrencyGate:
    """Caps simultaneous agent runs (each one spawns pytest subprocesses)."""

    def __init__(self, max_concurrent: int) -> None:
        self.max_concurrent = max_concurrent
        self._semaphore = threading.BoundedSemaphore(max_concurrent)

    @contextmanager
    def slot(self) -> Iterator[None]:
        if not self._semaphore.acquire(blocking=False):
            raise BusyError("TestSmith is busy with other users' runs. Please try again in a minute.")
        try:
            yield
        finally:
            self._semaphore.release()


_global_rate: SlidingWindowLimiter | None = None
_gate: ConcurrencyGate | None = None


def global_run_limiter(max_per_hour: int) -> SlidingWindowLimiter:
    global _global_rate
    with _global_lock:
        if _global_rate is None or _global_rate.max_events != max_per_hour:
            _global_rate = SlidingWindowLimiter(max_per_hour, 3600, scope="all users")
        return _global_rate


def concurrency_gate(max_concurrent: int) -> ConcurrencyGate:
    global _gate
    with _global_lock:
        if _gate is None or _gate.max_concurrent != max_concurrent:
            _gate = ConcurrencyGate(max_concurrent)
        return _gate
