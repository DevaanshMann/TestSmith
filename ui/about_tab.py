"""About tab: what TestSmith does and how it works."""

from __future__ import annotations

import streamlit as st

FLOW = """
digraph {
  rankdir=LR; bgcolor="transparent";
  node [shape=box, style="rounded,filled", fillcolor="#eef3fc", color="#2f6fdb", fontname="Helvetica", fontsize=11];
  edge [color="#555555", fontname="Helvetica", fontsize=9];
  user [label="Your module\\n+ instructions", fillcolor="#ffffff"];
  llm [label="Claude\\n(plans + writes tests)"];
  check [label="Validator\\n(schema + AST safety)"];
  sandbox [label="Sandbox\\n(pytest + coverage)"];
  result [label="Tests, coverage,\\nbugs, audit log", fillcolor="#ffffff"];
  user -> llm;
  llm -> check [label="tool call"];
  check -> llm [label="rejected:\\nfix it", style=dashed];
  check -> sandbox [label="valid"];
  sandbox -> llm [label="failures +\\nuncovered lines"];
  sandbox -> result [label="target met"];
}
"""


def render_about_tab() -> None:
    st.markdown(
        """
### Why TestSmith?
Writing unit tests is tedious, so students and developers skip it: coverage stays low and bugs ship.
Asking an LLM for tests helps, but you get **untested** test code. TestSmith closes the loop: it
generates tests, **actually runs them**, measures coverage, and refines them until the target is met.
"""
    )
    st.graphviz_chart(FLOW)
    st.markdown(
        """
### How a session works
1. **Read** – Claude reads your module with line numbers.
2. **Plan + write** – it writes a test plan (happy path, edge cases, errors, boundaries), then the pytest file.
3. **Validate** – every action is checked against a schema, and test code is scanned for dangerous calls.
   Invalid actions are sent back to Claude to fix (up to 2 times).
4. **Run** – tests run in an isolated sandbox with coverage.py.
5. **Refine** – failures and uncovered lines (with their source) go back to Claude, which fixes wrong tests,
   reports real **source bugs** instead of weakening tests, and adds tests for missing lines.
6. **Stop** – when coverage ≥ target with no failures, or the run budget is used, or progress stalls.

### Safety
- Generated tests are statically checked (no `subprocess`, sockets, `eval`/`exec`, file writes outside `tmp_path`, …).
- Each run uses a fresh temp folder, a timeout, CPU/memory/file-size limits (Linux), no network, and an
  environment with **no secrets** in it.
- Token budgets per session and per day, plus a per-session run limit.
- Every LLM call and action is recorded in the **Audit Log** tab.

### Good to know
On small modules Claude often reaches the target in a single run. The refinement loop matters most when
tests fail, the module has bugs, or you ask it to work incrementally (try the *Work incrementally* example).
"""
    )
