"""Settings parsing and token budgets."""

from __future__ import annotations

import pytest

from testsmith.config import DEFAULT_MODEL, Settings
from testsmith.safety.limits import BudgetExceededError, TokenBudget, global_daily_budget


def test_defaults_when_env_is_empty(monkeypatch):
    for var in ("ANTHROPIC_MODEL", "TESTSMITH_MAX_ITERATIONS", "TESTSMITH_EFFORT"):
        monkeypatch.delenv(var, raising=False)
    s = Settings.from_env(dotenv=False)
    assert s.model == DEFAULT_MODEL
    assert s.max_iterations == 5 and s.coverage_target == 90


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("TESTSMITH_MAX_ITERATIONS", "3")
    monkeypatch.setenv("TESTSMITH_COVERAGE_TARGET", "250")
    s = Settings.from_env(dotenv=False)
    assert s.model == "claude-opus-5-5"
    assert s.max_iterations == 3
    assert s.coverage_target == 100  # clamped


@pytest.mark.parametrize(("var", "value"), [("TESTSMITH_MAX_ITERATIONS", "lots"), ("TESTSMITH_EFFORT", "turbo")])
def test_invalid_env_values_raise_clear_errors(monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    with pytest.raises(ValueError, match=var):
        Settings.from_env(dotenv=False)


def test_with_overrides_returns_new_object():
    base = Settings()
    changed = base.with_overrides(coverage_target=75)
    assert changed.coverage_target == 75 and base.coverage_target == 90


def test_token_budget_raises_after_limit():
    budget = TokenBudget(limit=10)
    budget.ensure_available()
    budget.consume(10)
    assert budget.remaining == 0
    with pytest.raises(BudgetExceededError):
        budget.ensure_available()


def test_global_daily_budget_is_shared():
    assert global_daily_budget(1000) is global_daily_budget(1000)


def test_empty_effort_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("TESTSMITH_EFFORT", "  ")
    assert Settings.from_env(dotenv=False).effort == "medium"
