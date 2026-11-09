"""Pydantic models for every action the agent can take.

The JSON schemas sent to the model as tool definitions are generated from these
models, so what Claude is told and what we validate can never drift apart.
"""

from __future__ import annotations

import copy
from typing import Any, ClassVar, Union

from pydantic import BaseModel, ConfigDict, Field

IDENTIFIER = r"^[A-Za-z_][A-Za-z0-9_.]*$"
TEST_FILENAME = r"^test_[A-Za-z0-9_]{1,60}\.py$"


class _Action(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: ClassVar[str] = ""

    @classmethod
    def tool_description(cls) -> str:
        return (cls.__doc__ or "").strip()


class ReadSource(_Action):
    """Read the uploaded Python module under test. Returns the source with line numbers.
    Call this first if you have not seen the source yet."""

    tool_name: ClassVar[str] = "read_source"

    path: str = Field(min_length=1, max_length=120, description="File name of the module under test, e.g. bank_account.py")


class TestPlan(BaseModel):
    """The test plan you must write BEFORE writing test code."""

    __test__: ClassVar[bool] = False  # not a pytest test class
    model_config = ConfigDict(extra="forbid")

    happy_path: list[str] = Field(min_length=1, max_length=20, description="Normal-use behaviours to verify")
    edge_cases: list[str] = Field(default_factory=list, max_length=20, description="Empty inputs, zero, unicode, large values, etc.")
    error_cases: list[str] = Field(default_factory=list, max_length=20, description="Inputs that must raise, with the expected exception")
    boundaries: list[str] = Field(default_factory=list, max_length=20, description="Off-by-one and limit values")


class WriteTests(_Action):
    """Create or fully replace the pytest test file. Always send the COMPLETE file, never a diff.
    Fill test_plan first; the code must implement that plan."""

    tool_name: ClassVar[str] = "write_tests"

    filename: str = Field(pattern=TEST_FILENAME, description="Must look like test_<module>.py")
    test_plan: TestPlan
    rationale: str = Field(min_length=10, max_length=2000, description="What changed since the last version and why")
    content: str = Field(min_length=20, max_length=60_000, description="Full contents of the test file")


class RunTests(_Action):
    """Run the current test file with pytest under coverage in the sandbox. Returns pass/fail
    counts, trimmed failure tracebacks, coverage percent, and uncovered line numbers."""

    tool_name: ClassVar[str] = "run_tests"


class ReportBug(_Action):
    """Report a likely bug in the SOURCE code, when a correct test fails because the code is wrong.
    Do not weaken or delete the test to make it pass; report the bug instead."""

    tool_name: ClassVar[str] = "report_bug"

    function_name: str = Field(pattern=IDENTIFIER, max_length=120)
    description: str = Field(min_length=10, max_length=1500, description="What the code does wrong")
    evidence: str = Field(min_length=5, max_length=3000, description="The failing assertion or input/output that shows it")


class Finish(_Action):
    """Stop the session: the coverage target is met, or no further progress is possible."""

    tool_name: ClassVar[str] = "finish"

    summary: str = Field(min_length=10, max_length=2000)


Action = Union[ReadSource, WriteTests, RunTests, ReportBug, Finish]

TOOL_MODELS: dict[str, type[_Action]] = {
    m.tool_name: m for m in (ReadSource, WriteTests, RunTests, ReportBug, Finish)
}


def tool_definitions() -> list[dict[str, Any]]:
    """Anthropic tool definitions, generated from the pydantic models (deterministic order)."""
    return [
        {"name": name, "description": model.tool_description(), "input_schema": _input_schema(model)}
        for name, model in TOOL_MODELS.items()
    ]


def _input_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})
    schema = _inline_refs(schema, defs)
    schema.pop("description", None)  # duplicate of the tool description
    schema.setdefault("properties", {})
    _strip_titles(schema)
    return schema


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            return _inline_refs(copy.deepcopy(defs[ref.rsplit("/", 1)[-1]]), defs)
        return {k: _inline_refs(v, defs) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(v, defs) for v in node]
    return node


def _strip_titles(node: Any) -> None:
    if isinstance(node, dict):
        node.pop("title", None)
        props = node.get("properties")
        if isinstance(props, dict):
            for value in props.values():
                _strip_titles(value)
        for key in ("items", "anyOf"):
            child = node.get(key)
            if isinstance(child, dict):
                _strip_titles(child)
            elif isinstance(child, list):
                for c in child:
                    _strip_titles(c)
