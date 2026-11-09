"""Append-only JSONL audit log.

Every LLM call, validation result, action, confirmation, and refusal is
written as one JSON object per line. Records never contain API keys or
prompt text: keys that look like credentials are redacted, `sk-ant-…`
strings are scrubbed anywhere, and long values are truncated.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SECRET_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}")
SECRET_KEY_HINTS = ("api_key", "apikey", "secret", "password", "authorization", "auth_token", "x-api-key")
MAX_VALUE_CHARS = 2000
MAX_FILE_BYTES = 10 * 1024 * 1024

_file_lock = threading.Lock()  # one lock for all sessions writing the same file


def redact(value: Any, key: str = "") -> Any:
    """Remove secrets and cap sizes, recursively."""
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, key) for v in value]
    if isinstance(value, str):
        if any(hint in key.lower() for hint in SECRET_KEY_HINTS):
            return "[REDACTED]"
        value = SECRET_PATTERN.sub("[REDACTED]", value)
        return value if len(value) <= MAX_VALUE_CHARS else value[:MAX_VALUE_CHARS] + "…[truncated]"
    return value


class AuditLogger:
    """Writes one session's events to the shared JSONL file and keeps them in memory."""

    def __init__(self, path: Path, session_id: str) -> None:
        self.path = path
        self.session_id = session_id
        self.records: list[dict[str, Any]] = []
        self.write_errors = 0

    def record(self, event: dict[str, Any], **context: Any) -> dict[str, Any]:
        """Stamp, redact, persist, and return the record. Never raises."""
        record = redact({
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "session_id": self.session_id,
            **context,
            **event,
        })
        self.records.append(record)
        self._append(record)
        return record

    def to_jsonl(self, run: int | None = None) -> str:
        """This session's records (optionally one run) as JSONL text."""
        rows = [r for r in self.records if run is None or r.get("run") == run]
        return "".join(json.dumps(r, default=str) + "\n" for r in rows)

    def _append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, default=str) + "\n"
        try:
            with _file_lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size > MAX_FILE_BYTES:
                    self.path.replace(self.path.with_suffix(self.path.suffix + ".1"))
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line)
        except OSError:
            self.write_errors += 1  # read-only disk etc.: keep the in-memory log working


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL audit file, skipping malformed lines."""
    records = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records
