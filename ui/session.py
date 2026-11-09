"""Per-browser-session state, settings, and credential loading."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any

import streamlit as st

from testsmith.config import Settings
from testsmith.safety.limits import TokenBudget, global_daily_budget

MODEL_CHOICES = {
    "claude-sonnet-5-5": "Claude Sonnet 5.5 (balanced, default)",
    "claude-opus-5-5": "Claude Opus 5.5 (most capable, costlier)",
    "claude-haiku-5-5": "Claude Haiku 5.5 (fastest, cheapest)",
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
    """Create per-session defaults once."""
    state = st.session_state
    if "session_id" in state:
        return
    settings = base_settings()
    state.session_id = uuid.uuid4().hex[:12]
    state.session_budget = TokenBudget(settings.session_token_budget, scope="session")
    state.events = []          # every event of every run in this session (audit tab)
    state.result = None        # SessionResult of the last run
    state.last_source = None   # SourceInput of the last run
    state.run_count = 0
    state.flash = None         # (kind, message) shown once after a rerun
    state.instructions = ""


def daily_budget() -> TokenBudget:
    return global_daily_budget(base_settings().daily_token_budget)


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
