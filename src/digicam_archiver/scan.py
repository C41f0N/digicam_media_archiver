"""Walk a digicam folder and collect the photos and videos on it."""

from __future__ import annotations

import logging
from pathlib import Path

from .model import MediaFile, MediaKind
from .stamps import capture_time

log = logging.getLogger(__name__)

PHOTO_SUFFIXES = frozenset({".jpg", ".jpeg"})
VIDEO_SUFFIXES = frozenset({".avi"})

#: Things that live next to the media on a Fujifilm card and are not media.
IGNORED_SUFFIXES = frozenset(
    {".thm", ".mp4", ".mov", ".mpeg", ".mpg", ".3gp", ".json", ".txt", ".ds_store"}
)


def media_root(source: Path) -> Path:
    """Digicams put media under ``DCIM``; fall back to the folder itself."""
    dcim = source / "DCIM"
    if dcim.is_dir():
        return dcim
    return source


def find_media(source: Path) -> list[tuple[Path, MediaKind]]:
    """All supported media below ``source``, sorted for a stable order."""
    root = media_root(source)
    found: list[tuple[Path, MediaKind]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in PHOTO_SUFFIXES:
            found.append((path, "photo"))
        elif suffix in VIDEO_SUFFIXES:
            found.append((path, "video"))
        elif suffix in IGNORED_SUFFIXES:
            log.debug("ignoring %s", path.name)
    found.sort(key=lambda pair: (str(pair[0]).lower(), pair[0].name))
    return found


def scan(source: Path) -> list[MediaFile]:
    """Collect every photo and video, sorted by capture time.

    Files without a readable timestamp fall back to mtime, so this never
    returns an unordered or partial list.
    """
    files: list[MediaFile] = []
    for path, kind in find_media(source):
        taken, source_name = capture_time(path, kind)
        files.append(
            MediaFile(
                path=path,
                kind=kind,
                taken=taken,
                size=path.stat().st_size,
                stamp_source=source_name,
            )
        )
    files.sort(key=lambda f: (f.taken, str(f.path).lower()))
    return files
