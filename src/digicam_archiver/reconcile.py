"""Working out which of these events the archive already has.

A rerun puts the same card in front of the archiver with an archive that already
holds most of it.  The names live only in folder names, so the archiver has to
recognise a folder by what is inside it, adopt the name it finds there, and say
which files are still missing.  This module keeps the two halves apart on
purpose: :func:`build_index` is the only part that touches the disk, and
:func:`reconcile` is a pure function of a plan and that index.  The index is read
once per run, in the background, while the partition is being edited, and the
answer is recomputed from scratch on every split, merge and gap change, which is
cheap enough to redraw the list with.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .fingerprint import key_of
from .layout import ExistingFiles, media_in
from .model import Event, MediaFile
from .partition import Plan

#: ``26-08-23 Uni Friends Hangout`` -> ``("26-08-23", "Uni Friends Hangout")``.
FOLDER_NAME = re.compile(r"^(\d{2}-\d{2}-\d{2}) +(.*)$")

#: A month folder, ``26-08``.  Anything else in the archive is left alone.
MONTH_FOLDER = re.compile(r"^\d{2}-\d{2}$")


class MatchKind(StrEnum):
    """How an event of the plan relates to the archive."""

    #: Nothing in the archive looks like this event.
    NEW = "new"
    #: One folder holds every file of this event.
    MATCHED = "matched"
    #: One folder holds most of it and some files are not there yet.
    PARTIAL = "partial"
    #: One folder holds files of several events, because the partition was split.
    SPLIT = "split"
    #: Several folders hold files of this one event, because they were merged.
    MERGE = "merge"
    #: More than one folder fits, so the user has to say which one.
    AMBIGUOUS = "ambiguous"


@dataclass(frozen=True, slots=True)
class EventFolder:
    """One event folder in the archive, as it is now."""

    path: Path
    name: str
    date: str
    month: str
    files: ExistingFiles

    @property
    def media(self) -> dict[str, Path]:
        """The media files in here, by name."""
        return {path.name: path for path in media_in(self.path)}

    def holds(self, item: MediaFile) -> bool:
        """True when this folder already has a file that is ``item``."""
        if item.is_video:
            return item.stem in self.files.by_stem
        if not item.path.exists():
            return False
        return key_of(item.path).content in self.files.by_content


@dataclass(frozen=True, slots=True)
class ArchiveIndex:
    """Every event folder in the archive, indexed by what it holds.

    Only sizes that are on the card are fingerprinted, which is what makes this
    affordable: sixteen kilobytes per archived photo is nothing next to reading
    them all, and a photo of a different size could never have come from this
    card anyway.
    """

    folders: tuple[EventFolder, ...] = ()

    def by_path(self, path: Path) -> EventFolder | None:
        return next((folder for folder in self.folders if folder.path == path), None)

    def of_date(self, date: str) -> tuple[EventFolder, ...]:
        return tuple(folder for folder in self.folders if folder.date == date)


def parse_folder_name(folder: Path) -> tuple[str, str]:
    """``(date, name)`` for ``26-08-23 Uni Friends Hangout``."""
    found = FOLDER_NAME.match(folder.name)
    if found is None:
        return "", folder.name
    return found.group(1), found.group(2)


def build_index(
    archive: Path,
    plan: Plan,
    on_progress: Callable[[int, int], None] | None = None,
) -> ArchiveIndex:
    """Read the archive once. The only slow part of recognising a rerun."""
    sizes = {item.size for item in plan.files if item.kind == "photo"}
    months = _month_dirs(archive)
    total = sum(1 for month in months for _ in _event_dirs(month))
    folders: list[EventFolder] = []
    seen = 0
    for month in months:
        for folder in _event_dirs(month):
            seen += 1
            files = ExistingFiles.of(folder, sizes)
            if not files.names():
                # an empty folder is a leftover, not an event
                continue
            date, name = parse_folder_name(folder)
            folders.append(
                EventFolder(path=folder, name=name, date=date, month=month.name, files=files)
            )
            if on_progress is not None:
                on_progress(seen, total)
    return ArchiveIndex(tuple(folders))


def _month_dirs(archive: Path) -> list[Path]:
    if not archive.is_dir():
        return []
    return [
        path
        for path in sorted(archive.iterdir())
        if path.is_dir() and MONTH_FOLDER.match(path.name)
    ]


def _event_dirs(month: Path) -> list[Path]:
    return [
        path
        for path in sorted(month.iterdir())
        if path.is_dir() and not path.name.startswith(".")
    ]


# ---------------------------------------------------------------- the answer


@dataclass(frozen=True, slots=True)
class ArchiveClaim:
    """Where one card file already sits in the archive."""

    folder: Path
    name: str

    @property
    def path(self) -> Path:
        return self.folder / self.name


@dataclass(frozen=True, slots=True)
class Archived:
    """What the archive already holds for one event of the plan."""

    start: int
    kind: MatchKind
    #: The folder this event lives in.  ``None`` when it has none of its own,
    #: which is what half of a split event looks like from here.
    home: Path | None
    #: The name found in that folder.  Empty for a new event.
    name: str
    #: Card file -> where it already is in the archive.  Nothing to copy.
    present: dict[Path, ArchiveClaim] = field(default_factory=dict)
    #: The files of this event that are not in the archive yet.
    missing: tuple[MediaFile, ...] = ()
    #: Files in ``home`` that no event of the plan explains.  A person put those
    #: there, so the run stops to ask before anything moves.
    strangers: tuple[str, ...] = ()
    #: Other folders whose files this event has to collect, for a merge.
    donors: tuple[Path, ...] = ()
    #: Events that share ``home`` with this one, for a split.
    sharers: tuple[int, ...] = ()

    @property
    def is_archived(self) -> bool:
        """True when the archive has something for this event already."""
        return self.home is not None or bool(self.present)

    @property
    def complete(self) -> bool:
        return not self.missing

    def needs_a_name(self) -> bool:
        """True when the event's files are archived but it has no folder of its own.

        That is the split: half the event is in the archive under one name, and
        this half needs a name before the folder can be divided.
        """
        return self.home is None and bool(self.present)

    def wants_a_merge(self) -> bool:
        return self.kind is MatchKind.MERGE or self.kind is MatchKind.AMBIGUOUS


@dataclass(frozen=True, slots=True)
class Reconcile:
    """The answer for every event of a plan."""

    events: dict[int, Archived] = field(default_factory=dict)
    #: Folders holding files of more than one event, so they may be divided.
    contested: frozenset[Path] = frozenset()
    #: Folders holding files no event of the plan explains.
    strangers: frozenset[Path] = frozenset()
    #: Those folders, with the names in them that nobody accounts for.
    stranger_names: dict[Path, tuple[str, ...]] = field(default_factory=dict)

    def strangers_by_folder(self) -> dict[Path, tuple[str, ...]]:
        """Folder to the names in it that the card does not account for."""
        if self.stranger_names:
            return dict(self.stranger_names)
        found: dict[Path, tuple[str, ...]] = {}
        for item in self.events.values():
            if item.home is not None and item.strangers:
                found[item.home] = item.strangers
        return found

    def of(self, event: Event) -> Archived:
        """The answer for one event. A skipped event counts as new.

        Excluded events are left out of the answer on purpose, so they claim
        nothing, but the list still has to label them.
        """
        return self.events.get(event.start, Archived(event.start, MatchKind.NEW, None, ""))

    @property
    def archived(self) -> tuple[Archived, ...]:
        return tuple(item for item in self.events.values() if item.is_archived)

    @property
    def new(self) -> tuple[Archived, ...]:
        return tuple(
            item
            for item in self.events.values()
            if item.kind is MatchKind.NEW and not item.present
        )

    def lines(self) -> list[str]:
        """One line per event, for ``--print-plan``."""
        out = []
        for start, item in sorted(self.events.items()):
            out.append(
                f"{start + 1:>4}. {item.kind.value:<9} {item.name or '-'}  {_detail(item)}"
            )
        return out


def _detail(item: Archived) -> str:
    if item.kind is MatchKind.NEW:
        return "not in the archive"
    if item.home is None:
        return f"{len(item.present)} file(s) in a folder of its own"
    bits = [item.home.name]
    if item.missing:
        bits.append(f"{len(item.missing)} to copy")
    if item.donors:
        bits.append(f"merge {len(item.donors)} folder(s)")
    if item.sharers:
        bits.append(f"split with {len(item.sharers)} other event(s)")
    if item.strangers:
        bits.append(f"{len(item.strangers)} file(s) not on the card")
    return ", ".join(bits)


@dataclass(frozen=True, slots=True)
class _Claim:
    """What one folder holds of one event."""

    start: int
    files: tuple[MediaFile, ...]
    total: int

    @property
    def coverage(self) -> float:
        """How much of the event this folder explains, from 0 to 1."""
        return len(self.files) / self.total if self.total else 0.0


def reconcile(plan: Plan, index: ArchiveIndex) -> Reconcile:
    """Match every event of ``plan`` against the folders of ``index``.

    One pass asks each folder which events have files in it, which gives the
    whole shape of the archive at once: a folder only one event claims belongs to
    that event, a folder several events claim has to be divided between them, and
    an event with folders of its own that several events claim has been merged.
    The fourth shape, more than one folder fitting an event equally well, is
    reported as ambiguous rather than guessed at.
    """
    events = {event.start: event for event in plan.included()}
    totals = {start: len(event.files) for start, event in events.items()}

    claims: dict[Path, list[_Claim]] = {}
    for folder in index.folders:
        held = [
            _Claim(
                start=start,
                files=tuple(item for item in event.files if folder.holds(item)),
                total=totals[start],
            )
            for start, event in sorted(events.items())
        ]
        claims[folder.path] = [claim for claim in held if claim.files]

    keeper: dict[Path, int] = {}
    for path, held in claims.items():
        if held:
            keeper[path] = max(held, key=_rank).start

    owned: dict[int, list[Path]] = {}
    for path, start in keeper.items():
        owned.setdefault(start, []).append(path)

    # An event that owns more than one folder was merged.  The folder that
    # explains the most of it stays, and if they tie, the one filed under the
    # event's own date does, and after that the earliest name.
    homes: dict[int, Path] = {}
    donors: dict[int, list[Path]] = {}
    for start, paths in owned.items():
        date = events[start].taken_start.strftime("%y-%m-%d")
        best = min(
            paths,
            key=lambda path: (
                -_folder_rank(path, claims, start),
                not path.name.startswith(date),
                path.name,
            ),
        )
        homes[start] = best
        if len(paths) > 1:
            donors[start] = sorted(path for path in paths if path != best)

    present, strangers = _hand_out(index, claims, keeper)

    results: dict[int, Archived] = {}
    for start, event in sorted(events.items()):
        home = homes.get(start)
        folder = index.by_path(home) if home is not None else None
        sharers = _sharers(home, claims, start)
        given = present.get(start, {})
        absent = tuple(item for item in event.files if item.path not in given)
        kind = _kind_of(
            home, given, tuple(donors.get(start, ())), sharers, absent, strangers.get(home, ())
        )
        results[start] = Archived(
            start=start,
            kind=kind,
            home=home,
            name=folder.name if folder is not None else "",
            present=given,
            missing=absent,
            donors=tuple(donors.get(start, ())),
            sharers=sharers,
            strangers=strangers.get(home, ()) if folder is not None else (),
        )

    return Reconcile(
        events=results,
        contested=frozenset(path for path, held in claims.items() if len(held) > 1),
        strangers=frozenset(path for path, names in strangers.items() if names),
        stranger_names=dict(strangers),
    )


def _sharers(
    home: Path | None, claims: dict[Path, list[_Claim]], start: int
) -> tuple[int, ...]:
    """The events that are in the same folder as this one and are not it."""
    if home is None:
        return ()
    return tuple(sorted(claim.start for claim in claims.get(home, ()) if claim.start != start))


def _rank(claim: _Claim) -> tuple[float, int]:
    """Order the events a shared folder could belong to. Most covered wins."""
    return (claim.coverage, -claim.start)


def _folder_rank(path: Path, claims: dict[Path, list[_Claim]], start: int) -> float:
    claim = next((one for one in claims[path] if one.start == start), None)
    return claim.coverage if claim is not None else 0.0


def _hand_out(
    index: ArchiveIndex,
    claims: dict[Path, list[_Claim]],
    keeper: dict[Path, int],
) -> tuple[dict[int, dict[Path, ArchiveClaim]], dict[Path, tuple[str, ...]]]:
    """Give every archived file to one event, and list what is left over.

    The event that keeps a folder is served first, because it is the one that
    stays put; the events being split away are served afterwards and take only
    what is left.  Every name goes to one event, so a folder holding the same
    photo twice still hands out both copies.
    """
    present: dict[int, dict[Path, ArchiveClaim]] = {}
    strangers: dict[Path, tuple[str, ...]] = {}
    for path, held in claims.items():
        if not held:
            continue
        folder = index.by_path(path)
        assert folder is not None
        order = sorted(held, key=lambda claim: (claim.start != keeper[path], *_rank(claim)))
        taken: set[str] = set()
        for claim in order:
            mine = present.setdefault(claim.start, {})
            for item in claim.files:
                name = _name_in(folder, item, taken)
                if name is None:
                    continue
                taken.add(name)
                mine[item.path] = ArchiveClaim(folder=path, name=name)
        left = tuple(name for name in sorted(folder.files.names()) if name not in taken)
        if left:
            strangers[path] = left
    return present, strangers


def _name_in(folder: EventFolder, item: MediaFile, taken: set[str]) -> str | None:
    """The name of the file in ``folder`` that is ``item``, if it is still free."""
    if item.is_video:
        names: tuple[str, ...] = tuple(folder.files.by_stem.get(item.stem, ()))
    elif item.path.exists():
        key = key_of(item.path)
        names = tuple(folder.files.by_content.get(key.content, ()))
    else:
        names = ()
    for name in names:
        if name not in taken:
            return name
    return None


def _kind_of(
    home: Path | None,
    present: dict[Path, str],
    donors: tuple[Path, ...],
    sharers: tuple[int, ...],
    missing: tuple[MediaFile, ...],
    strangers: tuple[str, ...],
) -> MatchKind:
    if home is None:
        return MatchKind.SPLIT if present else MatchKind.NEW
    if strangers and (donors or sharers):
        # this folder is about to be divided or joined up, and it holds files
        # nobody can account for, so the run has to stop and ask
        return MatchKind.AMBIGUOUS
    if donors:
        return MatchKind.MERGE
    if sharers:
        return MatchKind.SPLIT
    return MatchKind.PARTIAL if missing else MatchKind.MATCHED
