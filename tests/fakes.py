"""Test doubles: a scripted stand-in for the Anthropic Messages API."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2

_REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def api_error(status: int, retry_after: str | None = None) -> anthropic.APIStatusError:
    """Build the SDK exception the real client would raise for an HTTP status."""
    headers = {"retry-after": retry_after} if retry_after else {}
    response = httpx2.Response(status, request=_REQ, headers=headers)
    classes = {
        401: anthropic.AuthenticationError,
        403: anthropic.PermissionDeniedError,
        404: anthropic.NotFoundError,
        429: anthropic.RateLimitError,
        500: anthropic.InternalServerError,
        529: anthropic.OverloadedError,
    }
    cls = classes.get(status, anthropic.APIStatusError)
    return cls(f"HTTP {status}", response=response, body=None)


def timeout_error() -> anthropic.APITimeoutError:
    return anthropic.APITimeoutError(request=_REQ)


def message(
    content: list[dict[str, Any]],
    stop_reason: str = "end_turn",
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> SimpleNamespace:
    """A minimal object shaped like an SDK Message."""
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        model="claude-sonnet-5-5",
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
        _request_id="req_test",
    )


def text_message(text: str, **kw: Any) -> SimpleNamespace:
    return message([{"type": "text", "text": text}], **kw)


def tool_message(name: str, tool_input: dict[str, Any], tool_id: str = "toolu_1", **kw: Any) -> SimpleNamespace:
    return message(
        [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}],
        stop_reason="tool_use",
        **kw,
    )


class FakeMessagesAPI:
    """Returns (or raises) scripted items in order and records every request."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(copy.deepcopy(kwargs))  # snapshot: callers keep mutating messages
        if not self.script:
            raise AssertionError("FakeMessagesAPI ran out of scripted responses")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item
