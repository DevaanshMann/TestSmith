"""Human-readable one-liners for agent events, shared by the live view and the run history."""

from __future__ import annotations

from typing import Any

from testsmith.agent.feedback import compress_ranges


def describe(event: dict[str, Any]) -> str | None:
    """Markdown line for an event, or None if it isn't worth showing to the user."""
    kind = event.get("event")
    if kind == "llm_call":
        return _llm(event)
    if kind == "validation" and not event.get("valid"):
        tool = event.get("tool") or "a reply without a tool call"
        errors = event.get("errors") or ["no tool was called"]
        return f":orange[:material/shield:] Rejected `{tool}`: {errors[0]} — sent back to Claude to correct"
    if kind == "action_finished":
        return _action(event)
    if kind == "iteration":
        return iteration_line(event)
    return None


def iteration_line(event: dict[str, Any]) -> str:
    delta = event.get("delta")
    delta_text = "" if delta is None else f" ({delta:+.1f})"
    failed = event.get("failed", 0)
    ok = failed == 0 and event.get("status") == "passed"
    status = ":green[✓ all passing]" if ok else f":red[✗ {failed} failing]"
    extra = f" · {event['xfailed_or_skipped']} xfail" if event.get("xfailed_or_skipped") else ""
    return (
        f":material/play_circle: **Run {event['number']}** · {status} · {event.get('passed', 0)} passed{extra} · "
        f"coverage **{event.get('coverage', 0):.1f}%**{delta_text}"
    )


def _llm(event: dict[str, Any]) -> str | None:
    if event.get("outcome") == "retry":
        return f":orange[:material/hourglass_top:] API {event.get('error_kind', 'error')}; retrying in {event.get('retry_in_s', '?')}s"
    if event.get("outcome") == "error":
        return f":red[:material/error:] API call failed ({event.get('error_kind', 'error')})"
    return None


def _action(event: dict[str, Any]) -> str | None:
    tool, ok = event.get("tool"), event.get("outcome") == "ok"
    if not ok:
        return f":orange[:material/block:] `{tool}` was refused: {event.get('summary', '')}"
    if tool == "read_source":
        return ":material/description: Read the source file"
    if tool == "write_tests":
        return f":material/edit_note: Wrote the test file ({event.get('tests', '?')} tests)"
    if tool == "report_bug":
        return ":orange[:material/bug_report:] Reported a likely bug in the source"
    if tool == "finish":
        return ":material/flag: Agent called finish"
    return None  # run_tests is summarised by the iteration event


def missing_text(lines: list[int]) -> str:
    return compress_ranges(lines) if lines else "none"
