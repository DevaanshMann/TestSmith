"""Assorted string helpers."""

from __future__ import annotations

import re
import unicodedata


def slugify(text: str, max_length: int = 50) -> str:
    """Lowercase ASCII slug: 'Héllo, World!' -> 'hello-world'. Never ends with '-'."""
    if max_length < 1:
        raise ValueError("max_length must be at least 1")
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    return slug[:max_length].rstrip("-")


def truncate(text: str, width: int, suffix: str = "...") -> str:
    """Shorten text to at most `width` characters, ending with `suffix` if cut."""
    if width < len(suffix):
        raise ValueError("width must be at least the suffix length")
    if len(text) <= width:
        return text
    return text[: width - len(suffix)] + suffix


def is_palindrome(text: str) -> bool:
    """True if text reads the same backwards, ignoring case, spaces, and punctuation."""
    cleaned = [c.lower() for c in text if c.isalnum()]
    return bool(cleaned) and cleaned == cleaned[::-1]


def word_frequencies(text: str) -> dict[str, int]:
    """Count words (letters, digits, apostrophes), case-insensitively."""
    counts: dict[str, int] = {}
    for word in re.findall(r"[a-z0-9']+", text.lower()):
        word = word.strip("'")
        if word:
            counts[word] = counts.get(word, 0) + 1
    return counts


def camel_to_snake(name: str) -> str:
    """'parseHTTPResponse' -> 'parse_http_response'."""
    if not name:
        return ""
    step = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    step = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", step)
    return step.lower()
