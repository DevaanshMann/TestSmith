"""Mutable state of one TestSmith session."""

from __future__ import annotations

from dataclasses import dataclass, field

from testsmith.agent.actions import TestPlan
from testsmith.sandbox.runner import RunResult


@dataclass
class IterationRecord:
    """One write -> run cycle."""

    number: int
    run: RunResult
    test_code: str
    test_plan: TestPlan | None
    rationale: str
    coverage_delta: float | None  # None on the first run

    @property
    def problems(self) -> int:
        return self.run.counts.failed + self.run.counts.errors + (0 if self.run.status in ("passed", "failed") else 1)


@dataclass
class BugReport:
    function_name: str
    description: str
    evidence: str
    iteration: int


@dataclass
class SessionState:
    module_name: str
    source_code: str
    coverage_target: int
    max_iterations: int
    instructions: str = ""
    test_code: str | None = None
    test_plan: TestPlan | None = None
    rationale: str = ""
    iterations: list[IterationRecord] = field(default_factory=list)
    bugs: list[BugReport] = field(default_factory=list)
    finished_by_agent: bool = False
    finish_summary: str = ""

    @property
    def module_file(self) -> str:
        return f"{self.module_name}.py"

    @property
    def test_filename(self) -> str:
        return f"test_{self.module_name}.py"

    @property
    def runs_used(self) -> int:
        return len(self.iterations)

    @property
    def runs_left(self) -> int:
        return max(0, self.max_iterations - self.runs_used)

    @property
    def last(self) -> IterationRecord | None:
        return self.iterations[-1] if self.iterations else None

    def target_met(self) -> bool:
        last = self.last
        return bool(last and last.problems == 0 and last.run.coverage_percent >= self.coverage_target)

    def best_iteration(self) -> IterationRecord | None:
        """Prefer green suites, then higher coverage, then the later run."""
        if not self.iterations:
            return None
        return max(self.iterations, key=lambda it: (it.problems == 0, it.run.coverage_percent, it.number))
