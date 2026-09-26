"""Turn what the user typed into a directory name."""

from __future__ import annotations

import re

INVALID_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
WHITESPACE = re.compile(r"\s+")
MAX_NAME_LEN = 80


def sanitize(raw: str, fallback: str = "Event") -> str:
    """Strip path separators and control characters, collapse spaces, trim dots."""
    cleaned = INVALID_CHARS.sub(" ", raw)
    cleaned = WHITESPACE.sub(" ", cleaned).strip()
    cleaned = cleaned.strip(". ")
    if len(cleaned) > MAX_NAME_LEN:
        cleaned = cleaned[:MAX_NAME_LEN].strip().strip(". ")
    return cleaned or fallback


def unique_name(base: str, taken: set[str]) -> str:
    """``Hangout`` -> ``Hangout (2)`` when the month already has that event."""
    if base not in taken:
        return base
    for number in range(2, 1000):
        candidate = f"{base} ({number})"
        if candidate not in taken:
            return candidate
    raise RuntimeError(f"cannot find a free name for {base!r}")
