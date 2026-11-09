"""AST safety validator: catches dangerous code without rejecting normal tests."""

from __future__ import annotations

import textwrap

import pytest

from testsmith.safety.validator import validate_source, validate_tests

SOURCE = textwrap.dedent(
    """
    import time

    class BankAccount:
        def withdraw(self, amount):
            return amount

    def helper():
        return time.time()
    """
)

GOOD_TEST = textwrap.dedent(
    """
    import pytest
    from bank_account import BankAccount

    @pytest.mark.parametrize("amount", [1, 5])
    def test_withdraw(amount):
        assert BankAccount().withdraw(amount) == amount

    def test_writes_inside_tmp_path(tmp_path):
        (tmp_path / "out.txt").write_text("ok")
        with open(tmp_path / "log.txt", "w") as fh:
            fh.write("x")

    def test_string_ops():
        name = "file_name".replace("_", "-")
        assert getattr(BankAccount(), "withdraw")(1) == 1
    """
)


def rules(report):
    return {f.rule for f in report.findings}


def test_clean_test_file_passes():
    report = validate_tests(GOOD_TEST, "bank_account", SOURCE)
    assert report.ok, report.summary()


@pytest.mark.parametrize(
    ("snippet", "rule"),
    [
        ("import subprocess", "dangerous_import"),
        ("import socket", "dangerous_import"),
        ("import requests", "dangerous_import"),
        ("from urllib.request import urlopen", "dangerous_import"),
        ("import ctypes", "dangerous_import"),
        ("import os\nos.system('ls')", "dangerous_call"),
        ("import os as o\no.system('ls')", "dangerous_call"),         # alias evasion
        ("from os import system", "dangerous_import"),
        ("import shutil\nshutil.rmtree('/')", "dangerous_call"),
        ("eval('1+1')", "dangerous_call"),
        ("exec('x=1')", "dangerous_call"),
        ("__import__('os')", "dangerous_call"),
        ("import importlib\nimportlib.import_module('os')", "dangerous_call"),
        ("import os\ngetattr(os, 'system')('ls')", "reflection"),
        ("().__class__.__mro__[1].__subclasses__()", "sandbox_escape"),
        ("x = __builtins__", "sandbox_escape"),
        ("open('/etc/passwd', 'w')", "file_write"),
        ("open('out.txt', mode='a')", "file_write"),
        ("from pathlib import Path\nPath('/tmp/x').write_text('hi')", "file_write"),
        ("import time\ntime.sleep(1)", "dangerous_call"),
    ],
)
def test_dangerous_test_code_is_blocked(snippet, rule):
    code = f"{snippet}\n\ndef test_x():\n    assert True\n"
    report = validate_tests(code, "bank_account", SOURCE)
    assert report.blocked
    assert rule in rules(report), report.summary()


@pytest.mark.parametrize(
    "snippet",
    [
        "monkeypatch.setattr(bank_account, 'helper', lambda: 1)",
        "monkeypatch.setattr(BankAccount, 'withdraw', lambda self, a: 0)",
        "mocker.patch('bank_account.BankAccount.withdraw')",
    ],
)
def test_mocking_code_under_test_is_blocked(snippet):
    code = f"import bank_account\nfrom bank_account import BankAccount\n\ndef test_x(monkeypatch, mocker):\n    {snippet}\n"
    report = validate_tests(code, "bank_account", SOURCE)
    assert "mocks_code_under_test" in rules(report), report.summary()


def test_mocking_a_dependency_is_allowed():
    code = (
        "import bank_account\n\n"
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(bank_account, 'time', None)\n"
        "    monkeypatch.setattr('bank_account.time', None)\n"
    )
    assert validate_tests(code, "bank_account", SOURCE).ok


def test_syntax_error_is_blocked_with_line_number():
    report = validate_tests("def test_x(:\n    pass\n", "m")
    assert rules(report) == {"syntax_error"}
    assert report.findings[0].line == 1


def test_file_without_tests_is_blocked():
    assert "no_tests" in rules(validate_tests("import pytest\n", "m"))


def test_source_findings_are_warnings_not_blocks():
    report = validate_source("import subprocess\nsubprocess.run(['ls'])\n")
    assert report.findings and not report.blocked
    assert all(f.severity == "warn" for f in report.findings)


def test_sleep_is_allowed_in_source_but_not_tests():
    assert validate_source("import time\ntime.sleep(1)\n").ok
