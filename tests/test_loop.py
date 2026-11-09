"""Agent loop + executor with a scripted fake LLM (no API key needed)."""

from __future__ import annotations

import itertools
import random

import pytest

from testsmith.agent.feedback import compress_ranges, uncovered_section
from testsmith.agent.loop import AgentLoop, build_system_prompt
from testsmith.config import Settings
from testsmith.llm.client import LLMClient
from testsmith.sandbox.report import CoverageData, TestCounts
from testsmith.sandbox.runner import RunResult, SandboxInputError
from tests.fakes import FakeMessagesAPI, api_error, text_message, tool_message

SOURCE = (
    "def add(a, b):\n"
    "    return a + b\n"
    "\n"
    "def sign(x):\n"
    "    if x < 0:\n"
    "        return -1\n"
    "    return 1\n"
)
PLAN = {"happy_path": ["basic behaviour"]}
TESTS_V1 = "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
TESTS_V2 = TESTS_V1 + "\nfrom calc import sign\n\ndef test_sign():\n    assert sign(-5) == -1\n    assert sign(5) == 1\n"

_ids = itertools.count()


def call(name, tool_input=None):
    return tool_message(name, tool_input or {}, tool_id=f"toolu_{next(_ids)}")


def write(content, rationale="Cover more behaviour."):
    return call("write_tests", {"filename": "test_calc.py", "test_plan": PLAN, "rationale": rationale, "content": content})


def make_loop(script, runner=None, **settings):
    api = FakeMessagesAPI(script)
    cfg = Settings(**settings)
    llm = LLMClient(cfg, messages_api=api, sleep=lambda s: None, rng=random.Random(0))
    events: list[dict] = []
    kwargs = {"runner": runner} if runner else {}
    loop = AgentLoop(llm, cfg, on_event=events.append, **kwargs)
    return loop, api, events


def fake_runner(*coverages, failed=0):
    """A runner that returns the given coverage values in order."""
    values = iter(coverages)

    def run(**_kwargs):
        cov = next(values)
        counts = TestCounts(passed=3, failed=failed)
        return RunResult(status="failed" if failed else "passed", counts=counts,
                         coverage=CoverageData(cov, [5, 6] if cov < 100 else [], [], 6))
    return run


def run_calc(loop, **kw):
    return loop.run(module_name="calc", source_code=SOURCE, **kw)


# ---- end to end with the real sandbox --------------------------------------


def test_coverage_climbs_until_target_met():
    loop, api, events = make_loop([
        call("read_source", {"path": "calc.py"}),
        write(TESTS_V1),
        call("run_tests"),
        write(TESTS_V2),
        call("run_tests"),
    ])
    result = run_calc(loop, coverage_target=100)

    assert result.stop_reason == "target_met"
    coverages = [e["coverage"] for e in events if e["event"] == "iteration"]
    assert coverages[0] < coverages[1] == 100.0
    assert result.final_tests == TESTS_V2
    assert len(api.requests) == 5  # stopped without asking the model again


def test_feedback_shows_uncovered_source_lines():
    loop, api, _ = make_loop([write(TESTS_V1), call("run_tests"), call("finish", {"summary": "Good enough for now."})])
    run_calc(loop)
    feedback = api.requests[2]["messages"][-1]["content"][0]["content"]
    assert "Test run 1 of 5" in feedback
    assert "Uncovered lines (5-7), with source:" in feedback
    assert "if x < 0:" in feedback
    assert "Add tests that execute lines 5-7" in feedback


def test_failures_are_fed_back_with_traceback():
    bad = "from calc import add\n\ndef test_add():\n    assert add(2, 2) == 5\n"
    loop, api, _ = make_loop([write(bad), call("run_tests"), call("finish", {"summary": "Stopping the session."})])
    run_calc(loop)
    feedback = api.requests[2]["messages"][-1]["content"][0]["content"]
    assert "1 test failing" in feedback
    assert "assert 4 == 5" in feedback
    assert "decide whether the test or the source is wrong" in feedback


# ---- stop conditions ----------------------------------------------------------


def test_stops_at_max_iterations_and_keeps_best_suite():
    loop, _, _ = make_loop([write(TESTS_V1), call("run_tests")], runner=fake_runner(40.0))
    result = run_calc(loop, max_iterations=1)
    assert result.stop_reason == "max_iterations"
    assert result.final_tests == TESTS_V1


def test_stops_when_stalled():
    script = [write(TESTS_V1), call("run_tests")] + [call("run_tests")] * 2
    loop, _, _ = make_loop(script, runner=fake_runner(50.0, 50.0, 50.0))
    result = run_calc(loop, max_iterations=5)
    assert result.stop_reason == "stalled"
    assert result.state.runs_used == 3


def test_agent_finish_stops_loop():
    loop, _, events = make_loop([write(TESTS_V1), call("run_tests"), call("finish", {"summary": "Remaining lines unreachable."})],
                                runner=fake_runner(80.0))
    result = run_calc(loop)
    assert result.stop_reason == "agent_finished"
    assert "unreachable" in result.message
    assert events[-1]["event"] == "session_finished"


def test_iteration_cap_is_enforced_even_if_requested_higher():
    loop, _, _ = make_loop([write(TESTS_V1), call("run_tests")], runner=fake_runner(10.0), hard_max_iterations=1)
    result = run_calc(loop, max_iterations=50)
    assert result.state.max_iterations == 1


# ---- bugs, errors ------------------------------------------------------------


def test_report_bug_is_recorded_once():
    bug = {"function_name": "sign", "description": "returns 1 for zero, docstring says 0", "evidence": "sign(0) == 1"}
    loop, api, _ = make_loop([call("report_bug", bug), call("report_bug", bug), call("finish", {"summary": "Reported a bug."})])
    result = run_calc(loop)
    assert len(result.state.bugs) == 1
    assert "xfail(strict=True" in api.requests[1]["messages"][-1]["content"][0]["content"]


@pytest.mark.parametrize(
    ("first", "fragment"),
    [
        (call("read_source", {"path": "/etc/passwd"}), "Only calc.py"),
        (call("run_tests"), "write_tests first"),
        (call("write_tests", {"filename": "test_other.py", "test_plan": PLAN, "rationale": "Wrong file name.", "content": TESTS_V1}),
         "test_calc.py"),
    ],
)
def test_handler_rejections_go_back_to_model_as_errors(first, fragment):
    loop, api, _ = make_loop([first, call("finish", {"summary": "Giving up here."})])
    run_calc(loop)
    result_block = api.requests[1]["messages"][-1]["content"][0]
    assert result_block["is_error"] is True
    assert fragment in result_block["content"]


def test_llm_error_ends_session_with_friendly_message_and_partial_results():
    loop, _, _ = make_loop([write(TESTS_V1), call("run_tests"), api_error(401)], runner=fake_runner(60.0))
    result = run_calc(loop)
    assert result.stop_reason == "llm_error" and result.is_error
    assert "API key" in result.message
    assert result.final_tests == TESTS_V1  # work done before the error is kept


def test_persistently_invalid_actions_end_session():
    loop, _, _ = make_loop([text_message("I think the tests look fine.")] * 3)
    result = run_calc(loop)
    assert result.stop_reason == "invalid_actions"


def test_bad_module_name_is_rejected_before_any_api_call():
    loop, api, _ = make_loop([])
    with pytest.raises(SandboxInputError):
        loop.run(module_name="json", source_code="x = 1\n")
    assert api.requests == []


def test_tool_results_match_tool_use_ids():
    loop, api, _ = make_loop([write(TESTS_V1), call("run_tests"), call("finish", {"summary": "Done with this module."})],
                             runner=fake_runner(70.0))
    run_calc(loop)
    messages = api.requests[-1]["messages"]
    for assistant, user in zip(messages[1::2], messages[2::2]):
        use_ids = [b["id"] for b in assistant["content"] if b["type"] == "tool_use"]
        result_ids = [b["tool_use_id"] for b in user["content"]]
        assert use_ids == result_ids


# ---- prompts & helpers ------------------------------------------------------------


def test_system_prompt_is_stable_and_complete():
    prompt = build_system_prompt()
    assert prompt == build_system_prompt()  # identical every call, so it caches
    assert "senior QA engineer" in prompt and "<example>" in prompt and "$" not in prompt


def test_task_prompt_includes_user_instructions():
    loop, api, _ = make_loop([call("finish", {"summary": "Nothing to do here."})])
    run_calc(loop, instructions="focus on negative numbers")
    first_user = api.requests[0]["messages"][0]["content"]
    assert "<user_instructions>\nfocus on negative numbers\n</user_instructions>" in first_user


def test_compress_ranges():
    assert compress_ranges([1, 2, 3, 7, 9, 10]) == "1-3, 7, 9-10"


def test_uncovered_section_marks_gaps():
    text = uncovered_section("a\nb\nc\nd\n", [1, 3])
    assert "   1 | a" in text and "  ..." in text and "   3 | c" in text
