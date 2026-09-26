"""Copy photos, convert videos, report progress.

One :class:`Transfer` run walks a list of :class:`TransferJob` (one per event)
and, for each file, either copies it byte for byte or re-encodes an AVI to MP4
with HandBrake.  Destinations that already look complete are skipped, so an
interrupted run can be resumed by starting it again.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .handbrake import DEFAULT_PRESET, EncodeProgress, encode
from .layout import TransferItem

log = logging.getLogger(__name__)

CHUNK = 1024 * 1024
#: Copy progress updates are throttled to one per this many seconds.
REPORT_INTERVAL = 0.1


class TransferCancelled(RuntimeError):
    """The user asked to stop; whatever is half written is cleaned up."""


class Reporter(Protocol):
    """What the UI needs to hear about."""

    def run_start(self, jobs: list[TransferJob]) -> None: ...
    def event_start(self, index: int, total: int, dest_dir: Path) -> None: ...
    def item_start(self, item: TransferItem, index: int, total: int) -> None: ...
    def item_progress(
        self,
        item: TransferItem,
        done_units: float,
        total_units: float,
        progress: EncodeProgress | None,
    ) -> None: ...
    def item_done(self, item: TransferItem, skipped: bool) -> None: ...
    def item_failed(self, item: TransferItem, error: BaseException) -> None: ...
    def log(self, message: str) -> None: ...
    def run_done(self, result: TransferResult) -> None: ...


class NullReporter:
    """Reporter for tests and for the non-interactive paths."""

    def run_start(self, jobs: list[TransferJob]) -> None:
        return None

    def event_start(self, index: int, total: int, dest_dir: Path) -> None:
        return None

    def item_start(self, item: TransferItem, index: int, total: int) -> None:
        return None

    def item_progress(
        self,
        item: TransferItem,
        done_units: float,
        total_units: float,
        progress: EncodeProgress | None,
    ) -> None:
        return None

    def item_done(self, item: TransferItem, skipped: bool) -> None:
        return None

    def item_failed(self, item: TransferItem, error: BaseException) -> None:
        return None

    def log(self, message: str) -> None:
        return None

    def run_done(self, result: TransferResult) -> None:
        return None


@dataclass(frozen=True, slots=True)
class TransferJob:
    """One event's worth of work."""

    label: str
    dest_dir: Path
    items: tuple[TransferItem, ...]

    @property
    def videos(self) -> tuple[TransferItem, ...]:
        return tuple(item for item in self.items if item.is_video)

    @property
    def bytes_expected(self) -> int:
        return sum(item.source.stat().st_size for item in self.items if item.source.exists())


@dataclass
class TransferResult:
    copied: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed == 0 and not self.cancelled


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_photo(
    source: Path,
    dest: Path,
    on_bytes=None,
    cancel: asyncio.Event | None = None,
) -> None:
    """Copy a photo verbatim, then check that it arrived intact.

    Writes to a ``.part`` file first so an interrupted copy never leaves a
    truncated photo in the archive.
    """
    total = source.stat().st_size
    done = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    try:
        with source.open("rb") as reader, partial.open("wb") as writer:
            while True:
                if cancel is not None and cancel.is_set():
                    raise TransferCancelled(f"cancelled while copying {source.name}")
                chunk = reader.read(CHUNK)
                if not chunk:
                    break
                writer.write(chunk)
                done += len(chunk)
                if on_bytes is not None:
                    on_bytes(done, total)
            writer.flush()
            os.fsync(writer.fileno())
        if done != total:
            raise OSError(f"short copy for {source.name}")
        partial.replace(dest)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    with contextlib.suppress(OSError):
        shutil.copystat(source, dest)
    if dest.stat().st_size != total:  # pragma: no cover - paranoia
        dest.unlink(missing_ok=True)
        raise OSError(f"short copy for {source.name}")


def already_done(item: TransferItem, checksum: bool = False) -> bool:
    """True when the destination is there and looks complete.

    A photo must match the source size.  A converted video cannot: the MP4 is a
    different size from the AVI by design, so existing and non-empty is the best
    answer available.
    """
    if not item.dest.exists() or item.dest.stat().st_size == 0:
        return False
    if item.is_video:
        return True
    if item.source.exists() and item.dest.stat().st_size != item.source.stat().st_size:
        return False
    if checksum and item.source.exists():
        return sha256_of(item.source) == sha256_of(item.dest)
    return True


class Transfer:
    """Runs the jobs in order on the event loop."""

    def __init__(
        self,
        jobs: list[TransferJob],
        reporter: Reporter | None = None,
        preset: str = DEFAULT_PRESET,
        faststart: bool = False,
        overwrite: bool = False,
        checksum: bool = False,
        handbrake: str | None = None,
        cancel: asyncio.Event | None = None,
    ) -> None:
        self.jobs = jobs
        self.reporter = reporter or NullReporter()
        self.preset = preset
        self.faststart = faststart
        self.overwrite = overwrite
        self.checksum = checksum
        self.handbrake = handbrake
        self.cancel = cancel or asyncio.Event()
        self.videos_total = sum(len(job.videos) for job in self.jobs)
        self.videos_left = self.videos_total

    def all_items(self) -> list[TransferItem]:
        return [item for job in self.jobs for item in job.items]

    async def run(self) -> TransferResult:
        result = TransferResult()
        total = len(self.all_items())
        self.reporter.run_start(self.jobs)
        position = 0
        for number, job in enumerate(self.jobs, start=1):
            if self.cancel.is_set():
                result.cancelled = True
                break
            job.dest_dir.mkdir(parents=True, exist_ok=True)
            self.reporter.event_start(number, len(self.jobs), job.dest_dir)
            for item in job.items:
                position += 1
                if self.cancel.is_set():
                    result.cancelled = True
                    break
                await self._run_item(item, position, total, result)
            if result.cancelled:
                break
        self.reporter.run_done(result)
        return result

    async def _run_item(
        self, item: TransferItem, position: int, total: int, result: TransferResult
    ) -> None:
        self.reporter.item_start(item, position, total)
        try:
            if not self.overwrite and already_done(item, self.checksum):
                result.skipped += 1
                self.reporter.item_progress(item, 1.0, 1.0, None)
                self.reporter.item_done(item, skipped=True)
                self._video_finished(item)
                return

            if item.is_video:
                await self._convert(item)
            else:
                await self._copy(item)
            result.copied += 1
            self.reporter.item_done(item, skipped=False)
            self._video_finished(item)
        except (TransferCancelled, asyncio.CancelledError):
            result.cancelled = True
            self.reporter.log(f"stopped at {item.source.name}")
        except Exception as exc:
            result.failed += 1
            message = f"{item.source.name}: {exc}"
            result.errors.append(message)
            log.warning("failed on %s: %s", item.source, exc)
            self.reporter.item_failed(item, exc)
            self.reporter.log(message)
            self._video_finished(item)

    def _video_finished(self, item: TransferItem) -> None:
        if item.is_video and self.videos_left > 0:
            self.videos_left -= 1

    async def _copy(self, item: TransferItem) -> None:
        loop = asyncio.get_running_loop()
        last = 0.0

        def on_bytes(done: int, total_bytes: int) -> None:
            nonlocal last
            now = loop.time()
            if done != total_bytes and now - last < REPORT_INTERVAL:
                return
            last = now
            self.reporter.item_progress(item, float(done), float(total_bytes), None)

        await asyncio.to_thread(copy_photo, item.source, item.dest, on_bytes, self.cancel)
        if self.checksum and sha256_of(item.source) != sha256_of(item.dest):
            item.dest.unlink(missing_ok=True)
            raise OSError(f"checksum mismatch for {item.source.name}")
        self.reporter.item_progress(item, 1.0, 1.0, None)

    async def _convert(self, item: TransferItem) -> None:
        def on_progress(progress: EncodeProgress) -> None:
            self.reporter.item_progress(item, progress.fraction_for_task or 0.0, 1.0, progress)

        await encode(
            item.source,
            item.dest,
            preset=self.preset,
            faststart=self.faststart,
            binary=self.handbrake,
            on_progress=on_progress,
            cancel=self.cancel,
        )
        self.reporter.item_progress(item, 1.0, 1.0, None)
