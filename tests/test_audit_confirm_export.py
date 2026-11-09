"""Audit log, confirmation rules, rate limiting, and zip export."""

from __future__ import annotations

import io
import json
import threading
import zipfile

import pytest

from testsmith.agent.actions import TestPlan
from testsmith.agent.loop import SessionResult
from testsmith.agent.state import BugReport, IterationRecord, SessionState
from testsmith.audit.log import AuditLogger, read_jsonl, redact
from testsmith.export.bundle import build_zip, results_markdown
from testsmith.llm.client import UsageTracker
from testsmith.safety.confirmations import ConfirmationKind, request_fingerprint, required_confirmations
from testsmith.safety.limits import BusyError, ConcurrencyGate, RateLimitedError, SlidingWindowLimiter
from testsmith.safety.validator import validate_source
from testsmith.sandbox.report import CoverageData, TestCounts
from testsmith.sandbox.runner import RunResult

# ---- audit log -----------------------------------------------------------------


def test_records_are_appended_as_jsonl_with_context(tmp_path):
    log = AuditLogger(tmp_path / "logs" / "audit.jsonl", session_id="abc")
    log.record({"event": "action_finished", "tool": "run_tests", "duration_ms": 12}, run=1)
    log.record({"event": "llm_call", "outcome": "ok"}, run=1)
    rows = read_jsonl(tmp_path / "logs" / "audit.jsonl")
    assert [r["event"] for r in rows] == ["action_finished", "llm_call"]
    assert all(r["session_id"] == "abc" and r["run"] == 1 and "ts" in r for r in rows)


def test_secrets_are_redacted_everywhere(tmp_path):
    log = AuditLogger(tmp_path / "audit.jsonl", session_id="s")
    log.record({
        "event": "x",
        "api_key": "sk-ant-abcdef123456",
        "error": "auth failed for key sk-ant-api03-SECRETSECRET",
        "nested": {"Authorization": "Bearer abc", "input_tokens": 5},
    })
    text = (tmp_path / "audit.jsonl").read_text()
    assert "sk-ant" not in text and "SECRETSECRET" not in text and "Bearer abc" not in text
    assert '"input_tokens": 5' in text  # numbers with "token" in the key are kept


def test_long_values_are_truncated():
    assert redact("x" * 5000).endswith("…[truncated]")
    assert len(redact("x" * 5000)) < 2100


def test_unwritable_log_does_not_crash(tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("file, not directory")
    log = AuditLogger(blocker / "audit.jsonl", session_id="s")
    log.record({"event": "x"})
    assert log.write_errors == 1 and len(log.records) == 1


def test_concurrent_writers_produce_valid_lines(tmp_path):
    path = tmp_path / "audit.jsonl"
    loggers = [AuditLogger(path, session_id=str(i)) for i in range(4)]

    def write(logger):
        for n in range(50):
            logger.record({"event": "e", "n": n, "padding": "y" * 500})

    threads = [threading.Thread(target=write, args=(lg,)) for lg in loggers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = path.read_text().splitlines()
    assert len(lines) == 200
    assert all(json.loads(line)["event"] == "e" for line in lines)


def test_download_contains_only_this_session_and_run(tmp_path):
    path = tmp_path / "audit.jsonl"
    mine, other = AuditLogger(path, "mine"), AuditLogger(path, "other")
    mine.record({"event": "a"}, run=1)
    mine.record({"event": "b"}, run=2)
    other.record({"event": "c"}, run=1)
    assert [json.loads(x)["event"] for x in mine.to_jsonl().splitlines()] == ["a", "b"]
    assert [json.loads(x)["event"] for x in mine.to_jsonl(run=2).splitlines()] == ["b"]


# ---- confirmations -------------------------------------------------------------


def kinds(requests):
    return {r.kind for r in requests}


def test_no_confirmation_for_a_normal_run():
    assert required_confirmations(source_findings=[], max_iterations=5, default_max_iterations=5, has_user_edits=False) == []


def test_each_rule_triggers_its_confirmation():
    findings = validate_source("import subprocess\nsubprocess.run(['ls'])\n").findings
    requests = required_confirmations(source_findings=findings, max_iterations=8, default_max_iterations=5,
                                      has_user_edits=True)
    assert kinds(requests) == {
        ConfirmationKind.RISKY_SOURCE, ConfirmationKind.EXTRA_ITERATIONS, ConfirmationKind.OVERWRITE_EDITED_TESTS,
    }
    risky = next(r for r in requests if r.kind is ConfirmationKind.RISKY_SOURCE)
    assert "subprocess" in risky.detail


def test_fingerprint_changes_when_inputs_change():
    a = request_fingerprint("mod", "code", 90, "instructions")
    assert a == request_fingerprint("mod", "code", 90, "instructions")
    assert a != request_fingerprint("mod", "code!", 90, "instructions")
    assert a != request_fingerprint("mod", "code", 95, "instructions")


# ---- rate limiting -------------------------------------------------------------


def test_sliding_window_limits_and_recovers():
    now = [0.0]
    limiter = SlidingWindowLimiter(2, window_s=60, scope="test", clock=lambda: now[0])
    limiter.acquire()
    limiter.acquire()
    with pytest.raises(RateLimitedError) as info:
        limiter.acquire()
    assert 0 < info.value.retry_after_s <= 61
    now[0] = 61.0
    limiter.acquire()  # window has passed


def test_concurrency_gate_refuses_when_full():
    gate = ConcurrencyGate(1)
    with gate.slot():
        with pytest.raises(BusyError):
            with gate.slot():
                pass
    with gate.slot():  # released again
        pass


# ---- export ----------------------------------------------------------------------


def make_result():
    state = SessionState(module_name="calc", source_code="def add(a, b):\n    return a + b\n",
                         coverage_target=90, max_iterations=5)
    for n, (cov, code) in enumerate([(50.0, "def test_a(): pass\n"), (100.0, "def test_b(): pass\n")], start=1):
        run = RunResult(status="passed", counts=TestCounts(passed=n), coverage=CoverageData(cov, [] if cov == 100 else [2], [1], 2))
        state.iterations.append(IterationRecord(n, run, code, TestPlan(happy_path=["adds"]), "why", None if n == 1 else 50.0))
    state.bugs.append(BugReport("add", "returns wrong sign for negatives", "add(-1, -1) == 2", 1))
    return SessionResult(state, "target_met", "Coverage target reached.", UsageTracker(input_tokens=10, output_tokens=5))


def test_zip_contains_everything():
    data = build_zip(make_result(), audit_jsonl='{"event": "x"}\n', edited_tests="def test_mine(): pass\n")
    names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
    assert names == {
        "test_calc.py", "edited_test_calc.py", "calc.py", "history/run1_test_calc.py", "history/run2_test_calc.py",
        "RESULTS.md", "results.json", "audit_log.jsonl",
    }
    archive = zipfile.ZipFile(io.BytesIO(data))
    assert archive.read("test_calc.py").decode() == "def test_b(): pass\n"  # best run
    results = json.loads(archive.read("results.json"))
    assert [r["coverage"] for r in results["runs"]] == [50.0, 100.0]
    assert results["bugs"][0]["function"] == "add"


def test_results_markdown_marks_best_run_and_bugs():
    md = results_markdown(make_result())
    assert "| 2 ★ | passed |" in md
    assert "### `add`" in md and "add(-1, -1) == 2" in md


def test_zero_limit_refuses_without_crashing():
    with pytest.raises(RateLimitedError):
        SlidingWindowLimiter(0, 60, scope="test").acquire()
