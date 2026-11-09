"""Runs pytest + coverage on generated tests in an isolated, resource-limited subprocess.

Isolation per run:
- a fresh temp directory (deleted afterwards) as the working directory and HOME;
- an allowlisted environment, so secrets such as ANTHROPIC_API_KEY never reach it;
- CPU / memory / file-size / process limits (where the OS supports them);
- a wall-clock timeout that kills the whole process group;
- outbound network blocked by an injected sitecustomize.py;
- stdout/stderr written to size-limited files, not unbounded pipes.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from testsmith.config import Settings
from testsmith.sandbox.report import CoverageData, Failure, TestCounts, parse_coverage, parse_junit

SANDBOX_DIR = Path(__file__).resolve().parent
LAUNCHER = SANDBOX_DIR / "launcher.py"
NETWORK_GUARD = SANDBOX_DIR / "sitecustomize_guard.py"
RESERVED_MODULE_NAMES = {"sitecustomize", "usercustomize", "conftest", "launcher", "pytest", "coverage"}
OUTPUT_TAIL_CHARS = 3000

Status = Literal["passed", "failed", "no_tests", "collection_error", "timeout", "crashed"]


class SandboxInputError(ValueError):
    """The module or test file name can't be used safely."""


@dataclass
class RunResult:
    status: Status
    counts: TestCounts = field(default_factory=TestCounts)
    coverage: CoverageData | None = None
    duration_s: float = 0.0
    exit_code: int | None = None
    output_tail: str = ""
    limits: dict[str, str] = field(default_factory=dict)

    @property
    def coverage_percent(self) -> float:
        return self.coverage.percent if self.coverage else 0.0

    @property
    def missing_lines(self) -> list[int]:
        return self.coverage.missing_lines if self.coverage else []

    @property
    def failures(self) -> list[Failure]:
        return self.counts.failures


def check_module_name(module_name: str) -> None:
    """Reject names that would shadow the stdlib or our sandbox files."""
    if not module_name.isidentifier():
        raise SandboxInputError(f"'{module_name}.py' isn't a valid Python module name. Rename the file.")
    if module_name in RESERVED_MODULE_NAMES or module_name in sys.stdlib_module_names:
        raise SandboxInputError(
            f"'{module_name}.py' clashes with a Python or pytest module name. Rename the file (e.g. my_{module_name}.py)."
        )


def run_tests(
    *,
    module_name: str,
    source_code: str,
    test_filename: str,
    test_code: str,
    settings: Settings,
) -> RunResult:
    """Run the tests in a fresh sandbox and return counts, failures, and coverage."""
    check_module_name(module_name)
    if Path(test_filename).name != test_filename or not test_filename.startswith("test_"):
        raise SandboxInputError(f"Invalid test file name: {test_filename!r}")

    workdir = Path(tempfile.mkdtemp(prefix="testsmith_")).resolve()  # macOS: /var -> /private/var
    started = time.monotonic()
    try:
        _prepare(workdir, module_name, source_code, test_filename, test_code)
        deadline = started + settings.sandbox_timeout_s
        exit_code = _run_step(_pytest_cmd(module_name, test_filename, settings), workdir, deadline, "pytest")
        if exit_code is None:
            return _finish(RunResult(status="timeout"), workdir, started)
        _run_step(_coverage_json_cmd(settings), workdir, deadline, "coverage")
        return _finish(_build_result(workdir, module_name, exit_code), workdir, started)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# ---- steps ------------------------------------------------------------------


def _prepare(workdir: Path, module_name: str, source: str, test_filename: str, tests: str) -> None:
    (workdir / f"{module_name}.py").write_text(source, encoding="utf-8")
    (workdir / test_filename).write_text(tests, encoding="utf-8")
    shutil.copyfile(NETWORK_GUARD, workdir / "sitecustomize.py")
    (workdir / "tmp").mkdir()


def _launcher_prefix(settings: Settings) -> list[str]:
    return [
        sys.executable, "-s", "-B", str(LAUNCHER),
        "--cpu", str(settings.sandbox_cpu_s),
        "--mem-mb", str(settings.sandbox_memory_mb),
        "--fsize-mb", str(settings.sandbox_file_size_mb),
        "--nproc", "256",
        "--",
        "-s", "-B",
    ]


def _pytest_cmd(module_name: str, test_filename: str, settings: Settings) -> list[str]:
    return [
        *_launcher_prefix(settings),
        "-m", "coverage", "run", f"--include={module_name}.py",
        "-m", "pytest", test_filename,
        "-q", "--tb=short", "-p", "no:cacheprovider", "--junitxml=report.xml",
    ]


def _coverage_json_cmd(settings: Settings) -> list[str]:
    return [*_launcher_prefix(settings), "-m", "coverage", "json", "-q", "-o", "coverage.json"]


def _sandbox_env(workdir: Path) -> dict[str, str]:
    """Allowlist: nothing from the parent environment except PATH."""
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(workdir),
        "TMPDIR": str(workdir / "tmp"),
        "PYTHONPATH": str(workdir),
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


def _run_step(cmd: list[str], workdir: Path, deadline: float, label: str) -> int | None:
    """Run one command; returns its exit code, or None on timeout."""
    remaining = max(0.5, deadline - time.monotonic())
    with open(workdir / f"{label}.out", "wb") as out:
        proc = subprocess.Popen(
            cmd,
            cwd=workdir,
            env=_sandbox_env(workdir),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # own process group, so a timeout kills every child
            close_fds=True,
        )
        try:
            return proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _kill_group(proc)
            return None


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()
    proc.wait()


# ---- results ----------------------------------------------------------------


def _build_result(workdir: Path, module_name: str, exit_code: int) -> RunResult:
    counts = parse_junit(workdir / "report.xml")
    coverage = parse_coverage(workdir / "coverage.json", f"{module_name}.py")
    status = _status_for(exit_code, counts)
    return RunResult(status=status, counts=counts or TestCounts(), coverage=coverage, exit_code=exit_code)


def _status_for(exit_code: int, counts: TestCounts | None) -> Status:
    # pytest exit codes: 0 ok, 1 tests failed, 2 interrupted/collection error, 5 no tests collected
    if exit_code == 0:
        return "passed"
    if exit_code == 1:
        return "failed"
    if exit_code == 5:
        return "no_tests"
    if exit_code == 2 or (counts is not None and counts.errors):
        return "collection_error"
    return "crashed"  # killed by a resource limit (negative code) or internal error


def _finish(result: RunResult, workdir: Path, started: float) -> RunResult:
    result.duration_s = round(time.monotonic() - started, 2)
    result.limits = _read_limits(workdir)
    result.output_tail = _tail(workdir / "pytest.out")
    return result


def _read_limits(workdir: Path) -> dict[str, str]:
    try:
        return json.loads((workdir / "limits.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _tail(path: Path) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - OUTPUT_TAIL_CHARS))
            return fh.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""
