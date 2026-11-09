"""Action schemas, tool-call parsing, and the self-correction loop."""

from __future__ import annotations

import random

import pytest

from testsmith.agent.actions import TOOL_MODELS, WriteTests, tool_definitions
from testsmith.agent.parser import ActionParseError, ParseContext, parse_tool_call, request_actions
from testsmith.config import Settings
from testsmith.llm.client import LLMClient
from tests.fakes import FakeMessagesAPI, message, text_message, tool_message

CTX = ParseContext(module_name="calc", source_code="def add(a, b):\n    return a + b\n")

GOOD_TESTS = "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"


def write_input(**overrides):
    data = {
        "filename": "test_calc.py",
        "test_plan": {"happy_path": ["add two ints"], "edge_cases": ["zero"]},
        "rationale": "First version covering add().",
        "content": GOOD_TESTS,
    }
    data.update(overrides)
    return data


def block(name, tool_input, tool_id="toolu_1"):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}


# ---- schemas ----------------------------------------------------------------


def test_tool_definitions_cover_every_action_and_have_no_refs():
    tools = tool_definitions()
    assert [t["name"] for t in tools] == list(TOOL_MODELS)
    for tool in tools:
        assert tool["description"]
        assert tool["input_schema"]["type"] == "object"
        assert "$ref" not in str(tool["input_schema"])
        assert tool["input_schema"]["additionalProperties"] is False


def test_write_tests_schema_puts_plan_before_code():
    props = list(tool_definitions()[1]["input_schema"]["properties"])
    assert props.index("test_plan") < props.index("content")


# ---- single call parsing ----------------------------------------------------


def test_valid_write_tests_parses():
    call = parse_tool_call(block("write_tests", write_input()), CTX)
    assert call.ok and isinstance(call.action, WriteTests)
    assert call.action.test_plan.happy_path == ["add two ints"]


def test_run_tests_accepts_empty_or_missing_input():
    assert parse_tool_call(block("run_tests", {}), CTX).ok
    assert parse_tool_call(block("run_tests", None), CTX).ok


@pytest.mark.parametrize(
    ("name", "tool_input", "stage", "fragment"),
    [
        ("delete_everything", {}, "unknown_tool", "unknown tool"),
        ("write_tests", "{not json", "json", "not valid JSON"),
        ("write_tests", ["a list"], "json", "JSON object"),
        ("write_tests", write_input(filename="../evil.py"), "schema", "filename"),
        ("write_tests", write_input(test_plan={"happy_path": []}), "schema", "test_plan.happy_path"),
        ("write_tests", write_input(extra_field=1), "schema", "extra_field"),
        ("run_tests", {"cmd": "rm -rf /"}, "schema", "cmd"),
        ("report_bug", {"function_name": "add; rm", "description": "wrong sum!!", "evidence": "1+1=3"}, "schema", "function_name"),
        ("write_tests", write_input(content="import os\nos.system('x')\n\ndef test_a():\n    pass\n"), "safety", "os.system"),
        ("write_tests", write_input(content="def test_a(:\n    pass  # broken\n"), "safety", "syntax error"),
    ],
)
def test_invalid_calls_are_rejected_with_reason(name, tool_input, stage, fragment):
    call = parse_tool_call(block(name, tool_input), CTX)
    assert not call.ok
    assert call.stage == stage
    assert any(fragment in e for e in call.errors), call.errors


def test_json_string_arguments_are_accepted():
    call = parse_tool_call(block("finish", '{"summary": "All done, 100% coverage."}'), CTX)
    assert call.ok


# ---- correction loop --------------------------------------------------------


def make_llm(script):
    api = FakeMessagesAPI(script)
    llm = LLMClient(Settings(), messages_api=api, sleep=lambda s: None, rng=random.Random(0))
    return llm, api


def test_valid_first_response_needs_no_correction():
    llm, api = make_llm([tool_message("write_tests", write_input())])
    messages = [{"role": "user", "content": "go"}]
    outcome = request_actions(llm, system="s", messages=messages, ctx=CTX)
    assert outcome.corrections_used == 0
    assert len(api.requests) == 1
    assert messages[-1]["role"] == "assistant"


def test_invalid_call_gets_error_feedback_then_succeeds():
    llm, api = make_llm([
        tool_message("write_tests", write_input(filename="tests.py"), tool_id="bad"),
        tool_message("write_tests", write_input(), tool_id="good"),
    ])
    events = []
    messages = [{"role": "user", "content": "go"}]
    outcome = request_actions(llm, system="s", messages=messages, ctx=CTX, on_event=events.append)

    assert outcome.corrections_used == 1
    feedback = messages[2]["content"][0]
    assert feedback["type"] == "tool_result" and feedback["tool_use_id"] == "bad" and feedback["is_error"]
    assert "filename" in feedback["content"] and "attempt 1 of 2" in feedback["content"]
    assert [e["valid"] for e in events] == [False, True]


def test_gives_up_after_two_corrections():
    bad = write_input(content="import socket\n\ndef test_a():\n    pass\n")
    llm, api = make_llm([tool_message("write_tests", bad)] * 3)
    with pytest.raises(ActionParseError) as info:
        request_actions(llm, system="s", messages=[{"role": "user", "content": "go"}], ctx=CTX)
    assert len(api.requests) == 3
    assert "socket" in info.value.user_message


def test_text_only_reply_is_nudged_to_use_a_tool():
    llm, _ = make_llm([text_message("Here are your tests: ..."), tool_message("run_tests", {})])
    messages = [{"role": "user", "content": "go"}]
    outcome = request_actions(llm, system="s", messages=messages, ctx=CTX)
    assert outcome.corrections_used == 1
    assert "without calling a tool" in messages[2]["content"]


def test_valid_sibling_of_invalid_call_is_not_executed():
    response = message(
        [block("write_tests", write_input(filename="bad"), "t1"), block("run_tests", {}, "t2")],
        stop_reason="tool_use",
    )
    llm, _ = make_llm([response, tool_message("write_tests", write_input())])
    messages = [{"role": "user", "content": "go"}]
    request_actions(llm, system="s", messages=messages, ctx=CTX)
    results = messages[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    assert "Not executed" in results[1]["content"]


def test_truncated_output_is_treated_as_invalid():
    truncated = message([block("write_tests", write_input())], stop_reason="max_tokens")
    llm, _ = make_llm([truncated, tool_message("write_tests", write_input())])
    messages = [{"role": "user", "content": "go"}]
    outcome = request_actions(llm, system="s", messages=messages, ctx=CTX)
    assert outcome.corrections_used == 1
    assert "cut off" in messages[2]["content"][0]["content"]
