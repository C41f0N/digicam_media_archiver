"""The Textual application: three screens, one plan."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from textual.app import App

from ..partition import Plan
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
        )

    def on_mount(self) -> None:
        self.push_screen(PartitionScreen(self.plan))
