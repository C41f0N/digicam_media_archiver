"""Split the sorted file list into events.

Automatic rule: a silence longer than ``gap`` starts a new event.  The user can
add splits the gap missed and remove splits the gap invented, and both survive
a change of gap, because manual decisions are stored per file index instead of
being baked into the resulting events.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from .model import Event, MediaFile, describe

DEFAULT_GAP = timedelta(hours=4)


def build_events(
    files: list[MediaFile],
    gap: timedelta,
    forced_starts: frozenset[int] | set[int] = frozenset(),
    suppressed_splits: frozenset[int] | set[int] = frozenset(),
) -> list[Event]:
    """Group ``files`` (already sorted by capture time) into events.

    A new event starts at index ``i`` when ``i`` is a forced start, or when the
    silence before it exceeds ``gap`` and the split was not suppressed.
    """
    events: list[Event] = []
    start = 0
    for index in range(1, len(files)):
        gap_exceeded = files[index].taken - files[index - 1].taken > gap
        should_split = index in forced_starts or (
            gap_exceeded and index not in suppressed_splits
        )
        if should_split:
            events.append(Event(start=start, files=tuple(files[start:index])))
            start = index
    if files:
        events.append(Event(start=start, files=tuple(files[start:])))
    return events


@dataclass
class Plan:
    """Everything the user is editing: the files, the gap, and the manual calls.

    Manual splits, exclusions and names are keyed by the index of the first file
    of an event, so they stay attached to the right files when the gap changes.
    """

    source: Path
    files: list[MediaFile]
    gap: timedelta = DEFAULT_GAP
    forced_starts: set[int] = field(default_factory=set)
    suppressed_splits: set[int] = field(default_factory=set)
    excluded_starts: set[int] = field(default_factory=set)
    names: dict[int, str] = field(default_factory=dict)
    _cache: tuple | None = field(default=None, repr=False, compare=False)

    def events(self) -> list[Event]:
        key = (
            len(self.files),
            self.gap,
            frozenset(self.forced_starts),
            frozenset(self.suppressed_splits),
        )
        if self._cache is not None and self._cache[0] == key:
            return self._cache[1]
        events = build_events(self.files, self.gap, self.forced_starts, self.suppressed_splits)
        self._cache = (key, events)
        return events

    def included(self) -> list[Event]:
        return [event for event in self.events() if event.start not in self.excluded_starts]

    def is_excluded(self, event: Event) -> bool:
        return event.start in self.excluded_starts

    def name_of(self, event: Event) -> str | None:
        return self.names.get(event.start)

    def set_gap(self, gap: timedelta) -> None:
        self.gap = max(timedelta(minutes=5), gap)

    def split_before(self, index: int) -> bool:
        """Force a new event to start at ``index``. False if impossible."""
        if index <= 0 or index >= len(self.files):
            return False
        self.forced_starts.add(index)
        self.suppressed_splits.discard(index)
        self._cache = None
        return True

    def merge_next(self, event: Event) -> bool:
        """Glue the event starting at ``event.start`` to the one after it."""
        events = self.events()
        position = next((i for i, e in enumerate(events) if e.start == event.start), -1)
        if position < 0 or position + 1 >= len(events):
            return False
        nxt = events[position + 1]
        self.suppressed_splits.add(nxt.start)
        self.forced_starts.discard(nxt.start)
        self.excluded_starts.discard(nxt.start)
        self.names.pop(nxt.start, None)
        self._cache = None
        return True

    def toggle_excluded(self, event: Event) -> bool:
        """Return the new excluded state."""
        if event.start in self.excluded_starts:
            self.excluded_starts.discard(event.start)
            excluded = False
        else:
            self.excluded_starts.add(event.start)
            excluded = True
        self._cache = None
        return excluded

    def set_name(self, event: Event, name: str) -> None:
        self.names[event.start] = name

    def index_of_file(self, path: Path) -> int:
        target = str(path)
        for index, item in enumerate(self.files):
            if str(item.path) == target:
                return index
        raise KeyError(path)

    def lines(self) -> list[str]:
        """The plan as plain text, for ``--print-plan`` and dry runs."""
        out: list[str] = []
        for number, event in enumerate(self.events(), start=1):
            flags = []
            if self.is_excluded(event):
                flags.append("excluded")
            name = self.name_of(event)
            if name:
                flags.append(name)
            suffix = f"  [{', '.join(flags)}]" if flags else ""
            out.append(f"{number:>3}. {describe(event)}{suffix}")
            for item in event.files:
                stamp = "exif" if item.stamp_source == "exif" else "mtime"
                out.append(f"     {item.taken:%y-%m-%d %H:%M:%S} {stamp} {item.path.name}")
        return out
