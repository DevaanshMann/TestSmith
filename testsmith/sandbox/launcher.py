"""Sandbox launcher: apply resource limits to *this* process, then exec the real command.

Run as:  python launcher.py --cpu 30 --mem-mb 512 --fsize-mb 10 -- <python args...>

Limits survive exec, so pytest inherits them. Doing this here instead of in
subprocess's preexec_fn avoids preexec_fn's deadlock risk in threaded apps
like Streamlit. Each limit is applied independently: macOS, for example,
refuses RLIMIT_AS, and that must not stop the other limits from applying.
Applied limits are written to limits.json for the audit log.

This file must stay dependency-free (stdlib only).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

try:
    import resource
except ImportError:  # Windows
    resource = None  # type: ignore[assignment]

MB = 1024 * 1024


def _apply(name: str, value: int) -> str:
    if resource is None:
        return "unsupported (no resource module)"
    limit = getattr(resource, name, None)
    if limit is None:
        return "unsupported on this OS"
    try:
        _, hard = resource.getrlimit(limit)
        if hard != resource.RLIM_INFINITY:
            value = min(value, hard)
        resource.setrlimit(limit, (value, value))
        return f"applied ({value})"
    except (ValueError, OSError) as exc:
        return f"unsupported ({exc.__class__.__name__})"


def apply_limits(cpu_s: int, mem_mb: int, fsize_mb: int, nproc: int | None) -> dict[str, str]:
    applied = {
        "cpu_seconds": _apply("RLIMIT_CPU", cpu_s),
        "file_size": _apply("RLIMIT_FSIZE", fsize_mb * MB),
        "memory": _apply("RLIMIT_AS", mem_mb * MB),
        "core_dumps": _apply("RLIMIT_CORE", 0),
    }
    # RLIMIT_NPROC counts every process of the user; only meaningful on Linux,
    # where the container runs as a dedicated user.
    applied["processes"] = _apply("RLIMIT_NPROC", nproc) if nproc and sys.platform.startswith("linux") else "skipped"
    return applied


def main(argv: list[str]) -> None:
    if "--" not in argv:
        raise SystemExit("usage: launcher.py [limits] -- <python args>")
    split = argv.index("--")
    parser = argparse.ArgumentParser()
    parser.add_argument("--cpu", type=int, required=True)
    parser.add_argument("--mem-mb", type=int, required=True)
    parser.add_argument("--fsize-mb", type=int, required=True)
    parser.add_argument("--nproc", type=int, default=None)
    parser.add_argument("--report", default="limits.json")
    opts = parser.parse_args(argv[:split])

    applied = apply_limits(opts.cpu, opts.mem_mb, opts.fsize_mb, opts.nproc)
    with open(opts.report, "w", encoding="utf-8") as fh:
        json.dump(applied, fh)

    sys.stdout.flush()
    os.execv(sys.executable, [sys.executable, *argv[split + 1 :]])


if __name__ == "__main__":
    main(sys.argv[1:])
