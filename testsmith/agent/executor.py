"""Dispatches validated actions to their handlers across domains:

- file operations: read_source / write_tests (Workspace)
- process execution + coverage analysis: run_tests (sandbox runner)
- reporting: report_bug, finish (session state)

Each handler returns a ToolOutcome whose text goes back to the model as a
tool_result. Handler errors become is_error results, never exceptions.
"""

from __future__ import annotations

import ast
import time
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any, Callable

from testsmith.agent.actions import Finish, ReadSource, ReportBug, RunTests, WriteTests, _Action
from testsmith.agent.bugs import unreported_bugs
from testsmith.agent.feedback import build_feedback
from testsmith.agent.parser import ParsedCall
from testsmith.agent.state import BugReport, IterationRecord, SessionState
from testsmith.agent.workspace import Workspace
from testsmith.config import Settings
from testsmith.sandbox.runner import RunResult, SandboxInputError, run_tests

Runner = Callable[..., RunResult]
EventHook = Callable[[dict[str, Any]], None]


@dataclass
class ToolOutcome:
    tool_use_id: str
    tool: str
    content: str
    is_error: bool = False
    data: dict[str, Any] = field(default_factory=dict)

    def as_tool_result(self) -> dict[str, Any]:
        block: dict[str, Any] = {"type": "tool_result", "tool_use_id": self.tool_use_id, "content": self.content}
        if self.is_error:
            block["is_error"] = True
        return block


class ActionRejected(Exception):
    """A handler refused the action; the message goes back to the model."""


class Executor:
    def __init__(
        self,
        state: SessionState,
        workspace: Workspace,
        settings: Settings,
        *,
        runner: Runner = run_tests,
        on_event: EventHook | None = None,
    ) -> None:
        self.state = state
        self.workspace = workspace
        self.settings = settings
        self.runner = runner
        self.on_event = on_event or (lambda _e: None)
        self._handlers: dict[type[_Action], Callable[[Any], tuple[str, dict[str, Any]]]] = {
            ReadSource: self._read_source,
            WriteTests: self._write_tests,
            RunTests: self._run_tests,
            ReportBug: self._report_bug,
            Finish: self._finish,
        }

    def execute(self, call: ParsedCall) -> ToolOutcome:
        """Run one validated action. Never raises for expected failures."""
        assert call.action is not None, "only validated calls may be executed"
        action = call.action
        args = summarize_args(action)
        self.on_event({"event": "action_started", "tool": call.name, "args": args})
        started = time.monotonic()
        try:
            content, data = self._handlers[type(action)](action)
            outcome = ToolOutcome(call.tool_use_id, call.name, content, data=data)
        except ActionRejected as exc:
            outcome = ToolOutcome(call.tool_use_id, call.name, str(exc), is_error=True)
        except Exception as exc:  # noqa: BLE001 - surfaced to model and UI, logged for audit
            outcome = ToolOutcome(
                call.tool_use_id, call.name, "Internal error while executing this action; try again.", is_error=True,
                data={"internal_error": f"{type(exc).__name__}: {exc}"[:500]},
            )
        self.on_event({
            "event": "action_finished",
            "tool": call.name,
            "args": args,
            "validation": "passed",  # only validated calls reach the executor; rejections are logged by the parser
            "outcome": "error" if outcome.is_error else "ok",
            "duration_ms": int((time.monotonic() - started) * 1000),
            "summary": outcome.content[:200],
            **{k: v for k, v in outcome.data.items() if k in ("coverage", "passed", "failed", "status", "tests", "internal_error")},
        })
        return outcome

    # ---- handlers -----------------------------------------------------------

    def _read_source(self, action: ReadSource) -> tuple[str, dict[str, Any]]:
        if PurePath(action.path).name != self.state.module_file:
            raise ActionRejected(f"Only {self.state.module_file} can be read in this session.")
        source = self.workspace.read_source(self.state.module_file)
        numbered = "\n".join(f"{i:>4} | {line}" for i, line in enumerate(source.splitlines(), start=1))
        return f"{self.state.module_file} ({len(source.splitlines())} lines):\n{numbered}", {}

    def _write_tests(self, action: WriteTests) -> tuple[str, dict[str, Any]]:
        if action.filename != self.state.test_filename:
            raise ActionRejected(f"Use the filename {self.state.test_filename}.")
        version = self.state.runs_used + 1
        self.workspace.write_tests(action.filename, action.content, version)
        self.state.test_code = action.content
        self.state.test_plan = action.test_plan
        self.state.rationale = action.rationale
        n_tests = count_tests(action.content)
        message = f"Saved {action.filename}: {n_tests} test functions, {len(action.content.splitlines())} lines. Call run_tests next."
        missing = unreported_bugs(action.content, self.state.source_code, self.state.bugs, self.state.runs_used)
        if missing:
            names = ", ".join(b.function_name for b in missing)
            message += f" Note: tests are marked xfail as BUG for {names} but no report_bug was called; call report_bug now."
        return message, {"tests": n_tests}

    def _run_tests(self, _action: RunTests) -> tuple[str, dict[str, Any]]:
        state = self.state
        if state.test_code is None:
            raise ActionRejected("There is no test file yet. Call write_tests first.")
        if state.runs_left == 0:
            raise ActionRejected("No test runs left. Call finish with a summary.")
        try:
            run = self.runner(
                module_name=state.module_name,
                source_code=state.source_code,
                test_filename=state.test_filename,
                test_code=state.test_code,
                settings=self.settings,
            )
        except SandboxInputError as exc:
            raise ActionRejected(str(exc)) from exc
        previous = state.last.run.coverage_percent if state.last else None
        delta = None if previous is None else round(run.coverage_percent - previous, 1)
        state.iterations.append(
            IterationRecord(state.runs_used + 1, run, state.test_code, state.test_plan, state.rationale, delta)
        )
        data = {
            "status": run.status,
            "passed": run.counts.passed,
            "failed": run.counts.failed + run.counts.errors,
            "coverage": run.coverage_percent,
        }
        return build_feedback(state, run, delta), data

    def _report_bug(self, action: ReportBug) -> tuple[str, dict[str, Any]]:
        key = (action.function_name, action.description.lower())
        if not any((b.function_name, b.description.lower()) == key for b in self.state.bugs):
            self.state.bugs.append(
                BugReport(action.function_name, action.description, action.evidence, self.state.runs_used)
            )
        return (
            f"Bug in {action.function_name} recorded. Make sure its test is kept and marked "
            '@pytest.mark.xfail(strict=True, reason="BUG: ...").'
        ), {}

    def _finish(self, action: Finish) -> tuple[str, dict[str, Any]]:
        self.state.finished_by_agent = True
        self.state.finish_summary = action.summary
        return "Session finished.", {}


# ---- helpers ---------------------------------------------------------------


def count_tests(code: str) -> int:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return 0
    return sum(
        1 for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")
    )


def summarize_args(action: _Action) -> dict[str, Any]:
    """Short, log-safe summary of an action's arguments (no full file contents)."""
    if isinstance(action, WriteTests):
        plan = action.test_plan
        return {
            "filename": action.filename,
            "content_chars": len(action.content),
            "tests": count_tests(action.content),
            "plan_items": len(plan.happy_path) + len(plan.edge_cases) + len(plan.error_cases) + len(plan.boundaries),
        }
    return {k: (v[:120] + "…" if isinstance(v, str) and len(v) > 120 else v) for k, v in action.model_dump().items()}
