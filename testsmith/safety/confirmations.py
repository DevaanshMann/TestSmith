"""Which runs need explicit user approval, and why.

Pure logic with no UI code: the Streamlit layer shows these as Confirm/Cancel
buttons and logs the decision to the audit log.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum

from testsmith.safety.validator import Finding


class ConfirmationKind(str, Enum):
    RISKY_SOURCE = "risky_source"
    EXTRA_ITERATIONS = "extra_iterations"
    OVERWRITE_EDITED_TESTS = "overwrite_edited_tests"


@dataclass(frozen=True)
class ConfirmationRequest:
    kind: ConfirmationKind
    title: str
    detail: str


def required_confirmations(
    *,
    source_findings: list[Finding],
    max_iterations: int,
    default_max_iterations: int,
    has_user_edits: bool,
) -> list[ConfirmationRequest]:
    """Everything the user must approve before this run starts."""
    requests: list[ConfirmationRequest] = []
    if source_findings:
        listed = "\n".join(f"- {f}" for f in source_findings[:8])
        more = f"\n- …and {len(source_findings) - 8} more" if len(source_findings) > 8 else ""
        requests.append(ConfirmationRequest(
            ConfirmationKind.RISKY_SOURCE,
            "This source code uses risky operations",
            "Generated tests will import and execute this module. It runs in a sandbox (no network, no secrets, "
            f"resource limits), but only continue if you trust it:\n{listed}{more}",
        ))
    if max_iterations > default_max_iterations:
        requests.append(ConfirmationRequest(
            ConfirmationKind.EXTRA_ITERATIONS,
            f"{max_iterations} test runs requested (default {default_max_iterations})",
            "More runs can mean more API calls and a longer wait.",
        ))
    if has_user_edits:
        requests.append(ConfirmationRequest(
            ConfirmationKind.OVERWRITE_EDITED_TESTS,
            "Replace your edited test file?",
            "You edited the generated tests. Generating again replaces them; download your version first to keep it.",
        ))
    return requests


def request_fingerprint(*parts: object) -> str:
    """Identifies one exact run request, so an approval can't be reused after inputs change."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(repr(part).encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()[:16]
