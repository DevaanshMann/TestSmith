"""Parses pytest's JUnit XML and coverage.py's JSON report into plain data."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

MAX_REPORT_BYTES = 5 * 1024 * 1024
MAX_FAILURES = 8
MAX_TRACE_LINES = 25
MAX_TRACE_CHARS = 1800


@dataclass
class Failure:
    test_id: str
    kind: str          # "failure" (assertion) or "error" (exception in setup/collection)
    message: str
    traceback: str


@dataclass
class TestCounts:
    __test__ = False  # not a pytest test class

    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    failures: list[Failure] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.passed + self.failed + self.errors + self.skipped


@dataclass
class CoverageData:
    percent: float
    missing_lines: list[int]
    executed_lines: list[int]
    num_statements: int


def parse_junit(path: Path) -> TestCounts | None:
    """Counts and trimmed failure details, or None if the report is missing/unreadable."""
    if not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None
    counts = TestCounts()
    for case in root.iter("testcase"):
        problem = case.find("failure")
        kind = "failure"
        if problem is None:
            problem, kind = case.find("error"), "error"
        if problem is not None:
            if kind == "failure":
                counts.failed += 1
            else:
                counts.errors += 1
            if len(counts.failures) < MAX_FAILURES:
                counts.failures.append(_failure(case, kind, problem))
        elif case.find("skipped") is not None:
            counts.skipped += 1
        else:
            counts.passed += 1
    return counts


def _failure(case: ET.Element, kind: str, problem: ET.Element) -> Failure:
    classname = case.get("classname", "")
    test_id = f"{classname}::{case.get('name', '?')}" if classname else case.get("name", "?")
    return Failure(
        test_id=test_id,
        kind=kind,
        message=(problem.get("message") or "")[:300],
        traceback=trim_traceback(problem.text or ""),
    )


def trim_traceback(text: str) -> str:
    """Keep the end of a traceback (where the assertion is), within line and char budgets."""
    lines = text.strip().splitlines()
    if len(lines) > MAX_TRACE_LINES:
        lines = ["…(trimmed)…", *lines[-MAX_TRACE_LINES:]]
    trimmed = "\n".join(lines)
    if len(trimmed) > MAX_TRACE_CHARS:
        trimmed = "…" + trimmed[-MAX_TRACE_CHARS:]
    return trimmed


def parse_coverage(path: Path, module_filename: str) -> CoverageData | None:
    """Coverage for the module under test only."""
    if not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    for name, info in data.get("files", {}).items():
        if Path(name).name == module_filename:
            summary = info.get("summary", {})
            return CoverageData(
                percent=round(float(summary.get("percent_covered", 0.0)), 1),
                missing_lines=sorted(info.get("missing_lines", [])),
                executed_lines=sorted(info.get("executed_lines", [])),
                num_statements=int(summary.get("num_statements", 0)),
            )
    return None
