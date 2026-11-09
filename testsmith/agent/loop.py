"""The agent loop: generate -> run -> analyze -> refine, until a stop condition holds.

Each turn:
1. ask the model for actions (validated, with self-correction: parser.request_actions);
2. execute them in order (executor) and send the results back as tool_results;
3. check stop conditions.

Stop conditions, checked after every turn:
- target_met:      last run has no failures and coverage >= target
- agent_finished:  the model called `finish`
- max_iterations:  every test run in the budget has been used
- stalled:         `stall_limit` consecutive runs with no progress
- turn_limit:      too many model turns (guards against loops that never run tests)
- invalid_actions / llm_error: unrecoverable errors, reported in friendly words
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from testsmith.agent.executor import Executor, Runner
from testsmith.agent.parser import ActionParseError, ParseContext, request_actions
from testsmith.agent.state import IterationRecord, SessionState
from testsmith.agent.workspace import Workspace
from testsmith.config import Settings
from testsmith.llm.client import LLMClient, LLMError, UsageTracker
from testsmith.llm.prompts import load_prompt, render_prompt
from testsmith.sandbox.runner import check_module_name, run_tests

StopReason = Literal[
    "target_met", "agent_finished", "max_iterations", "stalled", "turn_limit", "invalid_actions", "llm_error"
]
EventHook = Callable[[dict[str, Any]], None]

MAX_INSTRUCTION_CHARS = 1000

STOP_MESSAGES: dict[str, str] = {
    "target_met": "Coverage target reached with all tests passing.",
    "agent_finished": "The agent finished: {summary}",
    "max_iterations": "Used all {max_iterations} test runs. Showing the best suite found.",
    "stalled": "Stopped: the last {stall_limit} runs made no progress. Showing the best suite found.",
    "turn_limit": "Stopped: the agent took too many steps without finishing.",
}


@dataclass
class SessionResult:
    state: SessionState
    stop_reason: StopReason
    message: str
    usage: UsageTracker

    @property
    def best(self) -> IterationRecord | None:
        return self.state.best_iteration()

    @property
    def final_tests(self) -> str | None:
        best = self.best
        return best.test_code if best else None

    @property
    def is_error(self) -> bool:
        return self.stop_reason in ("invalid_actions", "llm_error")


def build_system_prompt() -> str:
    return render_prompt("system", few_shot=load_prompt("fewshot"))


class AgentLoop:
    def __init__(
        self,
        llm: LLMClient,
        settings: Settings,
        *,
        runner: Runner = run_tests,
        on_event: EventHook | None = None,
        stall_limit: int = 2,
    ) -> None:
        self.llm = llm
        self.settings = settings
        self.runner = runner
        self.emit = on_event or (lambda _e: None)
        self.stall_limit = stall_limit

    def run(
        self,
        *,
        module_name: str,
        source_code: str,
        instructions: str = "",
        coverage_target: int | None = None,
        max_iterations: int | None = None,
    ) -> SessionResult:
        """Run a full session. Raises SandboxInputError only for an unusable module name."""
        check_module_name(module_name)
        state = SessionState(
            module_name=module_name,
            source_code=source_code,
            coverage_target=coverage_target or self.settings.coverage_target,
            max_iterations=min(max_iterations or self.settings.max_iterations, self.settings.hard_max_iterations),
            instructions=instructions.strip()[:MAX_INSTRUCTION_CHARS],
        )
        workspace = Workspace()
        try:
            workspace.write_source(state.module_file, source_code)
            return self._run(state, workspace)
        finally:
            workspace.cleanup()

    # ---- internals -----------------------------------------------------------

    def _run(self, state: SessionState, workspace: Workspace) -> SessionResult:
        executor = Executor(state, workspace, self.settings, runner=self.runner, on_event=self.emit)
        ctx = ParseContext(module_name=state.module_name, source_code=state.source_code)
        system = build_system_prompt()
        messages: list[dict[str, Any]] = [{"role": "user", "content": self._task_prompt(state)}]
        self.emit({
            "event": "session_started", "module": state.module_file, "target": state.coverage_target,
            "max_iterations": state.max_iterations, "model": self.settings.model,
        })

        for turn in range(self._turn_limit(state)):
            runs_before = state.runs_used
            try:
                outcome = request_actions(self.llm, system=system, messages=messages, ctx=ctx, on_event=self.emit)
            except ActionParseError as exc:
                return self._end(state, "invalid_actions", exc.user_message)
            except LLMError as exc:
                return self._end(state, "llm_error", exc.user_message)

            results = [executor.execute(call).as_tool_result() for call in outcome.calls]
            messages.append({"role": "user", "content": results})

            if state.runs_used > runs_before:
                self._emit_iteration(state)
            reason = self._stop_reason(state)
            if reason:
                return self._end(state, reason)
        return self._end(state, "turn_limit")

    def _task_prompt(self, state: SessionState) -> str:
        return render_prompt(
            "task",
            module_file=state.module_file,
            test_file=state.test_filename,
            target=state.coverage_target,
            max_iterations=state.max_iterations,
            instructions=state.instructions or "(none)",
        )

    def _turn_limit(self, state: SessionState) -> int:
        # read + (write, run, maybe report_bug) per iteration, plus slack
        return 4 * state.max_iterations + 4

    def _stop_reason(self, state: SessionState) -> StopReason | None:
        if state.target_met():
            return "target_met"
        if state.finished_by_agent:
            return "agent_finished"
        if state.runs_left == 0:
            return "max_iterations"
        if self._stalled(state):
            return "stalled"
        return None

    def _stalled(self, state: SessionState) -> bool:
        its = state.iterations
        if len(its) <= self.stall_limit:
            return False
        recent = its[-(self.stall_limit + 1):]
        return not any(_progressed(prev, cur) for prev, cur in zip(recent, recent[1:]))

    def _emit_iteration(self, state: SessionState) -> None:
        it = state.last
        assert it is not None
        self.emit({
            "event": "iteration",
            "number": it.number,
            "status": it.run.status,
            "passed": it.run.counts.passed,
            "failed": it.run.counts.failed + it.run.counts.errors,
            "xfailed_or_skipped": it.run.counts.skipped,
            "coverage": it.run.coverage_percent,
            "delta": it.coverage_delta,
            "missing_lines": it.run.missing_lines,
            "duration_s": it.run.duration_s,
            "limits": it.run.limits,
        })

    def _end(self, state: SessionState, reason: StopReason, error_message: str | None = None) -> SessionResult:
        message = error_message or STOP_MESSAGES[reason].format(
            summary=state.finish_summary, max_iterations=state.max_iterations, stall_limit=self.stall_limit
        )
        best = state.best_iteration()
        self.emit({
            "event": "session_finished",
            "stop_reason": reason,
            "message": message,
            "runs": state.runs_used,
            "best_coverage": best.run.coverage_percent if best else None,
            "bugs": len(state.bugs),
            "input_tokens": self.llm.usage.input_tokens,
            "output_tokens": self.llm.usage.output_tokens,
        })
        return SessionResult(state, reason, message, self.llm.usage)


def _progressed(prev: IterationRecord, cur: IterationRecord) -> bool:
    return cur.run.coverage_percent > prev.run.coverage_percent + 0.05 or cur.problems < prev.problems
