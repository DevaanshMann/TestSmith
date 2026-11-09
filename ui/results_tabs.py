"""Final Tests and Bugs Found tabs."""

from __future__ import annotations

import html

import streamlit as st

from testsmith.agent.loop import SessionResult

_NO_RESULT = "Run TestSmith first; results appear here."


def render_final_tests_tab(result: SessionResult | None) -> None:
    if result is None or not result.final_tests:
        st.info(_NO_RESULT if result is None else "No test file was produced in the last run.")
        return
    state, best = result.state, result.best
    assert best is not None
    run = best.run
    st.markdown(
        f"Best suite: **run {best.number}** — {run.counts.passed} passed, "
        f"{run.counts.failed + run.counts.errors} failed, coverage **{run.coverage_percent:.1f}%**"
    )
    st.download_button(
        f"Download {state.test_filename}", data=result.final_tests, file_name=state.test_filename,
        mime="text/x-python", icon=":material/download:", type="primary",
    )
    st.code(result.final_tests, language="python", line_numbers=True)

    st.markdown("#### Uncovered lines")
    if not run.missing_lines:
        st.success("Every line of the module is covered.", icon=":material/done_all:")
    else:
        st.caption("Lines of the source that no test executed.")
        st.markdown(uncovered_html(state.source_code, run.missing_lines), unsafe_allow_html=True)


def uncovered_html(source: str, missing: list[int]) -> str:
    """Highlighted, escaped list of uncovered source lines."""
    lines = source.splitlines()
    rows = []
    for n in missing:
        text = html.escape(lines[n - 1]) if 0 < n <= len(lines) else ""
        rows.append(
            f'<div style="font-family:monospace;white-space:pre;background:#fdecea;border-left:3px solid #d93025;'
            f'padding:1px 8px;margin:1px 0"><span style="color:#888;display:inline-block;width:3.5em">{n}</span>{text}</div>'
        )
    return "\n".join(rows)


def render_bugs_tab(result: SessionResult | None) -> None:
    if result is None:
        st.info(_NO_RESULT)
        return
    bugs = result.state.bugs
    if not bugs:
        st.success("No source bugs were reported in the last run.", icon=":material/verified:")
        return
    st.markdown(
        "When a correct test fails because the **source** is wrong, TestSmith reports the bug instead of "
        "weakening the test, and keeps the test marked `xfail(strict=True)` so it documents the bug."
    )
    for bug in bugs:
        with st.container(border=True):
            st.warning(f"**`{bug.function_name}`** — {bug.description}", icon=":material/bug_report:")
            st.markdown("**Evidence**")
            st.code(bug.evidence, language="text")
            st.caption(f"Reported after test run {bug.iteration}" if bug.iteration else "Reported before the first test run")
