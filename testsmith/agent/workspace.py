"""Per-session working folder: the file-operations domain of the agent.

The uploaded module and every version of the generated test file live here,
so the agent's read_source / write_tests actions are real file operations.
Paths are always derived from validated names, never taken from model output.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path


class Workspace:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or Path(tempfile.mkdtemp(prefix="testsmith_ws_"))).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "history").mkdir(exist_ok=True)

    def write_source(self, filename: str, code: str) -> Path:
        return self._write(filename, code)

    def read_source(self, filename: str) -> str:
        return self._path(filename).read_text(encoding="utf-8")

    def write_tests(self, filename: str, code: str, version: int) -> Path:
        """Write the current test file and keep a numbered copy in history/."""
        self._write(f"history/v{version}_{filename}", code)
        return self._write(filename, code)

    def exists(self, filename: str) -> bool:
        return self._path(filename).is_file()

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, relative: str, code: str) -> Path:
        path = self._path(relative)
        path.write_text(code, encoding="utf-8")
        return path

    def _path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"Path escapes the workspace: {relative}")
        return path
