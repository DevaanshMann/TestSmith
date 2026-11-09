"""Bugs documented with xfail(BUG) markers but never reported are still surfaced."""

from __future__ import annotations

from testsmith.agent.bugs import bug_markers, unreported_bugs
from testsmith.agent.state import BugReport
from testsmith.llm.prompts import load_prompt

SOURCE = (
    "class Inventory:\n"
    "    def reorder(self):\n        return []\n"
    "    def needs_reorder(self):\n        return []\n"
)
TESTS = (
    "import pytest\n\n"
    "def test_ok():\n    assert True\n\n"
    '@pytest.mark.xfail(strict=True, reason="BUG: needs_reorder uses < instead of <=")\n'
    "def test_needs_reorder_at_level():\n    assert False\n\n"
    '@pytest.mark.xfail(reason="flaky on CI")\n'
    "def test_not_a_bug():\n    assert False\n"
)


def test_only_bug_markers_are_found():
    markers = bug_markers(TESTS)
    assert [(m.test_name, m.reason) for m in markers] == [
        ("test_needs_reorder_at_level", "BUG: needs_reorder uses < instead of <=")
    ]


def test_unreported_marker_becomes_a_bug_for_the_most_specific_function():
    bugs = unreported_bugs(TESTS, SOURCE, reported=[], iteration=1)
    assert len(bugs) == 1
    assert bugs[0].function_name == "needs_reorder"  # not the shorter "reorder"
    assert bugs[0].description == "needs_reorder uses < instead of <="
    assert bugs[0].source == "xfail_marker"


def test_already_reported_bug_is_not_duplicated():
    reported = [BugReport("Inventory.needs_reorder", "strict <", "x", 1)]
    assert unreported_bugs(TESTS, SOURCE, reported, iteration=1) == []


def test_bad_code_yields_no_markers():
    assert bug_markers("def broken(:\n") == []


def test_system_prompt_v2_is_active_and_v1_is_kept():
    v2, v1 = load_prompt("system"), load_prompt("system", "v1")
    assert "in the same response" in v2 and "boundary" in v2
    assert "in the same response" not in v1
