"""Builds the text the model sees after each test run (prompts/iteration_feedback_v1.md)."""

from __future__ import annotations

from testsmith.agent.state import SessionState
from testsmith.llm.prompts import render_prompt
from testsmith.sandbox.runner import RunResult

MAX_UNCOVERED_LINES_SHOWN = 60
MAX_OUTPUT_CHARS = 1500

_STATUS = {
    "passed": "all tests passing",
    "no_tests": "no tests were collected; make sure test functions start with test_",
    "collection_error": "the test file could not be imported or collected (see output below)",
    "timeout": "timed out; a test probably loops forever or is far too slow",
    "crashed": "the test process crashed, most likely by exceeding a resource limit",
}


def build_feedback(state: SessionState, run: RunResult, delta: float | None) -> str:
    """Render the iteration feedback prompt for one run."""
    return render_prompt(
        "iteration_feedback",
        iteration=state.runs_used,
        max_iterations=state.max_iterations,
        status_line=status_line(run),
        passed=run.counts.passed,
        failed=run.counts.failed,
        errors=run.counts.errors,
        skipped=run.counts.skipped,
        coverage=f"{run.coverage_percent:.1f}",
        target=state.coverage_target,
        delta="first run" if delta is None else f"{delta:+.1f}",
        failures_section=failures_section(run),
        uncovered_section=uncovered_section(state.source_code, run.missing_lines),
        guidance=guidance(state, run),
    )


def status_line(run: RunResult) -> str:
    if run.status == "failed":
        n = run.counts.failed + run.counts.errors
        return f"{n} test{'s' if n != 1 else ''} failing"
    return _STATUS.get(run.status, run.status)


def failures_section(run: RunResult) -> str:
    if run.failures:
        parts = ["Failures:"]
        for f in run.failures:
            body = "\n".join(f"  {line}" for line in f.traceback.splitlines())
            parts.append(f"- {f.test_id} ({f.kind})\n{body}")
        hidden = run.counts.failed + run.counts.errors - len(run.failures)
        if hidden > 0:
            parts.append(f"(+{hidden} more failures not shown)")
        return "\n".join(parts)
    if run.status in ("collection_error", "crashed", "no_tests") and run.output_tail:
        return "Output:\n" + run.output_tail[-MAX_OUTPUT_CHARS:]
    return "Failures: none"


def uncovered_section(source: str, missing: list[int]) -> str:
    if not missing:
        return "Uncovered lines: none"
    lines = source.splitlines()
    shown = missing[:MAX_UNCOVERED_LINES_SHOWN]
    out = [f"Uncovered lines ({compress_ranges(missing)}), with source:"]
    previous = None
    for n in shown:
        if previous is not None and n != previous + 1:
            out.append("  ...")
        text = lines[n - 1] if 0 < n <= len(lines) else ""
        out.append(f"  {n:>4} | {text}")
        previous = n
    if len(missing) > len(shown):
        out.append(f"  (+{len(missing) - len(shown)} more lines)")
    return "\n".join(out)


def compress_ranges(numbers: list[int]) -> str:
    """[1,2,3,7,9,10] -> '1-3, 7, 9-10'"""
    if not numbers:
        return ""
    ranges, start, prev = [], numbers[0], numbers[0]
    for n in numbers[1:]:
        if n == prev + 1:
            prev = n
            continue
        ranges.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = n
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return ", ".join(ranges)


def guidance(state: SessionState, run: RunResult) -> str:
    steps = []
    if run.status in ("collection_error", "no_tests", "timeout", "crashed"):
        steps.append("Fix the problem above first; nothing else can be measured until the suite runs.")
    if run.failures:
        steps.append(
            "For each failure, decide whether the test or the source is wrong. Fix wrong tests; "
            "for source bugs call report_bug and mark the test xfail(strict=True)."
        )
    if run.missing_lines and run.coverage_percent < state.coverage_target:
        steps.append(f"Add tests that execute lines {compress_ranges(run.missing_lines)}.")
    if not steps:
        steps.append("The target is met. Call finish with a short summary.")
    remaining = state.runs_left
    steps.append(f"{remaining} test run{'s' if remaining != 1 else ''} left." if remaining else "No test runs left.")
    return " ".join(steps)
