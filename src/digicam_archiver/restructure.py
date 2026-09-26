"""Moving files that are already in the archive when the plan says otherwise.

Splitting or merging an event after the fact means files that are already
archived have to change folders.  Nothing here is decided at import time: the
plan is worked out first, shown to the user, and only then applied.

Two rules, both from the brief: the card is never touched, and no file is ever
deleted.  A file only ever leaves a folder because it is being moved into
another one.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .layout import event_dir
from .partition import Plan
from .reconcile import ArchiveIndex, Reconcile

#: where the changes of one run are written down
JOURNAL_DIR = ".digicam"


@dataclass(frozen=True)
class Move:
    """One archived file that has to change folder."""

    source: Path
    target: Path
    name: str

    def apply(self) -> None:
        """Move it.  ``replace`` is atomic inside one filesystem."""
        if self.source == self.target:
            return
        self.target.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.source.replace(self.target)
        except OSError:
            # a different filesystem: copy, then let go of the original
            shutil.copy2(self.source, self.target)
            self.source.unlink()


@dataclass(frozen=True)
class FolderPlan:
    """What one event's folder should end up as."""

    start: int
    label: str
    source: Path | None  # the folder as it is now, None when it has to be made
    target: Path  # where this event's files go
    moves: tuple[Move, ...]
    final: Path  # where the folder itself ends up

    @property
    def renamed(self) -> bool:
        return self.final != self.target


@dataclass(frozen=True)
class Restructure:
    """The whole change set.  None of it has happened yet."""

    folders: tuple[FolderPlan, ...] = ()
    #: folders the moves leave behind
    vacant: tuple[Path, ...] = ()
    conflicts: tuple[str, ...] = ()
    #: folders holding files the card does not account for
    strangers: tuple[Path, ...] = ()
    #: folders with unknown files that would change anyway
    blocked: tuple[Path, ...] = ()

    @property
    def changes(self) -> tuple[FolderPlan, ...]:
        return tuple(folder for folder in self.folders if folder.moves or folder.renamed)

    @property
    def is_empty(self) -> bool:
        return not self.changes and not self.conflicts

    @property
    def move_count(self) -> int:
        return sum(len(folder.moves) for folder in self.folders)


def plan_restructure(
    plan: Plan,
    index: ArchiveIndex,
    answer: Reconcile,
    archive: Path,
) -> Restructure:
    """Work out the moves for the plan as it stands.

    Pure: it reads the index and the answer, and returns a description of what
    would change.  Nothing is written until :func:`apply` is called.
    """
    folders: list[FolderPlan] = []
    conflicts: list[str] = []
    emptied: set[Path] = set()

    # First every event that gets a folder, so the moves can see all of them: a
    # folder one event keeps must not be treated as another event's target.
    targets: dict[int, Path] = {}
    finals: dict[int, Path] = {}
    wanted_folders: set[Path] = set()
    for event in plan.included():
        archived = answer.of(event)
        home = archived.home
        name = plan.name_of(event)
        if home is not None:
            target = home
        elif name:
            target = event_dir(archive, event.taken_start, name)
        else:
            # no name and no folder: leave the archive exactly as it is
            continue
        targets[event.start] = target
        final = target
        if name and target.exists():
            wanted_name = event_dir(archive, event.taken_start, name).name
            if wanted_name != target.name:
                final = target.with_name(wanted_name)
                if final.exists():
                    conflicts.append(f"{final} is in the way, {target.name} keeps its name")
                    final = target
        finals[event.start] = final
        wanted_folders.add(final)

    for event in plan.included():
        target = targets.get(event.start)
        if target is None:
            continue
        archived = answer.of(event)
        name = plan.name_of(event)

        moves: list[Move] = []
        seen: set[str] = set()
        for _, claim in sorted(archived.present.items(), key=lambda pair: pair[1].name):
            if claim.name in seen:
                continue  # the same name twice, only the first can be moved
            seen.add(claim.name)
            if claim.folder == target:
                continue
            if (target / claim.name).exists():
                conflicts.append(
                    f"{target / claim.name} is in the way, leaving {claim.name} in "
                    f"{claim.folder.name}"
                )
                continue
            moves.append(Move(source=claim.path, target=target / claim.name, name=claim.name))
            emptied.add(claim.folder)

        folders.append(
            FolderPlan(
                start=event.start,
                label=name or target.name,
                source=target if target.exists() else None,
                target=target,
                moves=tuple(moves),
                final=finals[event.start],
            )
        )

    vacant = tuple(sorted(path for path in emptied if path not in wanted_folders))
    return Restructure(
        folders=tuple(folders),
        vacant=vacant,
        conflicts=tuple(conflicts),
        strangers=answer.strangers,
        blocked=tuple(path for path in answer.strangers if path in emptied),
    )


def apply(restructure: Restructure, archive: Path, log: Callable[[str], None]) -> int:
    """Do the moves.  Returns how many files changed folder."""
    done = 0
    for folder in restructure.changes:
        for move in folder.moves:
            if move.source.exists():
                move.apply()
                log(f"moved {move.name} into {folder.target.name}")
                done += 1
    for folder in restructure.changes:
        if folder.renamed and folder.source is not None and folder.source.exists():
            folder.source.replace(folder.final)
            log(f"renamed {folder.source.name} to {folder.final.name}")
    for path in restructure.vacant:
        try:
            path.rmdir()
        except OSError:
            continue  # something is still in there, so it stays
        log(f"removed the empty folder {path.name}")
    journal(restructure, archive, log)
    return done


def journal(restructure: Restructure, archive: Path, log: Callable[[str], None]) -> Path | None:
    """Write down what moved, in case it has to be undone by hand."""
    if restructure.is_empty:
        return None
    try:
        folder = archive / JOURNAL_DIR
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = folder / f"changes-{stamp}.json"
        path.write_text(
            json.dumps(
                {
                    "when": stamp,
                    "moved": [
                        {"from": str(move.source), "to": str(move.target), "name": move.name}
                        for folder in restructure.folders
                        for move in folder.moves
                    ],
                    "renamed": [
                        {"from": str(folder.source), "to": str(folder.final)}
                        for folder in restructure.folders
                        if folder.renamed and folder.source is not None
                    ],
                    "conflicts": list(restructure.conflicts),
                },
                indent=2,
            )
        )
    except OSError as exc:
        log(f"could not write the change log: {exc}")
        return None
    log(f"wrote down the changes in {path.name}")
    return path


def describe(restructure: Restructure) -> str:
    """The preview text: what would change, in plain words."""
    if restructure.is_empty:
        return "the archive stays where it is"
    lines: list[str] = []
    for folder in restructure.changes:
        for move in folder.moves:
            lines.append(f"move {move.name}: {move.source.parent.name} -> {folder.target.name}")
        if folder.renamed and folder.source is not None:
            lines.append(f"rename {folder.source.name} -> {folder.final.name}")
    for folder in restructure.vacant:
        lines.append(f"remove {folder.name} if it ends up empty")
    for text in restructure.conflicts:
        lines.append(f"cannot: {text}")
    for path in restructure.blocked:
        lines.append(f"hold on: {path.name} has files the card does not account for")
    return "\n".join(lines)


def asks_for_a_decision(restructure: Restructure) -> bool:
    """True when a folder with unknown files would change, so ask first."""
    return bool(restructure.blocked or restructure.conflicts)
