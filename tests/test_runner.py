"""Sandbox runner: results parsing and that every isolation guarantee actually holds."""

from __future__ import annotations

import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

from testsmith.config import Settings
from testsmith.sandbox.runner import SandboxInputError, run_tests

SOURCE = textwrap.dedent(
    """
    def add(a, b):
        return a + b

    def clamp(x):
        if x < 0:
            return 0
        return x
    """
)

FAST = Settings(sandbox_timeout_s=15)


def run(test_code: str, source: str = SOURCE, settings: Settings = FAST, module: str = "calc"):
    return run_tests(
        module_name=module,
        source_code=source,
        test_filename=f"test_{module}.py",
        test_code=textwrap.dedent(test_code),
        settings=settings,
    )


# ---- results ----------------------------------------------------------------


def test_all_passing_with_full_coverage():
    result = run(
        """
        from calc import add, clamp
        def test_add(): assert add(1, 2) == 3
        def test_clamp_negative(): assert clamp(-1) == 0
        def test_clamp_positive(): assert clamp(4) == 4
        """
    )
    assert result.status == "passed"
    assert result.counts.passed == 3 and result.counts.failed == 0
    assert result.coverage_percent == 100.0
    assert result.missing_lines == []


def test_failure_details_and_missing_lines():
    result = run(
        """
        from calc import add
        def test_add_wrong(): assert add(2, 2) == 5
        """
    )
    assert result.status == "failed"
    assert result.counts.failed == 1
    failure = result.failures[0]
    assert failure.test_id.endswith("test_add_wrong")
    assert "assert 4 == 5" in failure.traceback
    assert result.missing_lines == [6, 7, 8]  # clamp() body never ran
    assert 0 < result.coverage_percent < 100


def test_source_syntax_error_is_collection_error():
    result = run("from calc import add\ndef test_a(): assert add(1, 1) == 2\n", source="def add(:\n")
    assert result.status == "collection_error"
    assert "SyntaxError" in result.output_tail


def test_no_tests_collected():
    assert run("x = 1\n").status == "no_tests"


@pytest.mark.parametrize("name", ["random", "json", "conftest", "sitecustomize", "my-module"])
def test_unsafe_module_names_are_rejected(name):
    with pytest.raises(SandboxInputError):
        run("def test_a(): pass\n", module=name)


# ---- isolation guarantees ---------------------------------------------------


def test_secrets_are_not_visible_to_tests(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak")
    monkeypatch.setenv("SOME_OTHER_SECRET", "hunter2")
    result = run(
        """
        import os
        def test_env_is_clean():
            assert "ANTHROPIC_API_KEY" not in os.environ
            assert "SOME_OTHER_SECRET" not in os.environ
            assert not any(k.startswith("ANTHROPIC") for k in os.environ)
        """
    )
    assert result.status == "passed", result.output_tail


def test_runs_in_its_own_temp_dir_which_is_deleted(monkeypatch):
    created: list[str] = []
    real_mkdtemp = tempfile.mkdtemp

    def recording_mkdtemp(*args, **kwargs):
        path = real_mkdtemp(*args, **kwargs)
        created.append(path)
        return path

    monkeypatch.setattr(tempfile, "mkdtemp", recording_mkdtemp)
    result = run(
        """
        import os
        from pathlib import Path
        def test_cwd():
            assert Path.cwd().name.startswith("testsmith_")
            assert os.environ["HOME"] == str(Path.cwd())
        """
    )
    assert result.status == "passed", result.output_tail
    assert created and not Path(created[0]).exists()


def test_network_is_blocked():
    result = run(
        """
        import socket, pytest
        def test_no_dns():
            with pytest.raises(PermissionError, match="Network access is disabled"):
                socket.getaddrinfo("example.com", 80)
        def test_no_connect():
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            with pytest.raises(PermissionError):
                s.connect(("93.184.216.34", 80))
        """
    )
    assert result.status == "passed", result.output_tail


def test_infinite_loop_hits_timeout():
    result = run(
        """
        def test_spin():
            while True:
                pass
        """,
        settings=Settings(sandbox_timeout_s=2),
    )
    assert result.status == "timeout"
    assert result.duration_s < 6


def test_file_size_limit():
    result = run(
        """
        import pytest
        def test_big_write(tmp_path):
            with pytest.raises(OSError):
                (tmp_path / "big.bin").write_bytes(b"x" * 5_000_000)
        """,
        settings=Settings(sandbox_timeout_s=15, sandbox_file_size_mb=1),
    )
    assert result.limits["file_size"].startswith("applied")
    assert result.status == "passed", result.output_tail


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="RLIMIT_AS is only enforced on Linux")
def test_memory_limit():
    result = run(
        """
        import pytest
        def test_huge_alloc():
            with pytest.raises(MemoryError):
                bytearray(1_000_000_000)
        """,
        settings=Settings(sandbox_timeout_s=15, sandbox_memory_mb=256),
    )
    assert result.limits["memory"].startswith("applied")
    assert result.status == "passed", result.output_tail


def test_limits_are_reported():
    result = run("def test_a(): pass\n")
    assert set(result.limits) >= {"cpu_seconds", "file_size", "memory", "core_dumps", "processes"}
    assert result.limits["cpu_seconds"].startswith("applied")
