"""AST-based static checks for uploaded source and LLM-generated tests.

Two policies share one scanner:
- validate_source(): findings are WARNINGS. The user must confirm before running.
- validate_tests():  findings BLOCK execution and are sent back to the LLM.

Static analysis can be evaded by a determined attacker (e.g. building names at
runtime), so this is one layer of defence; the sandbox is the other.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["block", "warn"]

BANNED_MODULES = {
    "subprocess", "socket", "ssl", "requests", "urllib", "urllib3", "http", "httpx", "httpx2",
    "aiohttp", "ftplib", "smtplib", "telnetlib", "ctypes", "cffi", "multiprocessing", "pty",
    "webbrowser", "paramiko", "anthropic",
}
BANNED_CALLS = {
    "os.system", "os.popen", "os.fork", "os.forkpty", "os.kill", "os.killpg", "os.execv",
    "os.execve", "os.execl", "os.execlp", "os.execvp", "os.spawnl", "os.spawnv", "os.spawnve",
    "os.posix_spawn", "os.remove", "os.unlink", "os.rmdir", "os.removedirs", "os.chmod",
    "os.putenv", "shutil.rmtree", "shutil.move", "eval", "exec", "compile", "__import__",
    "importlib.import_module", "builtins.eval", "builtins.exec", "breakpoint", "input",
}
TEST_ONLY_BANNED_CALLS = {"time.sleep", "asyncio.sleep"}
SENSITIVE_MODULES = {"os", "sys", "builtins", "shutil", "importlib", "io"}
ESCAPE_ATTRIBUTES = {
    "__subclasses__", "__globals__", "__builtins__", "__code__", "__closure__", "__mro__",
    "__bases__", "__getattribute__", "f_globals", "f_locals", "gi_frame",
}
SAFE_PATH_ROOTS = {"tmp_path", "tmpdir", "tmp_path_factory", "tmpdir_factory"}
WRITE_METHODS = {"write_text", "write_bytes", "touch", "mkdir", "unlink", "rmdir", "rename"}


@dataclass(frozen=True)
class Finding:
    rule: str
    message: str
    line: int
    severity: Severity

    def __str__(self) -> str:
        return f"line {self.line}: {self.message} [{self.rule}]"


@dataclass
class ValidationReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return any(f.severity == "block" for f in self.findings)

    @property
    def ok(self) -> bool:
        return not self.findings

    def summary(self) -> str:
        return "\n".join(str(f) for f in self.findings) or "no issues"


# ---- public API -------------------------------------------------------------


def validate_source(code: str) -> ValidationReport:
    """Scan uploaded source. Everything is a warning that needs user confirmation."""
    findings = _scan(code, severity="warn", is_test=False, module_under_test=None)
    return ValidationReport(findings)


def validate_tests(code: str, module_under_test: str, source_code: str | None = None) -> ValidationReport:
    """Scan generated tests. Every finding blocks execution.

    source_code lets the mocking rule tell the module's own functions/classes
    (must not be patched) apart from its dependencies (fine to patch).
    """
    defined = defined_names(source_code) if source_code is not None else None
    findings = _scan(code, severity="block", is_test=True, module_under_test=module_under_test, defined=defined)
    if not any(f.rule == "syntax_error" for f in findings) and not _has_test_function(code):
        findings.append(Finding("no_tests", "file defines no test_* functions", 1, "block"))
    return ValidationReport(findings)


# ---- implementation ---------------------------------------------------------


def defined_names(source_code: str) -> set[str]:
    """Top-level functions and classes defined in a module."""
    try:
        tree = ast.parse(source_code)
    except SyntaxError:
        return set()
    return {
        n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }


def _scan(
    code: str,
    *,
    severity: Severity,
    is_test: bool,
    module_under_test: str | None,
    defined: set[str] | None = None,
) -> list[Finding]:
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [Finding("syntax_error", f"syntax error: {exc.msg}", exc.lineno or 1, "block")]
    visitor = _Visitor(severity, is_test, module_under_test, defined)
    visitor.visit(tree)
    return visitor.findings


def _has_test_function(code: str) -> bool:
    tree = ast.parse(code)
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test")
        for node in ast.walk(tree)
    )


class _Visitor(ast.NodeVisitor):
    """Walks the tree, resolving import aliases so `import os as o; o.system()` is caught."""

    def __init__(
        self, severity: Severity, is_test: bool, module_under_test: str | None, defined: set[str] | None
    ) -> None:
        self.severity = severity
        self.is_test = is_test
        self.module = module_under_test
        self.defined = defined
        self.aliases: dict[str, str] = {}
        self.findings: list[Finding] = []
        self.banned_calls = BANNED_CALLS | (TEST_ONLY_BANNED_CALLS if is_test else set())

    def _flag(self, node: ast.AST, rule: str, message: str) -> None:
        self.findings.append(Finding(rule, message, getattr(node, "lineno", 1), self.severity))

    # imports

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_module(node, alias.name)
            self.aliases[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = node.module or ""
        self._check_module(node, base)
        for alias in node.names:
            full = f"{base}.{alias.name}" if base else alias.name
            self._check_module(node, full)
            if full in self.banned_calls:
                self._flag(node, "dangerous_import", f"imports dangerous function {full}")
            self.aliases[alias.asname or alias.name] = full
        self.generic_visit(node)

    def _check_module(self, node: ast.AST, name: str) -> None:
        if name.split(".")[0] in BANNED_MODULES:
            self._flag(node, "dangerous_import", f"imports {name} (process/network/native access)")

    # calls

    def visit_Call(self, node: ast.Call) -> None:
        name = self._resolve(node.func)
        if name in self.banned_calls:
            self._flag(node, "dangerous_call", f"calls {name}()")
        elif name in {"getattr", "setattr", "builtins.getattr"}:
            self._check_reflection(node)
        elif name in {"open", "builtins.open", "io.open"}:
            self._check_open(node)
        elif isinstance(node.func, ast.Attribute) and node.func.attr in WRITE_METHODS:
            self._check_path_write(node)
        if self.is_test:
            self._check_mocking(node, name)
        self.generic_visit(node)

    def _check_reflection(self, node: ast.Call) -> None:
        if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str):
            attr = node.args[1].value
            target = self._resolve(node.args[0]) or ""
            if attr in ESCAPE_ATTRIBUTES or f"{target}.{attr}" in BANNED_CALLS or attr in {"system", "popen", "eval", "exec"}:
                self._flag(node, "reflection", f"uses getattr to reach {attr!r}")
        elif len(node.args) >= 2 and (self._resolve(node.args[0]) or "") in SENSITIVE_MODULES:
            self._flag(node, "reflection", "uses getattr with a computed name on a system module")

    def _check_open(self, node: ast.Call) -> None:
        mode = node.args[1] if len(node.args) >= 2 else next((k.value for k in node.keywords if k.arg == "mode"), None)
        if mode is None:
            return
        if not (isinstance(mode, ast.Constant) and isinstance(mode.value, str)):
            self._flag(node, "file_write", "open() with a non-literal mode")
            return
        if any(c in mode.value for c in "wax+"):
            if not node.args or not self._is_tmp_path(node.args[0]):
                self._flag(node, "file_write", f"open(..., {mode.value!r}) outside tmp_path")

    def _check_path_write(self, node: ast.Call) -> None:
        receiver = node.func.value if isinstance(node.func, ast.Attribute) else None
        if receiver is not None and not self._is_tmp_path(receiver) and self._looks_like_path(receiver):
            self._flag(node, "file_write", f".{node.func.attr}() on a path outside tmp_path")

    def _check_mocking(self, node: ast.Call, name: str | None) -> None:
        """Block patching the module under test: tests must exercise the real code."""
        if not self.module:
            return
        prefix = f"{self.module}."
        is_patch = name is not None and (name.endswith("patch") or name.endswith("patch.object"))
        is_setattr = isinstance(node.func, ast.Attribute) and node.func.attr == "setattr"
        if not (is_patch or is_setattr) or not node.args:
            return
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            if first.value.startswith(prefix) and self._is_own_name(first.value[len(prefix):].split(".")[0]):
                self._flag(node, "mocks_code_under_test", f"patches {first.value}; test the real implementation")
            return
        target = self._resolve(first) or ""
        attr = node.args[1].value if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) else None
        if target == self.module and isinstance(attr, str) and self._is_own_name(attr):
            self._flag(node, "mocks_code_under_test", f"patches {self.module}.{attr}; test the real implementation")
        elif target.startswith(prefix) and self._is_own_name(target[len(prefix):].split(".")[0]):
            self._flag(node, "mocks_code_under_test", f"patches {target}; test the real implementation")

    def _is_own_name(self, name: str) -> bool:
        """True if `name` is defined in the module under test (unknown source: assume yes)."""
        return self.defined is None or name in self.defined

    # attribute access

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in ESCAPE_ATTRIBUTES:
            self._flag(node, "sandbox_escape", f"accesses {node.attr}")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "__builtins__":
            self._flag(node, "sandbox_escape", "accesses __builtins__")

    # helpers

    def _resolve(self, node: ast.AST) -> str | None:
        """Turn `o.system` into `os.system` using the alias table."""
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if not isinstance(node, ast.Name):
            return None
        root = self.aliases.get(node.id, node.id)
        return ".".join([root, *reversed(parts)])

    def _is_tmp_path(self, node: ast.AST) -> bool:
        return any(isinstance(n, ast.Name) and n.id in SAFE_PATH_ROOTS for n in ast.walk(node))

    def _looks_like_path(self, node: ast.AST) -> bool:
        """Only flag receivers that are plausibly filesystem paths (Path(...), a / b, names with 'path'/'file')."""
        if isinstance(node, ast.Call):
            return (self._resolve(node.func) or "").endswith("Path")
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            return True
        name = self._resolve(node) or ""
        return any(word in name.lower() for word in ("path", "file", "dir"))
