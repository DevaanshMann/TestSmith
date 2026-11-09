"""Error-handling paths that normal runs rarely hit."""

from __future__ import annotations

import datetime as dt
import random
import textwrap

import anthropic
import httpx2
import pytest

from testsmith.agent.actions import ReportBug, RunTests, WriteTests
from testsmith.agent.executor import Executor, summarize_args
from testsmith.agent.feedback import build_feedback
from testsmith.agent.parser import ParsedCall
from testsmith.agent.state import SessionState
from testsmith.agent.workspace import Workspace
from testsmith.audit import log as audit_log
from testsmith.audit.log import AuditLogger
from testsmith.config import Settings, has_credentials
from testsmith.llm.client import LLMClient, LLMError, map_exception
from testsmith.safety import limits
from testsmith.safety.validator import validate_tests
from testsmith.sandbox.report import CoverageData, Failure, TestCounts, parse_coverage, parse_junit, trim_traceback
from testsmith.sandbox.runner import RunResult, SandboxInputError, _status_for
from tests.fakes import FakeMessagesAPI, text_message

REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

# ---- LLM client ------------------------------------------------------------------


def test_connection_errors_are_retried():
    api = FakeMessagesAPI([anthropic.APIConnectionError(request=REQ), text_message("ok")])
    client = LLMClient(Settings(), messages_api=api, sleep=lambda s: None, rng=random.Random(0))
    assert client.create_message(system="s", messages=[{"role": "user", "content": "x"}]).text == "ok"


@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (anthropic.AnthropicError("no credentials"), "credentials"),
        (RuntimeError("boom"), "unknown"),
        (LLMError("budget", "used up"), "budget"),
    ],
)
def test_unusual_exceptions_map_to_friendly_errors(exc, kind):
    error = map_exception(exc)
    assert error.kind == kind and error.user_message


def test_unparseable_retry_after_falls_back_to_backoff():
    response = httpx2.Response(429, request=REQ, headers={"retry-after": "soon"})
    api = FakeMessagesAPI([anthropic.RateLimitError("rl", response=response, body=None), text_message("ok")])
    sleeps: list[float] = []
    client = LLMClient(Settings(), messages_api=api, sleep=sleeps.append, rng=random.Random(0))
    client.create_message(system="s", messages=[{"role": "user", "content": "x"}])
    assert 0 < sleeps[0] < 1.5


def test_real_sdk_client_is_built_without_sdk_retries(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    client = LLMClient(Settings(llm_timeout_s=42))
    sdk = client._api._client  # the beta.messages resource keeps a reference to its client
    assert sdk.max_retries == 0 and sdk.timeout == 42.0


# ---- credentials ------------------------------------------------------------------

CRED_VARS = ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE", "ANTHROPIC_FEDERATION_RULE_ID",
             "ANTHROPIC_ORGANIZATION_ID", "ANTHROPIC_SERVICE_ACCOUNT_ID", "ANTHROPIC_IDENTITY_TOKEN",
             "ANTHROPIC_IDENTITY_TOKEN_FILE"]


@pytest.fixture
def no_creds(monkeypatch, tmp_path):
    for var in CRED_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.config/anthropic profile


def test_no_credentials_detected(no_creds):
    assert has_credentials() is False


def test_api_key_detected(no_creds, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    assert has_credentials() is True


def test_workload_identity_federation_detected(no_creds, monkeypatch):
    for var in ("ANTHROPIC_FEDERATION_RULE_ID", "ANTHROPIC_ORGANIZATION_ID", "ANTHROPIC_SERVICE_ACCOUNT_ID",
                "ANTHROPIC_IDENTITY_TOKEN"):
        monkeypatch.setenv(var, "x")
    assert has_credentials() is True


# ---- executor -------------------------------------------------------------------

SOURCE = "def add(a, b):\n    return a + b\n"
TESTS = "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"


def make_executor(runner, tmp_path, max_iterations=5):
    state = SessionState(module_name="calc", source_code=SOURCE, coverage_target=90, max_iterations=max_iterations)
    workspace = Workspace(tmp_path / "ws")
    workspace.write_source("calc.py", SOURCE)
    events: list[dict] = []
    return Executor(state, workspace, Settings(), runner=runner, on_event=events.append), state, events


def call(action, name):
    return ParsedCall(tool_use_id="t1", name=name, raw_input={}, action=action)


def write_action():
    return WriteTests(filename="test_calc.py", test_plan={"happy_path": ["adds"]}, rationale="first version", content=TESTS)


def test_handler_crash_becomes_error_result_not_exception(tmp_path):
    def broken_runner(**_):
        raise RuntimeError("disk on fire")

    executor, _, events = make_executor(broken_runner, tmp_path)
    executor.execute(call(write_action(), "write_tests"))
    outcome = executor.execute(call(RunTests(), "run_tests"))
    assert outcome.is_error and "Internal error" in outcome.content
    assert "disk on fire" in events[-1]["internal_error"]  # kept for the audit log, not shown to the model


def test_runner_input_error_is_rejected_cleanly(tmp_path):
    def picky_runner(**_):
        raise SandboxInputError("rename your file")

    executor, _, _ = make_executor(picky_runner, tmp_path)
    executor.execute(call(write_action(), "write_tests"))
    outcome = executor.execute(call(RunTests(), "run_tests"))
    assert outcome.is_error and "rename your file" in outcome.content


def test_no_runs_left_is_rejected(tmp_path):
    def runner(**_):
        return RunResult(status="passed", counts=TestCounts(passed=1), coverage=CoverageData(50.0, [2], [1], 2))

    executor, _, _ = make_executor(runner, tmp_path, max_iterations=1)
    executor.execute(call(write_action(), "write_tests"))
    assert not executor.execute(call(RunTests(), "run_tests")).is_error
    outcome = executor.execute(call(RunTests(), "run_tests"))
    assert outcome.is_error and "No test runs left" in outcome.content


def test_long_args_are_truncated_in_summaries():
    bug = ReportBug(function_name="add", description="d" * 500, evidence="e" * 10)
    assert summarize_args(bug)["description"].endswith("…")
    assert len(summarize_args(bug)["description"]) == 121


def test_workspace_refuses_paths_outside_it(tmp_path):
    with pytest.raises(ValueError, match="escapes"):
        Workspace(tmp_path / "ws").write_source("../outside.py", "x = 1\n")


# ---- feedback -------------------------------------------------------------------


def test_feedback_reports_hidden_failures_and_collection_output():
    state = SessionState(module_name="calc", source_code=SOURCE, coverage_target=90, max_iterations=5)
    failures = [Failure(f"t::test_{i}", "failure", "boom", "E assert 1 == 2") for i in range(8)]
    run = RunResult(status="failed", counts=TestCounts(failed=12, failures=failures))
    assert "(+4 more failures not shown)" in build_feedback(state, run, None)

    broken = RunResult(status="collection_error", output_tail="ImportError: cannot import name 'sub'")
    text = build_feedback(state, broken, -10.0)
    assert "ImportError" in text and "-10.0" in text and "Fix the problem above first" in text


# ---- sandbox reports ------------------------------------------------------------------


def test_damaged_or_missing_reports_return_none(tmp_path):
    assert parse_junit(tmp_path / "missing.xml") is None
    (tmp_path / "bad.xml").write_text("<testsuite><testcase")
    assert parse_junit(tmp_path / "bad.xml") is None
    (tmp_path / "bad.json").write_text("{not json")
    assert parse_coverage(tmp_path / "bad.json", "calc.py") is None
    (tmp_path / "other.json").write_text('{"files": {"other.py": {}}}')
    assert parse_coverage(tmp_path / "other.json", "calc.py") is None


def test_skipped_and_error_cases_are_counted(tmp_path):
    (tmp_path / "r.xml").write_text(textwrap.dedent("""
        <testsuites><testsuite>
          <testcase classname="t" name="ok"/>
          <testcase classname="t" name="skip"><skipped/></testcase>
          <testcase classname="t" name="err"><error message="fixture boom">trace</error></testcase>
        </testsuite></testsuites>
    """))
    counts = parse_junit(tmp_path / "r.xml")
    assert (counts.passed, counts.skipped, counts.errors) == (1, 1, 1)
    assert counts.failures[0].kind == "error"


def test_traceback_trimming_keeps_the_end():
    text = "\n".join(f"line {i}" for i in range(100))
    trimmed = trim_traceback(text)
    assert trimmed.endswith("line 99") and "line 0\n" not in trimmed


@pytest.mark.parametrize(("code", "status"), [(0, "passed"), (1, "failed"), (5, "no_tests"), (2, "collection_error"),
                                                (-9, "crashed"), (3, "crashed")])
def test_exit_code_mapping(code, status):
    assert _status_for(code, None) == status


# ---- validator corner cases ---------------------------------------------------------


@pytest.mark.parametrize(
    ("snippet", "rule"),
    [
        ("import os\nname = 'sys' + 'tem'\ngetattr(os, name)('ls')", "reflection"),
        ("m = 'w'\nopen('x.txt', m)", "file_write"),
        ("from pathlib import Path\n(Path('.') / 'out.txt').write_text('x')", "file_write"),
        ("config_file = None\nconfig_file.write_text('x')", "file_write"),
    ],
)
def test_validator_corner_cases_are_blocked(snippet, rule):
    report = validate_tests(f"{snippet}\n\ndef test_x():\n    pass\n", "calc", SOURCE)
    assert rule in {f.rule for f in report.findings}, report.summary()


def test_validator_tolerates_unparseable_source_for_mocking_rule():
    report = validate_tests("def test_x():\n    pass\n", "calc", source_code="def broken(:\n")
    assert report.ok and report.summary() == "no issues"


# ---- limits & audit ---------------------------------------------------------------------


def test_daily_budget_resets_at_midnight(monkeypatch):
    budget = limits.DailyTokenBudget(100)
    budget.consume(100)
    with pytest.raises(limits.BudgetExceededError):
        budget.ensure_available()

    class Tomorrow(dt.date):
        @classmethod
        def today(cls):
            return dt.date.max

    monkeypatch.setattr(limits, "date", Tomorrow)
    budget.ensure_available()  # new day, budget is fresh
    assert budget.used == 0


def test_shared_gate_and_limiter_are_singletons():
    assert limits.concurrency_gate(3) is limits.concurrency_gate(3)
    assert limits.global_run_limiter(7) is limits.global_run_limiter(7)


def test_audit_file_rotates_when_large(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_log, "MAX_FILE_BYTES", 200)
    log = AuditLogger(tmp_path / "audit.jsonl", "s")
    for i in range(10):
        log.record({"event": "e", "n": i, "pad": "x" * 50})
    assert (tmp_path / "audit.jsonl.1").exists()
    assert (tmp_path / "audit.jsonl").stat().st_size < 400
