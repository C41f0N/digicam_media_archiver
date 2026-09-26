"""Where things land in the archive.

Layout::

    <archive>/26-08/26-08-23 Uni Friends Hangout/DSCF0001.JPG
    <archive>/26-08/26-08-23 Uni Friends Hangout/DSCF0002.mp4

The month folder and the date prefix both come from the capture time of the
event's first file, so an event is filed under when it started.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .model import MediaFile, format_date, format_month
from .naming import sanitize, unique_name


def month_dir(archive: Path, when: datetime) -> Path:
    return archive / format_month(when)


def event_dir(archive: Path, when: datetime, name: str) -> Path:
    return month_dir(archive, when) / f"{format_date(when)} {name}"


def existing_event_names(archive: Path, when: datetime) -> set[str]:
    folder = month_dir(archive, when)
    if not folder.is_dir():
        return set()
    return {child.name for child in folder.iterdir() if child.is_dir()}


def unique_event_dir(archive: Path, when: datetime, name: str) -> Path:
    """Event folder for ``name``, suffixed if that folder is already there."""
    base = sanitize(name, fallback="Event")
    taken = existing_event_names(archive, when)
    return month_dir(archive, when) / unique_name(f"{format_date(when)} {base}", taken)


def unique_file(dest_dir: Path, filename: str) -> Path:
    """``DSCF0001.JPG`` -> ``DSCF0001_1.JPG`` when that name is taken.

    Two card folders (``DCIM/103_FUJI`` and ``DCIM/104_FUJI``) can hold the same
    filename, and a re-run into an existing event folder can hit it again.
    """
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    for number in range(1, 10000):
        candidate = dest_dir / f"{stem}_{number}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"cannot find a free filename for {filename!r}")


@dataclass(frozen=True, slots=True)
class TransferItem:
    """One file to put in the archive."""

    source: Path
    dest: Path
    kind: str

    @property
    def is_video(self) -> bool:
        return self.kind == "video"


def target_name(item: MediaFile) -> str:
    """Videos become .mp4, photos keep their name untouched."""
    if item.is_video:
        return f"{item.stem}.mp4"
    return item.name


def build_items(
    dest_dir: Path, files: tuple[MediaFile, ...] | list[MediaFile]
) -> list[TransferItem]:
    """Resolve destination names for one event, avoiding collisions.

    Checks both the names this run is about to use and the ones already sitting
    in the folder, so a second run into the same event does not overwrite.
    """
    items: list[TransferItem] = []
    reserved: set[Path] = set()
    for item in files:
        dest = _free_name(dest_dir, target_name(item), reserved)
        reserved.add(dest)
        items.append(TransferItem(source=item.path, dest=dest, kind=item.kind))
    return items


def _free_name(dest_dir: Path, filename: str, reserved: set[Path]) -> Path:
    candidate = dest_dir / filename
    if not candidate.exists() and candidate not in reserved:
        return candidate
    stem, suffix = Path(filename).stem, Path(filename).suffix
    for number in range(1, 10000):
        candidate = dest_dir / f"{stem}_{number}{suffix}"
        if not candidate.exists() and candidate not in reserved:
            return candidate
    raise RuntimeError(f"cannot find a free filename for {filename!r}")
