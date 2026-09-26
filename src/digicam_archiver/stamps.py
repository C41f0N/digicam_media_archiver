"""Read capture times off the media files.

Photos carry ``DateTimeOriginal`` in EXIF; AVI files carry nothing, so file
mtime is used.  mtime is right for a card dump because the camera writes the
file at shoot time and the copy off the card does not rewrite it.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from PIL import Image

TAG_EXIF_IFD = 0x8769
TAG_DATETIME_ORIGINAL = 0x9003
TAG_DATETIME_DIGITIZED = 0x9004
TAG_DATETIME = 0x0132

_DATETIME_RE = re.compile(r"(\d{4})[:-](\d{2})[:-](\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?")


def parse_exif_datetime(raw: object) -> datetime | None:
    """Parse ``2026:08:23 14:02:11`` in any sane variation into a datetime."""
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("ascii", "ignore")
        except Exception:  # pragma: no cover - defensive
            return None
    if not isinstance(raw, str):
        return None
    match = _DATETIME_RE.search(raw)
    if match is None:
        return None
    year, month, day, hour, minute, second = match.groups()
    try:
        return datetime(
            int(year), int(month), int(day), int(hour), int(minute), int(second or 0)
        )
    except ValueError:
        return None


def exif_datetime(path: Path) -> datetime | None:
    """Best effort EXIF capture time, or None when absent/unreadable."""
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            candidates = [exif.get(TAG_DATETIME_ORIGINAL)]
            try:
                sub = exif.get_ifd(TAG_EXIF_IFD)
            except Exception:  # pragma: no cover - corrupt exif
                sub = {}
            candidates.append(sub.get(TAG_DATETIME_ORIGINAL))
            candidates.append(sub.get(TAG_DATETIME_DIGITIZED))
            candidates.append(exif.get(TAG_DATETIME))
            for candidate in candidates:
                stamp = parse_exif_datetime(candidate)
                if stamp is not None:
                    return stamp
    except Exception:
        return None
    return None


def mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime)


def capture_time(path: Path, kind: str) -> tuple[datetime, str]:
    """Return ``(timestamp, source)`` for one media file."""
    if kind == "photo":
        stamp = exif_datetime(path)
        if stamp is not None:
            return stamp, "exif"
    return mtime(path), "mtime"
