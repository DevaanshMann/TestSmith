"""Export domain: package a session's results as a zip archive.

Contents:
    test_<module>.py          the best test suite
    <module>.py               the source under test
    history/run<N>_test_<module>.py   the suite as it was for each test run
    RESULTS.md                summary: coverage per run, plan, bugs, stop reason
    results.json              the same data in machine-readable form
    audit_log.jsonl           this run's audit records
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Any

from testsmith.agent.feedback import compress_ranges
from testsmith.agent.loop import SessionResult


def build_zip(result: SessionResult, audit_jsonl: str = "", edited_tests: str | None = None) -> bytes:
    """Return the zip archive as bytes (kept in memory; nothing touches disk)."""
    state = result.state
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if result.final_tests:
            zf.writestr(state.test_filename, result.final_tests)
        if edited_tests:
            zf.writestr(f"edited_{state.test_filename}", edited_tests)
        zf.writestr(state.module_file, state.source_code)
        for it in state.iterations:
            zf.writestr(f"history/run{it.number}_{state.test_filename}", it.test_code)
        zf.writestr("RESULTS.md", results_markdown(result))
        zf.writestr("results.json", json.dumps(results_data(result), indent=2))
        if audit_jsonl:
            zf.writestr("audit_log.jsonl", audit_jsonl)
    return buffer.getvalue()


def results_data(result: SessionResult) -> dict[str, Any]:
    state, best = result.state, result.best
    return {
        "module": state.module_file,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stop_reason": result.stop_reason,
        "message": result.message,
        "coverage_target": state.coverage_target,
        "best_run": best.number if best else None,
        "runs": [
            {
                "run": it.number,
                "status": it.run.status,
                "passed": it.run.counts.passed,
                "failed": it.run.counts.failed,
                "errors": it.run.counts.errors,
                "xfailed_or_skipped": it.run.counts.skipped,
                "coverage": it.run.coverage_percent,
                "missing_lines": it.run.missing_lines,
            }
            for it in state.iterations
        ],
        "bugs": [
            {"function": b.function_name, "description": b.description, "evidence": b.evidence}
            for b in state.bugs
        ],
        "tokens": result.usage.total_tokens,
    }


def results_markdown(result: SessionResult) -> str:
    state, best = result.state, result.best
    lines = [
        f"# TestSmith results for `{state.module_file}`",
        "",
        f"- **Outcome:** {result.message}",
        f"- **Coverage target:** {state.coverage_target}%",
        "",
        "| Run | Status | Passed | Failed | Coverage | Uncovered lines |",
        "|---|---|---|---|---|---|",
    ]
    for it in state.iterations:
        star = " ★" if best and it.number == best.number else ""
        lines.append(
            f"| {it.number}{star} | {it.run.status} | {it.run.counts.passed} | "
            f"{it.run.counts.failed + it.run.counts.errors} | {it.run.coverage_percent:.1f}% | "
            f"{compress_ranges(it.run.missing_lines) or '—'} |"
        )
    if best and best.test_plan:
        plan = best.test_plan
        lines += ["", "## Test plan (best run)"]
        for title, items in (("Happy path", plan.happy_path), ("Edge cases", plan.edge_cases),
                             ("Error cases", plan.error_cases), ("Boundaries", plan.boundaries)):
            if items:
                lines += ["", f"**{title}**"] + [f"- {i}" for i in items]
    lines += ["", "## Bugs found"]
    if state.bugs:
        for bug in state.bugs:
            lines += ["", f"### `{bug.function_name}`", bug.description, "", "```", bug.evidence, "```"]
    else:
        lines.append("None reported.")
    return "\n".join(lines) + "\n"
