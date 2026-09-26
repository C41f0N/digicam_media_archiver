"""Telling one media file from another, cheaply.

A rerun has to answer a question the file name cannot: is the photo sitting in
the archive the same one that is on the card right now, or is it a different
shot that happens to share a name?  Three cheap facts answer it.  The size is
free.  The mtime is nearly free, and ``copy_photo`` keeps the source's
timestamps, so a plain copy carries it over.  When those two disagree, the first
:data:`HEAD_BYTES` of each file decide, which costs one short read apiece and
settles it as well as a full hash for everything but a deliberate collision.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

#: How much of a file is read to fingerprint it.
HEAD_BYTES = 16 * 1024

#: The content of a file, independent of when it was written: size and head.
ContentId = tuple[int, str]

#: ``(path, size, mtime_ns)`` -> key, so a file is read once per run.
_CACHE: dict[tuple[str, int, int], FileKey] = {}


@dataclass(frozen=True, slots=True)
class FileKey:
    """Size, mtime and head hash of one file."""

    size: int
    mtime_ns: int
    head: str

    @property
    def content(self) -> ContentId:
        """What has to match for two files to be the same media."""
        return (self.size, self.head)

    @property
    def stat(self) -> tuple[int, int]:
        """Size and mtime, the identity a plain copy carries over."""
        return (self.size, self.mtime_ns)

    def same_media(self, other: FileKey) -> bool:
        """True when both keys describe the same media file.

        An mtime match is accepted on its own, because a copy that kept the
        source's timestamps is the source.  When the timestamps differ, which
        is what a backup restore or a FAT round trip looks like, the head hashes
        have to agree.
        """
        return self.size == other.size and (
            self.mtime_ns == other.mtime_ns or self.head == other.head
        )


def head_hash(path: Path) -> str:
    """sha256 of the first :data:`HEAD_BYTES` of ``path``."""
    with path.open("rb") as handle:
        return hashlib.sha256(handle.read(HEAD_BYTES)).hexdigest()


def key_of(path: Path) -> FileKey:
    """Fingerprint ``path``, reading it at most once per size and mtime."""
    info = path.stat()
    cache_key = (str(path), info.st_size, info.st_mtime_ns)
    key = _CACHE.get(cache_key)
    if key is None:
        key = FileKey(info.st_size, info.st_mtime_ns, head_hash(path))
        _CACHE[cache_key] = key
    return key


def same_media(left: Path, right: Path) -> bool:
    """True when two paths hold the same media file."""
    return key_of(left).same_media(key_of(right))


def clear_cache() -> None:
    """Forget every fingerprint read so far."""
    _CACHE.clear()
