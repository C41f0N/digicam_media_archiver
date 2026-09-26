"""Widgets shared by the screens: the preview pane and small formatters."""

from __future__ import annotations

import asyncio
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from ..model import MediaFile, human_size
from ..preview import PreviewError, render_media


def gap_text(gap: timedelta) -> str:
    total_minutes = int(gap.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours}h{minutes:02d}m"
    if hours:
        return f"{hours}h"
    return f"{minutes}m"


def format_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or math.isnan(seconds):
        return "--:--"
    seconds = int(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


@dataclass(frozen=True, slots=True)
class NodeRef:
    """What a tree or list node points at."""

    kind: str  # "event" or "file"
    start: int  # index of the first file of the owning event
    index: int  # index of this file in the global file list


class PreviewPane(Vertical):
    """ASCII rendering of a photo, or a still of a video.

    Colour mode uses upper half blocks with a foreground and background colour
    per cell, which is the most detail a character cell can carry.  Rendering
    happens in a thread and the result is dropped if the cursor moved on.
    """

    DEFAULT_CSS = """
    PreviewPane {
        height: 1fr;
        width: 1fr;
    }
    PreviewPane > .meta {
        height: auto;
        padding: 0 1;
        color: $text-muted;
    }
    PreviewPane > .image {
        height: 1fr;
        width: 1fr;
        text-wrap: nowrap;
        overflow: hidden;
    }
    PreviewPane.empty > .image {
        content-align: center middle;
    }
    """

    def __init__(
        self,
        colour: bool = True,
        frame_fraction: float = 0.2,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.colour = colour
        self.frame_fraction = frame_fraction
        self._path: Path | None = None
        self._is_video = False
        self._token = 0
        self._meta_value = ""
        self._note = ""
        # one thread, one queued job: holding an arrow key must not pile up
        # decodes, and a stale frame is never worth showing
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="preview")
        self._busy = False
        self._queued: tuple[Path, bool, int, int, int] | None = None
        self._warm: tuple[Path, bool, int, int, int] | None = None

    def compose(self) -> ComposeResult:
        yield Static("", classes="meta", id="preview-meta")
        yield Static("", classes="image", id="preview-image")

    @property
    def _image(self) -> Static:
        return self.query_one("#preview-image", Static)

    @property
    def _meta(self) -> Static:
        return self.query_one("#preview-meta", Static)

    def on_mount(self) -> None:
        self._image.update(Text("nothing selected", style="dim"))

    def on_unmount(self) -> None:
        self._token += 1
        self._queued = None
        self._warm = None
        self._executor.shutdown(wait=False, cancel_futures=True)

    def on_resize(self) -> None:
        if self._path is not None:
            self._render_current()

    def clear(self, message: str = "nothing selected") -> None:
        self._path = None
        self._token += 1
        self._queued = None
        self._meta_value = ""
        self._note = ""
        self._meta.update("")
        self._image.update(Text(message, style="dim"))

    def show_file(self, item: MediaFile, extra: str = "", position: str = "") -> None:
        self._path = item.path
        self._is_video = item.is_video
        second = f"{human_size(item.size)}   {item.path.parent.name}"
        if position:
            second += f"   {position}"
        lines = [
            f"{item.name}   {item.taken:%y-%m-%d %H:%M:%S} ({item.stamp_source})",
            second,
        ]
        if extra:
            lines.append(extra)
        self._set_meta("\n".join(lines))
        self._render_current()

    def show_path(self, path: Path, is_video: bool, meta: str) -> None:
        self._path = path
        self._is_video = is_video
        self._set_meta(meta)
        self._render_current()

    def warm(self, item: MediaFile) -> None:
        """Get ready for a file the user is about to reach, if nothing else waits."""
        size = self._image.size
        cols, rows = int(size.width), int(size.height)
        if cols < 4 or rows < 2:
            return
        self._warm = (item.path, item.is_video, cols, rows, self._token + 1)
        self._pump()

    def _set_meta(self, value: str) -> None:
        """Always three lines, so the image area below never jumps around."""
        self._meta_value = value
        lines = value.split("\n")
        while len(lines) < 3:
            lines.append("")
        self._meta.update("\n".join(lines[:3]))

    def _box(self) -> tuple[int, int] | None:
        size = self._image.size
        cols, rows = int(size.width), int(size.height)
        return None if cols < 4 or rows < 2 else (cols, rows)

    def _render_current(self) -> None:
        path = self._path
        if path is None:
            return
        box = self._box()
        if box is None:
            return
        self._token += 1
        self._warm = None
        self._queued = (path, self._is_video, box[0], box[1], self._token)
        self._pump()

    def _pump(self) -> None:
        if self._busy or not self.is_mounted:
            return
        job = self._queued if self._queued is not None else self._warm
        if job is None:
            return
        if job is self._queued:
            self._queued = None
        else:
            self._warm = None
        self._busy = True
        path, is_video, cols, rows, token = job
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            self._executor,
            render_media,
            path,
            cols,
            rows,
            self.colour,
            is_video,
            self.frame_fraction,
        )
        future.add_done_callback(lambda done: self._finished(done, path, token))

    def _finished(self, future, path: Path, token: int) -> None:
        self._busy = False
        if future.cancelled():
            return
        wanted = token == self._token and path == self._path
        error = future.exception()
        if error is None and wanted:
            result = future.result()
            self._publish(result.text, token, path, note=result.note)
        elif error is not None and wanted:
            self._publish(self._error_text(error), token, path)
        self._pump()

    def _error_text(self, error: BaseException) -> Text:
        if isinstance(error, PreviewError):
            return Text(str(error), style="bold red")
        if isinstance(error, OSError):
            return Text(f"cannot read file: {error}", style="red")
        return Text(f"preview failed: {error}", style="red")

    def _publish(self, text: Text, token: int, path: Path, note: str = "") -> None:
        if token != self._token or path != self._path:
            return
        if note != self._note:
            self._note = note
            lines = [*self._meta_value.split("\n")[:2], note]
            self._meta.update("\n".join(lines))
        self._image.update(text)
