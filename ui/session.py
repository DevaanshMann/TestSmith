"""Per-browser-session state, settings, and credential loading."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any

import streamlit as st

from testsmith.audit.log import AuditLogger
from testsmith.config import Settings
from testsmith.safety.limits import (
    ConcurrencyGate,
    SlidingWindowLimiter,
    TokenBudget,
    concurrency_gate,
    global_daily_budget,
    global_run_limiter,
)

MODEL_CHOICES = {
    "claude-sonnet-5-5": "Sonnet 5.5 · balanced",
    "claude-opus-5-5": "Opus 5.5 · most capable",
    "claude-haiku-5-5": "Haiku 5.5 · fastest",
}
EFFORT_CHOICES = ("low", "medium", "high")


@dataclass(frozen=True)
class RunOptions:
    """Choices made in the sidebar for the next run."""

    model: str
    effort: str
    coverage_target: int
    max_iterations: int


def load_streamlit_secrets() -> None:
    """Copy Streamlit secrets (Streamlit Community Cloud) into env vars, without overriding."""
    try:
        secrets = {k: v for k, v in st.secrets.items() if isinstance(v, str)}
    except Exception:  # noqa: BLE001 - no secrets.toml is the normal local case
        return
    for key, value in secrets.items():
        if key.isupper():
            os.environ.setdefault(key, value)


@st.cache_resource
def _base_settings() -> Settings:
    return Settings.from_env()


def base_settings() -> Settings:
    return _base_settings()


def init_session() -> None:
    """Create per-session defaults. Fills in any missing key, so sessions survive app upgrades."""
    state = st.session_state
    settings = base_settings()
    if "session_id" not in state:
        state.session_id = uuid.uuid4().hex[:12]
    defaults: dict[str, Any] = {
        "session_budget": lambda: TokenBudget(settings.session_token_budget, scope="session"),
        "run_limiter": lambda: SlidingWindowLimiter(settings.max_runs_per_session_per_hour, 3600, scope="this session"),
        "audit": lambda: AuditLogger(settings.audit_log_path, state.session_id),
        "pending": lambda: None,       # run request waiting for confirmation
        "edited_tests": lambda: None,  # user's edited version of the final tests
        "user_run": lambda: None,      # RunResult of the user's edited tests
        "result": lambda: None,        # SessionResult of the last run
        "last_source": lambda: None,
        "run_count": lambda: 0,
        "flash": lambda: None,         # (kind, message) shown once after a rerun
        "instructions": lambda: "",
    }
    for key, make in defaults.items():
        if key not in state:
            state[key] = make()
    state.events = state.audit.records  # same list: every event of every run in this session


def daily_budget() -> TokenBudget:
    return global_daily_budget(base_settings().daily_token_budget)


def global_limiter() -> SlidingWindowLimiter:
    return global_run_limiter(base_settings().max_runs_per_hour_global)


def gate() -> ConcurrencyGate:
    return concurrency_gate(base_settings().max_concurrent_runs)


def audit(event: dict[str, Any], **context: Any) -> dict[str, Any]:
    """Record an event in this session's audit log (file + memory)."""
    return st.session_state.audit.record(event, **context)


def run_settings(options: RunOptions) -> Settings:
    return base_settings().with_overrides(
        model=options.model,
        effort=options.effort,
        coverage_target=options.coverage_target,
        max_iterations=options.max_iterations,
    )


def flash(kind: str, message: str) -> None:
    st.session_state.flash = (kind, message)


def show_flash() -> None:
    item: Any = st.session_state.get("flash")
    if not item:
        return
    kind, message = item
    getattr(st, kind, st.info)(message)
    st.session_state.flash = None
