"""Runtime settings, read from environment variables (and .env locally).

Streamlit secrets are copied into the environment by app.py before
Settings.from_env() is called, so this module never imports Streamlit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_MODEL = "claude-sonnet-5-5"
VALID_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return max(minimum, int(raw))
    except ValueError as exc:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """All tunable limits in one place. Immutable; use with_overrides() for per-run changes."""

    model: str = DEFAULT_MODEL
    effort: str = "medium"
    max_output_tokens: int = 16_000

    max_iterations: int = 5          # default shown in the UI
    hard_max_iterations: int = 10    # absolute ceiling, even with user confirmation
    coverage_target: int = 90        # percent

    llm_timeout_s: int = 90
    llm_max_retries: int = 4
    llm_backoff_base_s: float = 1.0
    llm_backoff_cap_s: float = 30.0
    refusal_fallback: bool = True

    sandbox_timeout_s: int = 20
    sandbox_cpu_s: int = 30
    sandbox_memory_mb: int = 256    # per test run; keep (max_concurrent_runs x this) under the host's RAM
    sandbox_file_size_mb: int = 10

    max_upload_bytes: int = 51_200
    session_token_budget: int = 250_000
    daily_token_budget: int = 3_000_000
    max_runs_per_session_per_hour: int = 6
    max_runs_per_hour_global: int = 60
    max_concurrent_runs: int = 2

    audit_log_path: Path = Path("logs/audit.jsonl")

    @classmethod
    def from_env(cls, dotenv: bool = True) -> "Settings":
        """Build settings from env vars, loading .env first when present."""
        if dotenv:
            load_dotenv(override=False)
        effort = (os.getenv("TESTSMITH_EFFORT") or "").strip().lower() or cls.effort
        if effort not in VALID_EFFORTS:
            raise ValueError(f"TESTSMITH_EFFORT must be one of {VALID_EFFORTS}, got {effort!r}")
        return cls(
            model=os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL,
            effort=effort,
            max_iterations=_env_int("TESTSMITH_MAX_ITERATIONS", cls.max_iterations, 1),
            hard_max_iterations=_env_int("TESTSMITH_HARD_MAX_ITERATIONS", cls.hard_max_iterations, 1),
            coverage_target=min(100, _env_int("TESTSMITH_COVERAGE_TARGET", cls.coverage_target, 1)),
            llm_timeout_s=_env_int("TESTSMITH_LLM_TIMEOUT", cls.llm_timeout_s, 5),
            llm_max_retries=_env_int("TESTSMITH_LLM_MAX_RETRIES", cls.llm_max_retries, 0),
            refusal_fallback=_env_bool("TESTSMITH_REFUSAL_FALLBACK", cls.refusal_fallback),
            sandbox_timeout_s=_env_int("TESTSMITH_SANDBOX_TIMEOUT", cls.sandbox_timeout_s, 1),
            sandbox_memory_mb=_env_int("TESTSMITH_SANDBOX_MEMORY_MB", cls.sandbox_memory_mb, 64),
            max_upload_bytes=_env_int("TESTSMITH_MAX_UPLOAD_BYTES", cls.max_upload_bytes, 1),
            session_token_budget=_env_int("TESTSMITH_SESSION_TOKEN_BUDGET", cls.session_token_budget, 1),
            daily_token_budget=_env_int("TESTSMITH_DAILY_TOKEN_BUDGET", cls.daily_token_budget, 1),
            max_runs_per_session_per_hour=_env_int("TESTSMITH_RUNS_PER_SESSION_HOUR", cls.max_runs_per_session_per_hour, 1),
            max_runs_per_hour_global=_env_int("TESTSMITH_RUNS_PER_HOUR", cls.max_runs_per_hour_global, 1),
            max_concurrent_runs=_env_int("TESTSMITH_MAX_CONCURRENT_RUNS", cls.max_concurrent_runs, 1),
            audit_log_path=Path(os.getenv("TESTSMITH_AUDIT_LOG", str(cls.audit_log_path))),
        )

    def with_overrides(self, **changes: object) -> "Settings":
        """Return a copy with the given fields changed (e.g. from UI sliders)."""
        return replace(self, **changes)


def has_credentials() -> bool:
    """True if any credential source the Anthropic SDK understands is configured.

    Covers an API key, a bearer token, a CLI login profile, or workload identity
    federation, so the UI can show a friendly message instead of an SDK error.
    """
    if os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN") or os.getenv("ANTHROPIC_PROFILE"):
        return True
    federation = ("ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_ORGANIZATION_ID", "ANTHROPIC_SERVICE_ACCOUNT_ID")
    has_token = os.getenv("ANTHROPIC_IDENTITY_TOKEN_FILE") or os.getenv("ANTHROPIC_IDENTITY_TOKEN")
    if all(os.getenv(v) for v in federation) and has_token:
        return True
    return (Path.home() / ".config" / "anthropic").is_dir()
