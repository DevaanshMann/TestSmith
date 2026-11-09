"""Finds bugs the agent documented with xfail markers but never reported.

Models sometimes mark a test `@pytest.mark.xfail(reason="BUG: ...")` and skip
the `report_bug` call. The marker is still clear evidence, so the session
adds those bugs itself rather than relying on the model to follow the protocol.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from testsmith.agent.state import BugReport


@dataclass(frozen=True)
class BugMarker:
    test_name: str
    reason: str


def bug_markers(test_code: str) -> list[BugMarker]:
    """Tests decorated with xfail whose reason starts with 'BUG'."""
    try:
        tree = ast.parse(test_code)
    except SyntaxError:
        return []
    markers = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            reason = _xfail_reason(decorator)
            if reason is not None and reason.strip().upper().startswith("BUG"):
                markers.append(BugMarker(node.name, reason.strip()))
    return markers


def function_names(source_code: str) -> list[str]:
    """Every function and method name in the source, longest first (so matching prefers specific names)."""
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return []
    names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    return sorted((n for n in names if not n.startswith("__")), key=len, reverse=True)


def unreported_bugs(test_code: str, source_code: str, reported: list[BugReport], iteration: int) -> list[BugReport]:
    """BugReports for BUG markers whose function has no report yet."""
    candidates = function_names(source_code)
    reported_names = {b.function_name.rsplit(".", 1)[-1] for b in reported}
    found: list[BugReport] = []
    for marker in bug_markers(test_code):
        target = _guess_function(marker, candidates)
        key = target or marker.test_name
        if key in reported_names:
            continue
        reported_names.add(key)
        found.append(BugReport(
            function_name=target or "(unknown function)",
            description=marker.reason.removeprefix("BUG:").removeprefix("BUG").strip() or marker.reason,
            evidence=f"Test `{marker.test_name}` is marked xfail(strict=True) because the source is wrong.",
            iteration=iteration,
            source="xfail_marker",
        ))
    return found


def _guess_function(marker: BugMarker, candidates: list[str]) -> str | None:
    for name in candidates:  # longest first: "needs_reorder" beats "reorder"
        if name in marker.reason or name in marker.test_name:
            return name
    return None


def _xfail_reason(decorator: ast.expr) -> str | None:
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    if name != "xfail":
        return None
    for keyword in decorator.keywords:
        if keyword.arg == "reason" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            return keyword.value.value
    if decorator.args and isinstance(decorator.args[-1], ast.Constant) and isinstance(decorator.args[-1].value, str):
        return decorator.args[-1].value
    return None
