"""Confirm/Cancel panel for runs that need explicit approval."""

from __future__ import annotations

import streamlit as st

from ui.session import audit


def render_confirmation_panel(fingerprint: str) -> bool:
    """Show the pending confirmation, if any. Returns True when the user approves it now.

    An approval is bound to the exact request (source, options, instructions):
    if any input changed since the request, the pending confirmation expires.
    """
    pending = st.session_state.pending
    if not pending:
        return False
    kinds = [r.kind.value for r in pending["requests"]]
    if pending["fingerprint"] != fingerprint:
        st.session_state.pending = None
        audit({"event": "confirmation", "decision": "expired", "kinds": kinds})
        return False

    placeholder = st.empty()
    with placeholder.container(border=True):
        st.markdown("#### Please confirm before running")
        for request in pending["requests"]:
            st.warning(f"**{request.title}**\n\n{request.detail}", icon=":material/warning:")
        confirm_col, cancel_col, _ = st.columns([1, 1, 2])
        confirmed = confirm_col.button("Confirm and run", type="primary", key="confirm_run", width="stretch")
        cancelled = cancel_col.button("Cancel", key="cancel_run", width="stretch")

    if confirmed:
        st.session_state.pending = None
        audit({"event": "confirmation", "decision": "approved", "kinds": kinds})
        placeholder.empty()
        return True
    if cancelled:
        st.session_state.pending = None
        audit({"event": "confirmation", "decision": "declined", "kinds": kinds})
        st.rerun()
    return False
