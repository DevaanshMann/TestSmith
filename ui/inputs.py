"""Source input (sample / upload / paste), instructions, and pre-run checks."""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

import streamlit as st

from testsmith.config import Settings
from testsmith.safety.validator import Finding, validate_source
from testsmith.sandbox.runner import SandboxInputError, check_module_name

SAMPLES_DIR = Path(__file__).resolve().parent.parent / "samples"

SAMPLE_BLURBS = {
    "bank_account": "Deposits, withdrawals, daily limits, transfers, interest",
    "inventory": "Stock tracking (contains a subtle bug for TestSmith to find)",
    "order_pricing": "Larger pricing engine: coupons, tax, shipping (≈240 lines)",
    "string_utils": "Slugify, truncate, palindromes, word counts",
}

EXAMPLE_INSTRUCTIONS = {
    "Work incrementally": "Work incrementally: first cover only {first}() and {second}(), run the tests, "
                          "then use the coverage report to add the rest.",
    "Edge cases": "Focus on edge cases and boundary values for {first}().",
    "Find bugs": "Be skeptical: compare every function with its docstring and report any mismatch as a bug.",
    "Readable tests": "Prefer small, readable tests with descriptive names; use parametrize for input tables.",
}


@dataclass
class SourceInput:
    module_name: str
    code: str
    origin: str  # "sample" | "upload" | "paste"

    @property
    def filename(self) -> str:
        return f"{self.module_name}.py"


@dataclass
class SourceCheck:
    errors: list[str] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def render_source_picker(settings: Settings) -> SourceInput | None:
    origin = st.segmented_control(
        "Python source", ["Sample", "Upload", "Paste"], default="Sample", required=True, key="origin",
    )
    if origin == "Upload":
        return _upload(settings)
    if origin == "Paste":
        return _paste()
    return _sample()


def _sample() -> SourceInput | None:
    names = sorted(p.stem for p in SAMPLES_DIR.glob("*.py"))
    if not names:
        st.info("No sample files found.")
        return None
    default = names.index("bank_account") if "bank_account" in names else 0
    name = st.selectbox(
        "Sample module", names, index=default,
        format_func=lambda n: f"{n}.py — {SAMPLE_BLURBS.get(n, '')}".rstrip(" —"),
    )
    code = (SAMPLES_DIR / f"{name}.py").read_text(encoding="utf-8")
    with st.expander(f"View {name}.py ({len(code.splitlines())} lines)"):
        st.code(code, language="python", line_numbers=True)
    return SourceInput(name, code, "sample")


def _upload(settings: Settings) -> SourceInput | None:
    uploaded = st.file_uploader("Upload a .py file", type=["py"], help=f"Max {settings.max_upload_bytes // 1024} KB")
    if uploaded is None:
        return None
    raw = uploaded.getvalue()
    if len(raw) > settings.max_upload_bytes:
        st.error(f"That file is {len(raw) // 1024} KB; the limit is {settings.max_upload_bytes // 1024} KB.")
        return None
    try:
        code = raw.decode("utf-8")
    except UnicodeDecodeError:
        st.error("The file isn't valid UTF-8 text.")
        return None
    return SourceInput(Path(uploaded.name).stem, code, "upload")


def _paste() -> SourceInput | None:
    name = st.text_input("Module name", value="my_module", help="Used as the file name, e.g. my_module.py")
    code = st.text_area("Paste Python code", height=260, placeholder="def add(a, b):\n    return a + b")
    if not code.strip():
        return None
    return SourceInput(name.strip().removesuffix(".py"), code, "paste")


def public_functions(code: str) -> list[str]:
    """Public function and method names in source order (used to tailor example instructions)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    names: list[str] = []
    for node in tree.body:
        members = node.body if isinstance(node, ast.ClassDef) else [node]
        for member in members:
            if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) or member.name.startswith("_"):
                continue
            if any(isinstance(d, ast.Name) and d.id == "property" for d in member.decorator_list):
                continue
            names.append(member.name)
    return names


def render_instructions(source: SourceInput | None) -> str:
    names = public_functions(source.code) if source else []
    first = names[0] if names else "the main function"
    second = names[1] if len(names) > 1 else first

    def use_example() -> None:
        choice = st.session_state.get("example_choice")
        if choice:
            text = EXAMPLE_INSTRUCTIONS[choice].format(first=first, second=second)
            st.session_state.instructions = text.replace("the main function()", "the main function")
            st.session_state.example_choice = None

    st.text_area(
        "Instructions (optional)", key="instructions", height=90, max_chars=1000,
        placeholder="e.g. Focus on edge cases for withdraw(), or: target 95% coverage",
    )
    st.pills("Try an example", list(EXAMPLE_INSTRUCTIONS), key="example_choice", on_change=use_example)
    return st.session_state.instructions


def check_source(source: SourceInput, settings: Settings) -> SourceCheck:
    """Problems that block a run (errors) and risky code that needs confirmation (warnings)."""
    check = SourceCheck()
    if not source.code.strip():
        check.errors.append("The source file is empty.")
        return check
    if len(source.code.encode("utf-8")) > settings.max_upload_bytes:
        check.errors.append(f"Source is larger than {settings.max_upload_bytes // 1024} KB.")
    try:
        check_module_name(source.module_name)
    except SandboxInputError as exc:
        check.errors.append(str(exc))
    try:
        tree = ast.parse(source.code)
    except SyntaxError as exc:
        check.errors.append(f"The source has a syntax error on line {exc.lineno}: {exc.msg}.")
        return check
    if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) for n in tree.body):
        check.errors.append("The source defines no functions or classes to test.")
    check.warnings = validate_source(source.code).findings
    return check
