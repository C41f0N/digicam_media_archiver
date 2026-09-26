"""The Textual user interface."""

from __future__ import annotations

__all__ = ["ArchiverApp"]


def __getattr__(name: str):  # pragma: no cover - lazy import keeps --help fast
    if name == "ArchiverApp":
        from .app import ArchiverApp

        return ArchiverApp
    raise AttributeError(name)
