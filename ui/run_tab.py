"""Run tab: choose source + instructions, start the agent, watch it, and review each step."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import streamlit as st

from testsmith.agent.loop import AgentLoop, SessionResult
from testsmith.agent.state import IterationRecord
from testsmith.config import has_credentials
from testsmith.llm.client import LLMClient
from testsmith.sandbox.runner import SandboxInputError
from ui.describe import describe, iteration_line, missing_text
from ui.inputs import SourceInput, check_source, render_instructions, render_source_picker
from ui.live import LiveView
from ui.session import RunOptions, base_settings, daily_budget, flash, run_settings, show_flash

STOP_STYLE = {
    "target_met": "success",
    "agent_finished": "info",
    "max_iterations": "warning",
    "stalled": "warning",
    "turn_limit": "warning",
    "invalid_actions": "error",
    "llm_error": "error",
}


def render_run_tab(options: RunOptions) -> None:
    settings = base_settings()
    left, right = st.columns([3, 2], gap="large")
    with left:
        source = render_source_picker(settings)
    with right:
        render_instructions(source)
        start = st.button(
            "Generate tests", type="primary", icon=":material/play_arrow:", width="stretch",
            disabled=source is None or not has_credentials(),
            help=None if has_credentials() else "No API key configured",
        )

    show_flash()
    if start and source is not None:
        _start_run(source, options)
    render_result(st.session_state.result)


# ---- running -----------------------------------------------------------------


def _start_run(source: SourceInput, options: RunOptions) -> None:
    settings = run_settings(options)
    check = check_source(source, settings)
    for error in check.errors:
        st.error(error)
    if not check.ok:
        return
    if check.warnings:
        st.warning(
            "This source uses risky operations (shown below). Tests run in a sandbox, "
            "but please make sure you trust this code.\n\n" + "\n".join(f"- {w}" for w in check.warnings)
        )

    live = LiveView(options.coverage_target, options.max_iterations)
    run_id = st.session_state.run_count + 1
    events: list[dict[str, Any]] = st.session_state.events

    def on_event(event: dict[str, Any]) -> None:
        event = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                 "session_id": st.session_state.session_id, "run": run_id, **event}
        events.append(event)
        live.handle(event)

    llm = LLMClient(settings, budgets=[st.session_state.session_budget, daily_budget()], on_call=on_event)
    loop = AgentLoop(llm, settings, on_event=on_event)
    started = time.monotonic()
    try:
        result = loop.run(
            module_name=source.module_name,
            source_code=source.code,
            instructions=st.session_state.instructions,
            coverage_target=options.coverage_target,
            max_iterations=options.max_iterations,
        )
    except SandboxInputError as exc:
        live.finish("Could not start", error=True)
        st.error(str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - never show a traceback; keep details in the audit log
        on_event({"event": "internal_error", "error": f"{type(exc).__name__}: {exc}"[:500]})
        live.finish("Something went wrong", error=True)
        st.error("Something unexpected went wrong while running the agent. Details were saved to the audit log.")
        return

    st.session_state.result = result
    st.session_state.last_source = source
    st.session_state.run_count = run_id
    kind = STOP_STYLE.get(result.stop_reason, "info")
    flash(kind, f"{result.message} (took {time.monotonic() - started:.0f}s)")
    st.rerun()


# ---- results -----------------------------------------------------------------


def render_result(result: SessionResult | None) -> None:
    if result is None:
        st.info("Pick a sample, upload, or paste a Python module, then click **Generate tests**.", icon=":material/info:")
        return
    state = result.state
    best = result.best

    st.subheader(f"Results for `{state.module_file}`")
    first_cov = state.iterations[0].run.coverage_percent if state.iterations else None
    final_cov = best.run.coverage_percent if best else 0.0
    cols = st.columns(5)
    cols[0].metric("Coverage", f"{final_cov:.1f}%",
                   delta=None if first_cov is None or best is None or best.number == 1 else f"{final_cov - first_cov:+.1f} pts")
    cols[1].metric("Tests passing", best.run.counts.passed if best else 0)
    cols[2].metric("Test runs", f"{state.runs_used} / {state.max_iterations}")
    cols[3].metric("Bugs found", len(state.bugs))
    cols[4].metric("Tokens", f"{result.usage.total_tokens:,}", help="Input, output, and prompt-cache tokens")
    st.progress(min(1.0, final_cov / 100), text=f"Final coverage {final_cov:.1f}% (target {state.coverage_target}%)")

    if state.bugs:
        st.warning(f"TestSmith found {len(state.bugs)} likely bug(s) in the source. See the **Bugs Found** tab.",
                   icon=":material/bug_report:")

    st.markdown("#### Agent steps")
    buckets = _steps_by_iteration(st.session_state.events, st.session_state.run_count)
    for it in state.iterations:
        _render_iteration(it, buckets.get(it.number, []), is_best=best is not None and it.number == best.number)
    trailing = buckets.get(None, [])
    if trailing:
        with st.expander("After the last run", expanded=False):
            for line in trailing:
                st.markdown(line)


def _render_iteration(it: IterationRecord, steps: list[str], is_best: bool) -> None:
    run = it.run
    failed = run.counts.failed + run.counts.errors
    icon = ":material/check_circle:" if failed == 0 and run.status == "passed" else ":material/cancel:"
    label = (f"Run {it.number} — coverage {run.coverage_percent:.1f}% · {run.counts.passed} passed · {failed} failed"
             + ("  ★ best" if is_best else ""))
    with st.expander(label, icon=icon, expanded=is_best):
        st.progress(min(1.0, run.coverage_percent / 100), text=f"Coverage {run.coverage_percent:.1f}%"
                    + ("" if it.coverage_delta is None else f" ({it.coverage_delta:+.1f} vs previous run)"))
        st.markdown("**Actions**")
        for line in steps:
            st.markdown(f"- {line}")
        if it.test_plan:
            st.markdown("**Test plan**")
            plan = it.test_plan
            plan_cols = st.columns(4)
            for col, (title, items) in zip(plan_cols, [("Happy path", plan.happy_path), ("Edge cases", plan.edge_cases),
                                                       ("Error cases", plan.error_cases), ("Boundaries", plan.boundaries)]):
                col.markdown(f"*{title}*\n" + "\n".join(f"- {i}" for i in items) if items else f"*{title}*\n- —")
        if it.rationale:
            st.markdown(f"**Rationale:** {it.rationale}")
        st.markdown(f"**Uncovered lines:** {missing_text(run.missing_lines)}")
        for failure in run.failures:
            st.markdown(f"**Failure:** `{failure.test_id}`")
            st.code(failure.traceback, language="text")
        if run.status in ("collection_error", "crashed", "timeout") and run.output_tail:
            st.code(run.output_tail[-1500:], language="text")


def _steps_by_iteration(events: list[dict[str, Any]], run_id: int) -> dict[int | None, list[str]]:
    """Group the last run's event lines by the test run they led up to."""
    buckets: dict[int | None, list[str]] = {}
    pending: list[str] = []
    for event in events:
        if event.get("run") != run_id:
            continue
        if event.get("event") == "iteration":
            pending.append(iteration_line(event))
            buckets[event["number"]] = pending
            pending = []
            continue
        line = describe(event)
        if line:
            pending.append(line)
    if pending:
        buckets[None] = pending
    return buckets
