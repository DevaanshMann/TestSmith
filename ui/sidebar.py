"""Sidebar: run settings, credential status, and token usage."""

from __future__ import annotations

import streamlit as st

from testsmith.config import has_credentials
from ui.session import EFFORT_CHOICES, MODEL_CHOICES, RunOptions, base_settings, daily_budget
from ui.theme import sidebar_brand


def render_sidebar() -> RunOptions:
    settings = base_settings()
    with st.sidebar:
        sidebar_brand()
        coverage_target = st.slider(
            "Coverage target (%)", min_value=50, max_value=100, value=settings.coverage_target, step=5,
            help="The agent stops once line coverage reaches this and no tests fail.",
        )
        max_iterations = st.number_input(
            "Max test runs", min_value=1, max_value=settings.hard_max_iterations, value=settings.max_iterations,
            help=f"Each run executes the suite once. Above {settings.max_iterations} costs more and needs confirmation.",
        )
        models = list(MODEL_CHOICES)
        default_model = settings.model if settings.model in MODEL_CHOICES else models[0]
        model = st.selectbox("Model", models, index=models.index(default_model), format_func=MODEL_CHOICES.get)
        effort_default = settings.effort if settings.effort in EFFORT_CHOICES else "medium"
        effort = st.segmented_control(
            "Effort", EFFORT_CHOICES, default=effort_default, required=True,
            help="How hard the model thinks per step. Higher is slower and uses more tokens.",
        )

        st.divider()
        _render_status()
    return RunOptions(model=model, effort=effort or effort_default, coverage_target=int(coverage_target),
                      max_iterations=int(max_iterations))


def _render_status() -> None:
    if has_credentials():
        st.badge("API key configured", icon=":material/check_circle:", color="green")
    else:
        st.badge("No API key configured", icon=":material/error:", color="red")
        st.caption("Set ANTHROPIC_API_KEY in .env (local) or the host's environment settings.")

    session = st.session_state.session_budget
    daily = daily_budget()
    st.caption("Token budget · this session")
    st.progress(min(1.0, session.used / session.limit), text=f"{session.used:,} / {session.limit:,} tokens")
    st.caption("Token budget · all users today")
    st.progress(min(1.0, daily.used / daily.limit), text=f"{daily.used:,} / {daily.limit:,} tokens")
    st.caption(f"Session `{st.session_state.session_id}`")
