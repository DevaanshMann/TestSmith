"""Final Tests and Bugs Found tabs."""

from __future__ import annotations

import html

import streamlit as st

from testsmith.agent.loop import SessionResult
from testsmith.export.bundle import build_zip
from testsmith.safety.limits import BusyError
from testsmith.safety.validator import validate_tests
from testsmith.sandbox.runner import RunResult, run_tests
from ui.session import audit, base_settings, gate

_NO_RESULT = "Run TestSmith first; results appear here."


def render_final_tests_tab(result: SessionResult | None) -> None:
    if result is None or not result.final_tests:
        st.info(_NO_RESULT if result is None else "No test file was produced in the last run.")
        return
    state, best = result.state, result.best
    assert best is not None
    ss = st.session_state
    edited: str | None = ss.edited_tests
    current = edited if edited is not None else result.final_tests

    run = best.run
    st.markdown(
        f"Best generated suite: **run {best.number}** — {run.counts.passed} passed, "
        f"{run.counts.failed + run.counts.errors} failed, coverage **{run.coverage_percent:.1f}%**"
        + ("  ·  :orange[showing your edited version]" if edited is not None else "")
    )
    dl1, dl2, _ = st.columns([1, 1, 2])
    dl1.download_button(
        f"Download {state.test_filename}", data=current, file_name=state.test_filename,
        mime="text/x-python", icon=":material/download:", type="primary", width="stretch",
    )
    bundle = build_zip(result, ss.audit.to_jsonl(run=ss.run_count), edited)
    dl2.download_button(
        "Download everything (.zip)", data=bundle, file_name=f"testsmith_{state.module_name}.zip",
        mime="application/zip", icon=":material/folder_zip:", width="stretch",
        help="Tests, source, every run's version, RESULTS.md, results.json, and the audit log",
        on_click=lambda: audit({"event": "export", "format": "zip", "bytes": len(bundle)}, run=ss.run_count),
    )

    if st.toggle("Edit tests", key="edit_mode", help="Change the tests, re-run them in the sandbox, and download"):
        _render_editor(result, current)
    else:
        st.code(current, language="python", line_numbers=True)

    if ss.user_run is not None:
        _render_user_run(ss.user_run)

    st.markdown("#### Uncovered lines")
    shown = ss.user_run or run
    if not shown.missing_lines:
        st.success("Every line of the module is covered.", icon=":material/done_all:")
    else:
        st.caption("Lines of the source that no test executed" + (" (your version)." if ss.user_run else "."))
        st.markdown(uncovered_html(state.source_code, shown.missing_lines), unsafe_allow_html=True)


def _render_editor(result: SessionResult, current: str) -> None:
    ss = st.session_state
    state = result.state
    text = st.text_area("Test file", value=current, height=420, key=f"editor_{ss.run_count}",
                        label_visibility="collapsed")
    save, run_mine, discard, _ = st.columns([1, 1, 1, 2])
    if save.button("Save edits", icon=":material/save:", width="stretch"):
        _save_edits(result, text)
        st.rerun()
    if run_mine.button("Run my version", icon=":material/play_arrow:", width="stretch"):
        _save_edits(result, text)
        _run_user_tests(state.module_name, state.source_code, state.test_filename, text)
    if discard.button("Discard edits", icon=":material/undo:", width="stretch", disabled=ss.edited_tests is None):
        ss.edited_tests, ss.user_run = None, None
        ss.pop(f"editor_{ss.run_count}", None)
        audit({"event": "user_edit", "action": "discarded"}, run=ss.run_count)
        st.rerun()


def _save_edits(result: SessionResult, text: str) -> None:
    ss = st.session_state
    if text == result.final_tests:
        return
    if text != ss.edited_tests:
        ss.edited_tests = text
        audit({"event": "user_edit", "action": "saved", "chars": len(text)}, run=ss.run_count)


def _run_user_tests(module_name: str, source: str, test_filename: str, code: str) -> None:
    """Run the user's edited tests in the sandbox (no LLM call), after the same safety checks."""
    ss = st.session_state
    report = validate_tests(code, module_name, source)
    if report.blocked:
        st.error("Your version can't run until these are fixed:\n\n" + "\n".join(f"- {f}" for f in report.findings))
        audit({"event": "user_tests_rejected", "findings": [str(f) for f in report.findings]}, run=ss.run_count)
        return
    try:
        with gate().slot(), st.spinner("Running your tests in the sandbox…"):
            run = run_tests(module_name=module_name, source_code=source, test_filename=test_filename,
                            test_code=code, settings=base_settings())
    except BusyError as exc:
        st.warning(str(exc))
        return
    ss.user_run = run
    audit({"event": "user_tests_run", "status": run.status, "passed": run.counts.passed,
           "failed": run.counts.failed + run.counts.errors, "coverage": run.coverage_percent,
           "duration_s": run.duration_s}, run=ss.run_count)


def _render_user_run(run: RunResult) -> None:
    failed = run.counts.failed + run.counts.errors
    box = st.success if failed == 0 and run.status == "passed" else st.warning
    box(f"Your version: {run.counts.passed} passed, {failed} failed — coverage **{run.coverage_percent:.1f}%**"
        f" ({run.status.replace('_', ' ')})", icon=":material/person:")
    for failure in run.failures:
        st.markdown(f"`{failure.test_id}`")
        st.code(failure.traceback, language="text")
    if run.status in ("collection_error", "crashed", "timeout") and run.output_tail:
        st.code(run.output_tail[-1500:], language="text")


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
            if bug.source == "xfail_marker":
                st.caption("Found from the agent's xfail(BUG) marker in the final tests; the agent didn't call report_bug.")
            else:
                st.caption(f"Reported after test run {bug.iteration}" if bug.iteration else "Reported before the first test run")
