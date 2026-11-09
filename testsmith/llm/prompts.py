"""Loads versioned prompt templates from testsmith/prompts/.

Files are named <name>_<version>.md (e.g. system_v1.md). Templates use
string.Template `$placeholders` because test code is full of `{}` braces.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
PROMPT_VERSION = "v1"


@lru_cache(maxsize=None)
def load_prompt(name: str, version: str = PROMPT_VERSION) -> str:
    """Return the raw text of prompts/<name>_<version>.md."""
    path = PROMPTS_DIR / f"{name}_{version}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Prompt template not found: {path.name}")
    return path.read_text(encoding="utf-8").strip()


def render_prompt(name: str, version: str = PROMPT_VERSION, **values: object) -> str:
    """Load a template and fill its $placeholders. Missing values raise KeyError."""
    return Template(load_prompt(name, version)).substitute({k: str(v) for k, v in values.items()})
