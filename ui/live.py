"""Live progress while the agent runs: a status box with a step log and a coverage meter."""

from __future__ import annotations

from typing import Any

import streamlit as st

from ui.describe import describe

_WAITING = {
    "read_source": "Reading the source…",
    "write_tests": "Saving the test file…",
    "run_tests": "Running tests in the sandbox…",
}


class LiveView:
    def __init__(self, target: int, max_iterations: int) -> None:
        self.target = target
        self.max_iterations = max_iterations
        self.meter = st.progress(0.0, text=f"Coverage: waiting for the first test run (target {target}%)")
        self.status = st.status("Asking Claude to read the module…", expanded=True)

    def handle(self, event: dict[str, Any]) -> None:
        kind = event.get("event")
        line = describe(event)
        if line:
            self.status.write(line)
        if kind == "action_started":
            self.status.update(label=_WAITING.get(event.get("tool", ""), "Working…"))
        elif kind in ("action_finished", "validation"):
            self.status.update(label="Waiting for Claude's next step…")
        elif kind == "iteration":
            coverage = float(event.get("coverage", 0.0))
            self.meter.progress(
                min(1.0, coverage / 100),
                text=f"Coverage {coverage:.1f}% after run {event['number']} of {self.max_iterations} (target {self.target}%)",
            )

    def finish(self, label: str, error: bool) -> None:
        self.status.update(label=label, state="error" if error else "complete", expanded=False)
