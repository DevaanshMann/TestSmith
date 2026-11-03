"""LLM client: retries, backoff, error mapping, budgets, audit hook."""

from __future__ import annotations

import random

import pytest

from testsmith.config import Settings
from testsmith.llm.client import FALLBACK_BETA, LLMClient, LLMError
from testsmith.safety.limits import TokenBudget
from tests.fakes import FakeMessagesAPI, api_error, text_message, timeout_error, tool_message


def make_client(script, *, budgets=(), settings=None):
    api = FakeMessagesAPI(script)
    sleeps: list[float] = []
    events: list[dict] = []
    client = LLMClient(
        settings or Settings(),
        messages_api=api,
        budgets=budgets,
        on_call=events.append,
        sleep=sleeps.append,
        rng=random.Random(0),
    )
    return client, api, sleeps, events


def call(client, **kw):
    return client.create_message(system="sys", messages=[{"role": "user", "content": "hi"}], **kw)


def test_success_returns_text_and_tracks_usage():
    client, api, sleeps, events = make_client([text_message("hello", input_tokens=10, output_tokens=5)])
    response = call(client)
    assert response.text == "hello"
    assert response.attempts == 1
    assert client.usage.input_tokens == 10 and client.usage.output_tokens == 5
    assert sleeps == []
    assert events[-1]["outcome"] == "ok" and events[-1]["output_tokens"] == 5


def test_request_shape_uses_settings_and_enables_fallback():
    client, api, *_ = make_client([text_message("ok")], settings=Settings(model="m", effort="low"))
    call(client, tools=[{"name": "t", "input_schema": {"type": "object"}}])
    req = api.requests[0]
    assert req["model"] == "m"
    assert req["output_config"] == {"effort": "low"}
    assert req["tool_choice"] == {"type": "auto"}
    assert req["betas"] == [FALLBACK_BETA] and req["fallbacks"] == "default"
    assert "temperature" not in req


def test_fallback_can_be_disabled():
    client, api, *_ = make_client([text_message("ok")], settings=Settings(refusal_fallback=False))
    call(client)
    assert "fallbacks" not in api.requests[0] and "betas" not in api.requests[0]


@pytest.mark.parametrize("error", [api_error(429), api_error(500), api_error(529), timeout_error()])
def test_retryable_errors_are_retried_then_succeed(error):
    client, api, sleeps, events = make_client([error, text_message("ok")])
    response = call(client)
    assert response.attempts == 2
    assert len(sleeps) == 1 and sleeps[0] > 0
    assert [e["outcome"] for e in events] == ["retry", "ok"]


def test_backoff_grows_exponentially_and_is_capped():
    settings = Settings(llm_max_retries=6, llm_backoff_base_s=1.0, llm_backoff_cap_s=8.0)
    client, _, sleeps, _ = make_client([api_error(500)] * 6 + [text_message("ok")], settings=settings)
    call(client)
    assert len(sleeps) == 6
    assert sleeps[0] < 1.3          # ~1s * jitter
    assert sleeps[3] > 3.9          # ~8s * jitter >= 4
    assert max(sleeps) <= 8.25      # cap + max jitter


def test_retry_after_header_is_honoured():
    client, _, sleeps, _ = make_client([api_error(429, retry_after="3"), text_message("ok")])
    call(client)
    assert 3.0 <= sleeps[0] <= 3.5


def test_gives_up_after_max_retries():
    settings = Settings(llm_max_retries=2)
    client, api, sleeps, events = make_client([api_error(529)] * 3, settings=settings)
    with pytest.raises(LLMError) as info:
        call(client)
    assert info.value.kind == "overloaded"
    assert len(api.requests) == 3 and len(sleeps) == 2
    assert events[-1]["outcome"] == "error"


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "auth"), (403, "permission"), (404, "not_found"), (400, "bad_request"), (413, "too_large")],
)
def test_non_retryable_errors_fail_fast_with_friendly_message(status, kind):
    client, api, sleeps, _ = make_client([api_error(status)])
    with pytest.raises(LLMError) as info:
        call(client)
    assert info.value.kind == kind
    assert info.value.user_message and "Traceback" not in info.value.user_message
    assert sleeps == [] and len(api.requests) == 1


def test_refusal_raises_llm_error():
    client, *_ = make_client([text_message("", stop_reason="refusal")])
    with pytest.raises(LLMError) as info:
        call(client)
    assert info.value.kind == "refusal"


def test_budget_blocks_call_when_exhausted():
    budget = TokenBudget(limit=100)
    client, api, *_ = make_client([text_message("a", input_tokens=80, output_tokens=40)], budgets=[budget])
    call(client)
    assert budget.used == 120
    with pytest.raises(LLMError) as info:
        call(client)
    assert info.value.kind == "budget"
    assert len(api.requests) == 1  # second call never reached the API


def test_tool_uses_are_exposed_as_dicts():
    client, *_ = make_client([tool_message("run_tests", {})])
    response = call(client)
    assert response.stop_reason == "tool_use"
    assert response.tool_uses[0]["name"] == "run_tests"


def test_audit_event_never_contains_api_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SECRET")
    client, _, _, events = make_client([api_error(429), text_message("ok")])
    call(client)
    assert "sk-ant-SECRET" not in repr(events)
