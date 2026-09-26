"""Data types shared across the archiver."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

MediaKind = Literal["photo", "video"]
StampSource = Literal["exif", "mtime"]


@dataclass(frozen=True, slots=True)
class MediaFile:
    """One photo or video on the digicam card."""

    path: Path
    kind: MediaKind
    taken: datetime
    size: int
    stamp_source: StampSource

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def stem(self) -> str:
        return self.path.stem

    @property
    def is_video(self) -> bool:
        return self.kind == "video"


@dataclass(frozen=True, slots=True)
class Event:
    """A run of files the user considers one outing.

    ``start`` is the index of the first file in the globally sorted file list.
    It is the stable identity of an event: manual splits, exclusions and names
    are all keyed by it, so they survive re-running the automatic partitioning
    with a different gap.
    """

    start: int
    files: tuple[MediaFile, ...]

    @property
    def end(self) -> int:
        """Index one past the last file of this event."""
        return self.start + len(self.files)

    @property
    def first(self) -> MediaFile:
        return self.files[0]

    @property
    def last(self) -> MediaFile:
        return self.files[-1]

    @property
    def taken_start(self) -> datetime:
        return self.files[0].taken

    @property
    def taken_end(self) -> datetime:
        return self.files[-1].taken

    @property
    def photos(self) -> tuple[MediaFile, ...]:
        return tuple(f for f in self.files if f.kind == "photo")

    @property
    def videos(self) -> tuple[MediaFile, ...]:
        return tuple(f for f in self.files if f.kind == "video")

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)


def human_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def format_time(when: datetime) -> str:
    return when.strftime("%y-%m-%d %H:%M:%S")


def format_date(when: datetime) -> str:
    return when.strftime("%y-%m-%d")


def format_month(when: datetime) -> str:
    return when.strftime("%y-%m")


def format_range(event: Event) -> str:
    """``26-08-23 14:02 -> 26-08-23 18:40``, date shown twice only when it changes."""
    start, end = event.taken_start, event.taken_end
    if start.date() == end.date():
        return f"{format_date(start)} {start:%H:%M} -> {end:%H:%M}"
    return f"{format_date(start)} {start:%H:%M} -> {format_date(end)} {end:%H:%M}"


def describe(event: Event) -> str:
    """One-line summary used in the UI and in ``--print-plan``."""
    counts = f"{len(event.files)} files"
    if event.videos:
        counts += f", {len(event.videos)} videos"
    return f"{format_range(event)}  {counts}, {human_size(event.size)}"
