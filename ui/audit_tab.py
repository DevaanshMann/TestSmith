"""Audit Log tab: every LLM call, validation, and action from this browser session."""

from __future__ import annotations

import json
from typing import Any

import streamlit as st

COLUMNS = ["ts", "run", "event", "tool", "args", "validation", "valid", "outcome", "decision", "reason",
           "duration_ms", "latency_ms", "input_tokens", "output_tokens", "coverage", "error_kind", "summary"]


def render_audit_tab() -> None:
    events: list[dict[str, Any]] = st.session_state.events
    if not events:
        st.info("Nothing logged yet. Every LLM call, validation result, and action will appear here.")
        return
    kinds = sorted({e["event"] for e in events})
    chosen = st.multiselect("Event types", kinds, default=kinds)
    rows = [{c: _cell(e.get(c)) for c in COLUMNS} for e in events if e["event"] in chosen]
    st.dataframe(rows, hide_index=True, width="stretch")
    st.download_button(
        "Download audit log (JSONL)", data=st.session_state.audit.to_jsonl(),
        file_name=f"testsmith_audit_{st.session_state.session_id}.jsonl",
        mime="application/x-ndjson", icon=":material/download:",
    )
    st.caption(
        f"{len(events)} records from this browser session. They are also appended to the server's audit file "
        "(`TESTSMITH_AUDIT_LOG`). API keys are never logged, and file contents are logged only as short summaries."
    )


def _cell(value: Any) -> Any:
    if isinstance(value, (list, dict)):
        return json.dumps(value, default=str)[:200]
    return value
