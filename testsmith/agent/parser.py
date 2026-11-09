"""Turns model output into validated actions, asking the model to self-correct when needed.

Validation happens in two stages for every tool call:
1. schema: pydantic checks types, required fields, patterns, and lengths;
2. safety: write_tests content is statically scanned with the AST validator.

Invalid calls are never executed. Their errors go back to the model as
`tool_result` blocks with is_error=true, up to `max_corrections` times.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import ValidationError

from testsmith.agent.actions import TOOL_MODELS, WriteTests, _Action, tool_definitions
from testsmith.llm.client import LLMClient, LLMResponse
from testsmith.llm.prompts import render_prompt
from testsmith.safety.validator import validate_tests

Stage = Literal["ok", "json", "unknown_tool", "schema", "safety", "truncated"]
EventHook = Callable[[dict[str, Any]], None]


@dataclass(frozen=True)
class ParseContext:
    """What the parser needs to know about the current session."""

    module_name: str          # e.g. "bank_account"
    source_code: str | None = None


@dataclass
class ParsedCall:
    tool_use_id: str
    name: str
    raw_input: Any
    action: _Action | None = None
    stage: Stage = "ok"
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.action is not None and not self.errors


@dataclass
class ParseOutcome:
    response: LLMResponse
    calls: list[ParsedCall]
    corrections_used: int


class ActionParseError(Exception):
    """The model kept producing invalid actions after all correction attempts."""

    def __init__(self, user_message: str, calls: list[ParsedCall]) -> None:
        self.user_message = user_message
        self.calls = calls
        super().__init__(user_message)


# ---- single-response parsing ------------------------------------------------


def parse_response(response: LLMResponse, ctx: ParseContext) -> list[ParsedCall]:
    """Validate every tool_use block in a response. Never raises."""
    truncated = response.stop_reason == "max_tokens"
    calls = []
    for block in response.tool_uses:
        call = parse_tool_call(block, ctx)
        if truncated and call.ok:
            call.action, call.stage = None, "truncated"
            call.errors = ["your output was cut off at the token limit; send a shorter test file"]
        calls.append(call)
    return calls


def parse_tool_call(block: dict[str, Any], ctx: ParseContext) -> ParsedCall:
    name = str(block.get("name", ""))
    call = ParsedCall(tool_use_id=str(block.get("id", "")), name=name, raw_input=block.get("input"))

    model = TOOL_MODELS.get(name)
    if model is None:
        return _fail(call, "unknown_tool", [f"unknown tool {name!r}; valid tools: {', '.join(TOOL_MODELS)}"])

    data = call.raw_input
    if isinstance(data, str):  # some providers/edge cases deliver arguments as a JSON string
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            return _fail(call, "json", [f"arguments are not valid JSON: {exc.msg} at position {exc.pos}"])
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return _fail(call, "json", ["arguments must be a JSON object"])

    try:
        action = model.model_validate(data)
    except ValidationError as exc:
        return _fail(call, "schema", format_validation_errors(exc))

    safety_errors = _safety_errors(action, ctx)
    if safety_errors:
        return _fail(call, "safety", safety_errors)

    call.action = action
    return call


def format_validation_errors(exc: ValidationError) -> list[str]:
    """Short, model-readable lines like `filename: String should match pattern ...`."""
    lines = []
    for err in exc.errors(include_url=False):
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        lines.append(f"{loc}: {err['msg']}")
    return lines


def _safety_errors(action: _Action, ctx: ParseContext) -> list[str]:
    if not isinstance(action, WriteTests):
        return []
    report = validate_tests(action.content, ctx.module_name, ctx.source_code)
    return [f"unsafe or invalid test code, {finding}" for finding in report.findings if finding.severity == "block"]


def _fail(call: ParsedCall, stage: Stage, errors: list[str]) -> ParsedCall:
    call.stage, call.errors = stage, errors
    return call


# ---- request + self-correction loop ------------------------------------------


def request_actions(
    llm: LLMClient,
    *,
    system: str,
    messages: list[dict[str, Any]],
    ctx: ParseContext,
    max_corrections: int = 2,
    on_event: EventHook | None = None,
) -> ParseOutcome:
    """Ask the model for its next actions; on invalid output, feed errors back and retry.

    `messages` is appended to in place (never rewritten), so the conversation
    stays append-only, which keeps prompt caching effective.
    """
    tools = tool_definitions()
    for attempt in range(max_corrections + 1):
        response = llm.create_message(system=system, messages=messages, tools=tools, purpose="agent_step")
        messages.append({"role": "assistant", "content": response.content})
        calls = parse_response(response, ctx)
        _emit_validation(on_event, calls, attempt)

        if calls and all(c.ok for c in calls):
            return ParseOutcome(response, calls, corrections_used=attempt)
        if attempt == max_corrections:
            raise ActionParseError(_give_up_message(calls), calls)
        messages.append({"role": "user", "content": _correction_content(calls, attempt + 1, max_corrections)})
    raise AssertionError("unreachable")


def _correction_content(calls: list[ParsedCall], attempt: int, max_attempts: int) -> list[dict[str, Any]] | str:
    if not calls:
        return render_prompt("no_tool_call", attempt=attempt, max_attempts=max_attempts)
    blocks = []
    for call in calls:
        if call.ok:
            text = "Not executed: another tool call in the same message was invalid. Send it again if still needed."
        else:
            errors = "\n".join(f"- {e}" for e in call.errors)
            text = render_prompt("correction", tool=call.name, errors=errors, attempt=attempt, max_attempts=max_attempts)
        blocks.append({"type": "tool_result", "tool_use_id": call.tool_use_id, "content": text, "is_error": True})
    return blocks


def _give_up_message(calls: list[ParsedCall]) -> str:
    if not calls:
        return "The model stopped calling tools, so the session ended. Try again or rephrase your instructions."
    first = next(c for c in calls if not c.ok)
    return f"The model's `{first.name}` action stayed invalid after self-correction: {first.errors[0]}"


def _emit_validation(on_event: EventHook | None, calls: list[ParsedCall], attempt: int) -> None:
    if on_event is None:
        return
    if not calls:
        on_event({"event": "validation", "tool": None, "valid": False, "stage": "no_tool_call", "attempt": attempt})
    for call in calls:
        on_event({
            "event": "validation",
            "tool": call.name,
            "valid": call.ok,
            "stage": call.stage,
            "errors": call.errors[:5],
            "attempt": attempt,
        })
