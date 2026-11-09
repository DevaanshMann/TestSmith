"""Streamlit UI flows (headless via AppTest). None of these call the Anthropic API."""

from __future__ import annotations

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from testsmith.safety.limits import SlidingWindowLimiter
from tests.test_audit_confirm_export import make_result

APP = str(Path(__file__).resolve().parent.parent / "app.py")
RISKY = "import subprocess\n\ndef run(cmd):\n    return subprocess.run(cmd)\n"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("TESTSMITH_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-used")
    st.cache_resource.clear()  # re-read settings with this env
    at = AppTest.from_file(APP, default_timeout=60).run()
    # Any run that gets past confirmations is refused by the rate limiter, so no API call is made.
    at.session_state["run_limiter"] = SlidingWindowLimiter(0, 3600, scope="this session")
    yield at
    st.cache_resource.clear()


def events(at):
    return [e["event"] for e in at.session_state["events"]]


def generate(at):
    next(b for b in at.button if b.label == "Generate tests").click().run()


def test_app_loads_without_errors(app):
    assert not app.exception
    labels = [t.label for t in app.tabs]
    assert [label.split(" ", 1)[1] for label in labels] == ["Run", "Final Tests", "Bugs Found", "Audit Log", "About"]
    assert all(label.startswith(":material/") for label in labels)


def test_extra_iterations_need_confirmation_and_cancel_is_logged(app):
    app.sidebar.number_input[0].set_value(8).run()
    generate(app)
    assert any("8 test runs requested" in w.value for w in app.warning)
    next(b for b in app.button if b.label == "Cancel").click().run()
    assert events(app)[-2:] == ["confirmation_requested", "confirmation"]
    assert app.session_state["events"][-1]["decision"] == "declined"
    assert not any(b.label == "Confirm and run" for b in app.button)


def test_approving_proceeds_to_launch(app):
    app.sidebar.number_input[0].set_value(8).run()
    generate(app)
    next(b for b in app.button if b.label == "Confirm and run").click().run()
    decisions = [e.get("decision") for e in app.session_state["events"] if e["event"] == "confirmation"]
    assert decisions == ["approved"]
    assert events(app)[-1] == "run_refused"  # reached the launch step (then hit the test rate limit)
    assert any("Run limit reached" in e.value for e in app.error)


def test_confirmation_expires_when_inputs_change(app):
    app.sidebar.number_input[0].set_value(8).run()
    generate(app)
    app.sidebar.slider[0].set_value(75).run()  # change coverage target
    assert app.session_state["pending"] is None
    assert app.session_state["events"][-1]["decision"] == "expired"


def test_risky_pasted_source_needs_confirmation(app):
    app.segmented_control[0].set_value("Paste").run()
    app.text_area[0].set_value(RISKY).run()
    generate(app)
    assert any("risky operations" in w.value and "subprocess" in w.value for w in app.warning)
    assert app.session_state["events"][-1]["kinds"] == ["risky_source"]


def test_invalid_source_is_refused_with_friendly_error(app):
    app.segmented_control[0].set_value("Paste").run()
    app.text_area[0].set_value("def broken(:\n").run()
    generate(app)
    assert any("syntax error on line 1" in e.value for e in app.error)
    assert events(app)[-1] == "run_refused"


def test_rate_limit_refuses_run(app):
    generate(app)  # normal sample run: no confirmation needed
    assert any("Run limit reached" in e.value for e in app.error)
    assert app.session_state["events"][-1]["reason"] == "rate_limited"


def test_edit_and_run_my_version_then_regenerate_needs_confirmation(app):
    app.session_state["result"] = make_result()
    app.session_state["run_count"] = 1
    app.run()
    app.toggle(key="edit_mode").set_value(True).run()
    mine = "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    app.text_area(key="editor_1").set_value(mine).run()
    next(b for b in app.button if b.label == "Run my version").click().run()

    assert app.session_state["edited_tests"] == mine
    user_run = app.session_state["user_run"]
    assert user_run.status == "passed" and user_run.coverage_percent == 100.0
    assert "user_tests_run" in events(app)

    generate(app)  # edited tests exist -> overwrite needs confirmation
    assert "overwrite_edited_tests" in app.session_state["events"][-1]["kinds"]


def test_unsafe_edits_are_not_run(app):
    app.session_state["result"] = make_result()
    app.session_state["run_count"] = 1
    app.run()
    app.toggle(key="edit_mode").set_value(True).run()
    app.text_area(key="editor_1").set_value("import socket\n\ndef test_x():\n    pass\n").run()
    next(b for b in app.button if b.label == "Run my version").click().run()
    assert any("can't run until these are fixed" in e.value for e in app.error)
    assert events(app)[-1] == "user_tests_rejected"


def test_audit_file_is_written(app, tmp_path):
    generate(app)
    text = (tmp_path / "audit.jsonl").read_text()
    assert '"run_refused"' in text and app.session_state["session_id"] in text
    assert "sk-ant-test-not-used" not in text


# ---- full run through the UI with a scripted fake Claude ---------------------------------

CALC = "def add(a, b):\n    return a + b\n\ndef sign(x):\n    if x < 0:\n        return -1\n    return 1\n"
V1 = "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
V2 = V1 + "\nfrom calc import sign\n\ndef test_sign():\n    assert sign(-5) == -1\n    assert sign(5) == 1\n"
PLAN = {"happy_path": ["add numbers"], "edge_cases": ["negative input"]}


def fake_claude_script():
    from tests.fakes import api_error, tool_message

    def write(content, filename="test_calc.py", tid="w"):
        return tool_message("write_tests", {"filename": filename, "test_plan": PLAN,
                                            "rationale": "Cover more of the module.", "content": content}, tool_id=tid)

    bug = {"function_name": "sign", "description": "sign(0) returns 1 but zero has no sign",
           "evidence": "sign(0) == 1"}
    return [
        api_error(429, retry_after="0"),                       # rate limited once, then retried
        tool_message("read_source", {"path": "calc.py"}, tool_id="r"),
        write(V1, filename="tests.py", tid="bad"),             # invalid -> sent back for correction
        write(V1, tid="w1"),
        tool_message("run_tests", {}, tool_id="run1"),
        tool_message("report_bug", bug, tool_id="b"),
        write(V2, tid="w2"),
        tool_message("run_tests", {}, tool_id="run2"),
    ]


@pytest.fixture
def app_with_fake_claude(tmp_path, monkeypatch):
    from testsmith.llm.client import LLMClient
    from tests.fakes import FakeMessagesAPI

    api = FakeMessagesAPI(fake_claude_script())
    monkeypatch.setattr(LLMClient, "_build_sdk", staticmethod(lambda settings: api))
    monkeypatch.setenv("TESTSMITH_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-used")
    st.cache_resource.clear()
    at = AppTest.from_file(APP, default_timeout=120).run()
    yield at, api
    st.cache_resource.clear()


def test_full_run_through_the_ui(app_with_fake_claude):
    at, api = app_with_fake_claude
    at.segmented_control[0].set_value("Paste").run()
    at.text_input[0].set_value("calc").run()
    at.text_area[0].set_value(CALC).run()
    generate(at)

    assert not at.exception
    assert api.script == []  # every scripted response was used
    assert any("Coverage target reached" in s.value for s in at.success)

    metrics = {m.label: (m.value, m.delta) for m in at.metric}
    assert metrics["Coverage"] == ("100.0%", "+50.0 pts")
    assert metrics["Test runs"][0] == "2 / 5"
    assert metrics["Bugs found"][0] == "1"

    labels = [e.label for e in at.expander]
    assert any(label.startswith("Run 1 — coverage 50.0%") for label in labels)
    assert any(label.startswith("Run 2 — coverage 100.0%") and "best" in label for label in labels)

    steps = " ".join(m.value for m in at.markdown)
    assert "Rejected `write_tests`" in steps          # validation failure shown to the user
    assert "Reported a likely bug" in steps
    assert any(t.label.endswith("Bugs Found (1)") for t in at.tabs)
    assert any("sign" in w.value for w in at.warning)  # bug card in Bugs Found tab
    assert any(c.value == V2.rstrip() for c in at.code)  # final tests shown (st.code drops the final newline)

    kinds = [e["event"] for e in at.session_state["events"]]
    assert "llm_call" in kinds and kinds.count("iteration") == 2
    retry = next(e for e in at.session_state["events"] if e["event"] == "llm_call")
    assert retry["outcome"] == "retry" and retry["error_kind"] == "rate_limit"


def test_switching_samples_clears_instructions(app):
    app.pills[0].set_value("Work incrementally").run()
    assert "deposit()" in app.session_state["instructions"]
    app.selectbox(key="sample_name").set_value("inventory").run()
    assert app.session_state["instructions"] == ""
    app.pills[0].set_value("Work incrementally").run()  # examples now use inventory's functions
    assert "add_item()" in app.session_state["instructions"]
