"""Where things land in the archive.

Layout::

    <archive>/26-08/26-08-23 Uni Friends Hangout/DSCF0001.JPG
    <archive>/26-08/26-08-23 Uni Friends Hangout/DSCF0002.mp4

The month folder and the date prefix both come from the capture time of the
event's first file, so an event is filed under when it started.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .fingerprint import ContentId, FileKey, key_of
from .model import MediaFile, format_date, format_month
from .naming import sanitize, unique_name
from .scan import PHOTO_SUFFIXES

#: The converted name a video gets in the archive.
VIDEO_SUFFIXES = frozenset({".mp4"})

#: Dot files in an event folder are the archiver's own, not media.
SKIP_PREFIX = "."

#: A half written copy, or a half finished encode.
PART_SUFFIX = ".part"

#: A date on the front of a name, as ``event_dir`` writes it.
LEADING_DATE = re.compile(r"^\d{2}-\d{2}-\d{2}(\s+|$)")


def month_dir(archive: Path, when: datetime) -> Path:
    return archive / format_month(when)


def event_dir(archive: Path, when: datetime, name: str) -> Path:
    """Where an event lives: ``<archive>/26-09/26-09-18 Home``.

    The date is written once.  A name that already starts with it, whether the
    user typed it or it was adopted from the folder the archive already has, is
    not doubled up, and a date belonging to another day is replaced rather than
    kept.
    """
    text = name.strip()
    if text.startswith(dated := f"{format_date(when)} "):
        text = text[len(dated) :].strip()
    else:
        # a date from another day is the event's date's job, not the name's
        text = LEADING_DATE.sub("", text, count=1).strip()
    return month_dir(archive, when) / f"{dated}{text or 'Event'}"


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
    dest_dir: Path,
    files: tuple[MediaFile, ...] | list[MediaFile],
    existing: ExistingFiles | None = None,
    keys: Mapping[Path, FileKey] | None = None,
) -> list[TransferItem]:
    """Resolve destination names for one event against what is already there.

    A file that is already in ``dest_dir`` with the same content keeps the name
    it has, so a rerun skips it instead of filing a second copy next to it.  A
    name held by *different* content is still avoided, because two card folders
    can carry the same filename and one of them has to become ``_1``.

    ``existing`` is what the folder holds, when the caller already knows it from
    reconciling against the archive.  ``keys`` are fingerprints the caller
    already has, so nothing is read twice.
    """
    items: list[TransferItem] = []
    folder = _Destination(dest_dir, files, existing, keys)
    for item in files:
        dest = folder.take(item) or _free_name(dest_dir, target_name(item), folder.used)
        folder.used.add(dest)
        items.append(TransferItem(source=item.path, dest=dest, kind=item.kind))
    return items


@dataclass(frozen=True, slots=True)
class ExistingFiles:
    """The media a destination folder already holds, and how to find it again.

    ``by_stat`` matches a photo by size and mtime, which is free and is what a
    plain copy carries over.  ``by_content`` matches by size and head hash for
    when the timestamps were lost on the way in.  ``by_stem`` finds videos by the
    stem of their converted name, because the archived MP4 cannot match the AVI
    and by design is a different file.  Every map holds a list, because a folder
    can hold the same photo twice and each copy may be used only once.
    """

    by_stat: Mapping[tuple[int, int], list[str]] = field(default_factory=dict)
    by_content: Mapping[ContentId, list[str]] = field(default_factory=dict)
    by_stem: Mapping[str, list[str]] = field(default_factory=dict)

    @classmethod
    def of(cls, folder: Path, sizes: Iterable[int] | None = None) -> ExistingFiles:
        """Read a folder from disk.

        ``sizes`` limits the fingerprinting to files that could match one of
        those sizes, so a folder of photos costs one short read per photo and
        nothing at all for the videos next to it.
        """
        by_stat: dict[tuple[int, int], list[str]] = {}
        by_content: dict[ContentId, list[str]] = {}
        by_stem: dict[str, list[str]] = {}
        wanted = None if sizes is None else set(sizes)
        for path in media_in(folder):
            suffix = path.suffix.lower()
            if suffix in VIDEO_SUFFIXES:
                by_stem.setdefault(path.stem, []).append(path.name)
                continue
            if suffix not in PHOTO_SUFFIXES:
                continue
            key = key_of(path)
            by_stat.setdefault(key.stat, []).append(path.name)
            if wanted is not None and key.size not in wanted:
                continue
            by_content.setdefault(key.content, []).append(path.name)
        return cls(by_stat=by_stat, by_content=by_content, by_stem=by_stem)

    def names(self) -> set[str]:
        """Every name this folder is holding, for reporting."""
        return {
            name
            for group in (self.by_stat, self.by_content, self.by_stem)
            for names in group.values()
            for name in names
        }


def media_in(folder: Path) -> list[Path]:
    """The media files in ``folder``, in a stable order, ignoring our own files."""
    if not folder.is_dir():
        return []
    return [
        path
        for path in sorted(folder.iterdir())
        if path.is_file()
        and not path.name.startswith(SKIP_PREFIX)
        and path.suffix.lower() != PART_SUFFIX
    ]


class _Destination:
    """The names a folder already uses, and the ones this run has taken.

    Each existing file is handed out once.  Two byte identical photos from two
    card folders are the same media by every measure available, so the first one
    reuses the archived file and the second has to be suffixed.
    """

    def __init__(
        self,
        folder: Path,
        files: Iterable[MediaFile],
        existing: ExistingFiles | None,
        keys: Mapping[Path, FileKey] | None,
    ) -> None:
        self.folder = folder
        self.used: set[Path] = set()
        if existing is None:
            existing = ExistingFiles.of(folder, {item.size for item in files})
        self._keys = dict(keys or {})
        self._photos = existing
        self._videos = existing.by_stem

    def take(self, item: MediaFile) -> Path | None:
        """The file in the folder that already holds ``item``, if there is one."""
        if item.is_video:
            return self._hand_out(self._videos.get(item.stem, ()))
        key = self._key_of(item)
        if key is None:
            return None
        # the cheap identity first, so an unchanged archive costs no read at all
        names = self._photos.by_stat.get(key.stat, ())
        return self._hand_out(names) or self._hand_out(
            self._photos.by_content.get(key.content, ())
        )

    def _key_of(self, item: MediaFile) -> FileKey | None:
        key = self._keys.get(item.path)
        if key is not None:
            return key
        if not item.path.exists():
            return None
        key = key_of(item.path)
        self._keys[item.path] = key
        return key

    def _hand_out(self, names: Iterable[str]) -> Path | None:
        for name in names:
            candidate = self.folder / name
            if candidate not in self.used:
                self.used.add(candidate)
                return candidate
        return None


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
