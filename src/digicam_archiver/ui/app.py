"""The Textual application: three screens, one plan."""

from __future__ import annotations

import asyncio
from argparse import Namespace
from collections.abc import Callable
from pathlib import Path

from textual.app import App

from ..model import Event
from ..partition import Plan
from ..reconcile import ArchiveIndex, Reconcile, build_index, reconcile
from ..restructure import Restructure, plan_restructure
from .dialogs import ArchiveDir
from .partition_screen import PartitionScreen

DEFAULT_PRESET = "Fast 720p30"


class ArchiverApp(App):
    """Runs the partition, naming and transfer screens against one plan."""

    TITLE = "digicam media archiver"
    SUB_TITLE = "partition, name, copy"

    CSS = """
    #body {
        height: 1fr;
    }
    """

    def __init__(
        self,
        plan: Plan,
        archive: Path | None = None,
        opts: Namespace | None = None,
        preview_colour: bool = True,
        dry_run: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.plan = plan
        self.archive = archive
        self.preview_colour = preview_colour
        self.dry_run = dry_run
        self.opts = opts or Namespace(
            preset=DEFAULT_PRESET,
            faststart=False,
            overwrite=False,
            checksum=False,
            handbrake=None,
            no_adopt=False,
        )
        #: What the archive holds, read once because it means hashing every
        #: archived photo.  ``None`` until the first screen asks for it.
        self.index: ArchiveIndex | None = None
        #: Which event of the plan is which folder.  Recomputed from the index
        #: on every change to the partition, which is cheap enough to do inline.
        self.reconcile = Reconcile()
        #: Files already in the archive that the plan now wants elsewhere.  Set
        #: once the user has seen the preview and agreed to it.
        self.restructure: Restructure | None = None
        #: The one archive read of this run, shared by everyone who needs it.
        self._index_task: asyncio.Task | None = None
        #: Set when --strict turned down moves the plan asked for, so the exit
        #: code can tell a script that the archive is not what was expected.
        self.strict_refused = False

    @property
    def adopts(self) -> bool:
        """True when the run may recognise events it has archived before."""
        return not getattr(self.opts, "no_adopt", False)

    @property
    def archive_known(self) -> bool:
        """True when there is an archive to compare this card against."""
        return self.archive is not None and self.adopts

    @property
    def strict(self) -> bool:
        """True when the run may not move files the archive already holds."""
        return bool(getattr(self.opts, "strict", False))

    def refresh_reconcile(self) -> None:
        """Match the plan against the archive again, after an edit."""
        self.reconcile = reconcile(self.plan, self.index or ArchiveIndex())

    def adoption_of(self, event: Event) -> str:
        """The name this event already has in the archive, if any."""
        return self.reconcile.of(event).name

    def preview_restructure(self) -> Restructure:
        """What the archive would have to change to match the plan."""
        if self.archive is None or not self.adopts:
            return Restructure()
        return plan_restructure(
            self.plan, self.index or ArchiveIndex(), self.reconcile, Path(self.archive)
        )

    async def load_index(self, on_progress: Callable[[int, int], None] | None = None) -> None:
        """Read the archive. The slow part, so it runs in a thread.

        Safe to call again while it is still going: the second caller waits for
        the same answer instead of hashing everything twice.  A run must never
        start copying before this has finished, or it would copy files the
        archive already holds.
        """
        if self.archive is None or not self.adopts:
            return
        if self._index_task is None or self._index_task.done():
            self._index_task = asyncio.create_task(self._read_index(on_progress))
        await asyncio.shield(self._index_task)

    async def _read_index(self, on_progress: Callable[[int, int], None] | None) -> None:
        assert self.archive is not None
        archive = self.archive
        loop = asyncio.get_running_loop()

        def report(done: int, total: int) -> None:
            if on_progress is not None:
                loop.call_soon_threadsafe(on_progress, done, total)

        self.index = await asyncio.to_thread(build_index, archive, self.plan, report)
        self.refresh_reconcile()

    @property
    def reading_archive(self) -> bool:
        task = self._index_task
        return task is not None and not task.done()

    def on_mount(self) -> None:
        if self.archive is None and self.adopts:
            # recognising a rerun needs to know where the archive is, and
            # push_screen_wait is not allowed here, so answer it by callback
            self.push_screen(ArchiveDir(), self._asked_for_archive)
            return
        self._open_events()

    def _asked_for_archive(self, chosen: str | None) -> None:
        if chosen:
            self.archive = Path(chosen)
        self._open_events()

    def _open_events(self) -> None:
        self.push_screen(PartitionScreen(self.plan))
