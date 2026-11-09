"""Anthropic API wrapper: retries with backoff + jitter, timeouts, budgets, usage tracking.

The SDK's built-in retries are disabled (max_retries=0) so that every attempt,
delay, and failure goes through this module and shows up in the audit log.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol

import anthropic

from testsmith.config import Settings
from testsmith.safety.limits import BudgetExceededError, TokenBudget

FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Statuses that are worth retrying: rate limit, request timeout, conflict, and server-side errors.
_RETRYABLE_STATUSES = {408, 409, 429}


class LLMError(Exception):
    """A failed LLM call with a message that is safe to show in the UI."""

    def __init__(self, kind: str, user_message: str, *, retryable: bool = False, detail: str = "") -> None:
        self.kind = kind
        self.user_message = user_message
        self.retryable = retryable
        self.detail = detail
        super().__init__(f"[{kind}] {user_message}")


@dataclass
class Usage:
    """Token usage for one call."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def billable_total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens


@dataclass
class LLMResponse:
    """A successful model response, with content blocks as plain dicts."""

    content: list[dict[str, Any]]
    stop_reason: str | None
    usage: Usage
    model: str
    latency_ms: int
    attempts: int
    request_id: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(b.get("text", "") for b in self.content if b.get("type") == "text").strip()

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]


@dataclass
class UsageTracker:
    """Running totals for a session, shown in the UI sidebar."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    history: list[Usage] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        """Same total the token budgets are charged with."""
        return self.input_tokens + self.output_tokens + self.cache_read_tokens + self.cache_write_tokens

    def add(self, usage: Usage) -> None:
        self.calls += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cache_read_tokens += usage.cache_read_tokens
        self.cache_write_tokens += usage.cache_write_tokens
        self.history.append(usage)


class MessagesAPI(Protocol):
    """The slice of the SDK we use; lets tests inject a fake."""

    def create(self, **kwargs: Any) -> Any: ...


CallHook = Callable[[dict[str, Any]], None]


class LLMClient:
    """Sends Messages API requests with retries, budgets, and audit hooks."""

    def __init__(
        self,
        settings: Settings,
        *,
        messages_api: MessagesAPI | None = None,
        budgets: Iterable[TokenBudget] = (),
        on_call: CallHook | None = None,
        sleep: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.settings = settings
        self._api = messages_api or self._build_sdk(settings)
        self._budgets = list(budgets)
        self._on_call = on_call
        self._sleep = sleep
        self._rng = rng or random.Random()
        self.usage = UsageTracker()

    @staticmethod
    def _build_sdk(settings: Settings) -> MessagesAPI:
        sdk = anthropic.Anthropic(timeout=float(settings.llm_timeout_s), max_retries=0)
        return sdk.beta.messages

    # ---- public API -------------------------------------------------------

    def create_message(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        purpose: str = "agent_step",
    ) -> LLMResponse:
        """Call the model once (with retries). Raises LLMError on failure."""
        self._check_budgets()
        request = self._build_request(system, messages, tools)
        started = time.monotonic()
        attempt = 0
        while True:
            attempt += 1
            try:
                raw = self._api.create(**request)
            except Exception as exc:  # noqa: BLE001 - mapped to LLMError below
                error = map_exception(exc)
                if error.retryable and attempt <= self.settings.llm_max_retries:
                    delay = self._backoff_delay(attempt, exc)
                    self._emit(purpose, started, attempt, outcome="retry", error=error, delay_s=delay)
                    self._sleep(delay)
                    continue
                self._emit(purpose, started, attempt, outcome="error", error=error)
                raise error from exc
            response = self._to_response(raw, started, attempt)
            self._record_usage(response.usage)
            self._emit(purpose, started, attempt, outcome="ok", response=response)
            if response.stop_reason == "refusal":
                raise LLMError("refusal", "The model declined this request. Try a different source file.")
            return response

    # ---- internals ---------------------------------------------------------

    def _check_budgets(self) -> None:
        for budget in self._budgets:
            try:
                budget.ensure_available()
            except BudgetExceededError as exc:
                raise LLMError(
                    "budget",
                    f"The {exc.scope} token budget is used up ({exc.used:,}/{exc.limit:,}). "
                    "Please try again later.",
                ) from exc

    def _record_usage(self, usage: Usage) -> None:
        self.usage.add(usage)
        for budget in self._budgets:
            budget.consume(usage.billable_total)

    def _build_request(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
    ) -> dict[str, Any]:
        s = self.settings
        request: dict[str, Any] = {
            "model": s.model,
            "max_tokens": s.max_output_tokens,
            "system": system,
            "messages": messages,
            "output_config": {"effort": s.effort},
            # The system prompt and tool list are identical every turn, so caching the
            # prefix makes iterations 2..N much cheaper.
            "cache_control": {"type": "ephemeral"},
        }
        if tools:
            request["tools"] = tools
            request["tool_choice"] = {"type": "auto"}
        if s.refusal_fallback:
            request["betas"] = [FALLBACK_BETA]
            request["fallbacks"] = "default"
        return request

    def _backoff_delay(self, attempt: int, exc: Exception) -> float:
        """Exponential backoff with jitter; honours a server Retry-After header."""
        retry_after = _retry_after_seconds(exc)
        if retry_after is not None:
            return min(retry_after, self.settings.llm_backoff_cap_s) + self._rng.uniform(0, 0.5)
        exp = self.settings.llm_backoff_base_s * (2 ** (attempt - 1))
        return min(self.settings.llm_backoff_cap_s, exp) * self._rng.uniform(0.5, 1.0) + self._rng.uniform(0, 0.25)

    def _to_response(self, raw: Any, started: float, attempts: int) -> LLMResponse:
        u = getattr(raw, "usage", None)
        usage = Usage(
            input_tokens=getattr(u, "input_tokens", 0) or 0,
            output_tokens=getattr(u, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
        )
        return LLMResponse(
            content=[_block_to_dict(b) for b in raw.content],
            stop_reason=getattr(raw, "stop_reason", None),
            usage=usage,
            model=getattr(raw, "model", self.settings.model),
            latency_ms=int((time.monotonic() - started) * 1000),
            attempts=attempts,
            request_id=getattr(raw, "_request_id", None),
        )

    def _emit(
        self,
        purpose: str,
        started: float,
        attempt: int,
        *,
        outcome: str,
        error: LLMError | None = None,
        response: LLMResponse | None = None,
        delay_s: float | None = None,
    ) -> None:
        if self._on_call is None:
            return
        record: dict[str, Any] = {
            "event": "llm_call",
            "purpose": purpose,
            "model": self.settings.model,
            "attempt": attempt,
            "outcome": outcome,
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
        if response is not None:
            record.update(
                stop_reason=response.stop_reason,
                served_by=response.model,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cache_read_tokens=response.usage.cache_read_tokens,
                cache_write_tokens=response.usage.cache_write_tokens,
                request_id=response.request_id,
            )
        if error is not None:
            record.update(error_kind=error.kind, error=error.detail or error.user_message)
        if delay_s is not None:
            record["retry_in_s"] = round(delay_s, 2)
        self._on_call(record)


# ---- helpers (module level so they're easy to unit test) -------------------


def _block_to_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    return block.to_dict()


def _retry_after_seconds(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if not headers:
        return None
    value = headers.get("retry-after")
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


def map_exception(exc: Exception) -> LLMError:
    """Translate SDK exceptions into friendly, classified LLMErrors."""
    if isinstance(exc, LLMError):
        return exc
    detail = str(exc)[:500]
    if isinstance(exc, anthropic.APITimeoutError):
        return LLMError("timeout", "The model took too long to respond. Retrying…", retryable=True, detail=detail)
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMError("connection", "Couldn't reach the Anthropic API. Check the network.", retryable=True, detail=detail)
    if isinstance(exc, anthropic.AuthenticationError):
        return LLMError("auth", "The Anthropic API key is missing or invalid. Check ANTHROPIC_API_KEY.", detail=detail)
    if isinstance(exc, anthropic.PermissionDeniedError):
        return LLMError("permission", "This API key isn't allowed to use the selected model.", detail=detail)
    if isinstance(exc, anthropic.NotFoundError):
        return LLMError("not_found", "The selected model wasn't found. Check ANTHROPIC_MODEL.", detail=detail)
    if isinstance(exc, anthropic.RateLimitError):
        return LLMError("rate_limit", "Rate limited by the API. Backing off and retrying…", retryable=True, detail=detail)
    if isinstance(exc, anthropic.APIStatusError):
        status = exc.status_code
        if status == 402 or (status == 400 and "credit balance" in detail.lower()):
            return LLMError(
                "credits",
                "API credits have run out! TestSmith can't call Claude until more credits are added to the account.",
                detail=detail,
            )
        if status == 529:
            return LLMError("overloaded", "The API is overloaded right now. Retrying…", retryable=True, detail=detail)
        if status >= 500 or status in _RETRYABLE_STATUSES:
            return LLMError("server", f"The API returned a temporary error ({status}).", retryable=True, detail=detail)
        if status == 413:
            return LLMError("too_large", "The request was too large. Try a smaller source file.", detail=detail)
        return LLMError("bad_request", f"The API rejected the request ({status}).", detail=detail)
    if isinstance(exc, anthropic.AnthropicError):
        return LLMError("credentials", "No usable Anthropic credentials were found.", detail=detail)
    return LLMError("unknown", "Unexpected error while calling the model.", detail=f"{type(exc).__name__}: {detail}")
