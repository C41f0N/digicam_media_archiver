"""Screen three: copy the photos, convert the videos, show how far along."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, ProgressBar, RichLog, Static

from ..handbrake import HandBrakeMissing, require_handbrake
from ..layout import ExistingFiles, build_items, event_dir, existing_event_names
from ..model import Event, format_date, format_range, human_size
from ..naming import sanitize, unique_name
from ..partition import Plan
from ..reconcile import Archived
from ..transfer import Transfer, TransferJob, TransferResult
from .dialogs import Choice, Notice, TextInput
from .widgets import format_eta


class Cancelled(Exception):
    """The run was called off while it was still being set up."""


@dataclass
class TransferState:
    total_items: int = 0
    done_items: int = 0
    current_fraction: float = 0.0
    videos_total: int = 0
    videos_left: int = 0
    started: float = 0.0
    running: bool = False


class TransferReporter:
    """Feeds the widgets. Runs on the event loop, beside the transfer."""

    def __init__(self, screen: TransferScreen) -> None:
        self.screen = screen

    def run_start(self, jobs: list[TransferJob]) -> None:
        state = self.screen.state
        state.total_items = sum(len(job.items) for job in jobs)
        state.videos_total = sum(len(job.videos) for job in jobs)
        state.videos_left = state.videos_total
        state.done_items = 0
        state.started = time.monotonic()
        state.running = True
        self.screen.refresh_summary()

    def event_start(self, index: int, total: int, dest_dir: Path) -> None:
        self.screen.log_line(f"[{index}/{total}] {dest_dir}")

    def item_start(self, item, index: int, total: int) -> None:
        self.screen.state.current_fraction = 0.0
        try:
            size = human_size(item.source.stat().st_size)
        except OSError:  # pragma: no cover - defensive
            size = "?"
        kind = "mp4" if item.is_video else "jpg"
        self.screen.set_current(f"{index}/{total}  [{kind}] {item.source.name}  {size}")
        self.screen.item_bar.update(total=1.0, progress=0.0)

    def item_progress(self, item, done_units: float, total_units: float, progress) -> None:
        fraction = 0.0
        if total_units:
            fraction = max(0.0, min(done_units / total_units, 1.0))
        self.screen.state.current_fraction = fraction
        self.screen.item_bar.update(total=1.0, progress=fraction)
        if progress is not None and progress.eta_seconds is not None:
            self.screen.set_eta(progress.eta_seconds)
        else:
            self.screen.set_eta(self._overall_eta(fraction))

    def item_done(self, item, skipped: bool) -> None:
        state = self.screen.state
        state.done_items += 1
        state.current_fraction = 0.0
        self.screen.log_line(
            f"   {'skipped, already there' if skipped else 'done'}: {item.dest.name}"
        )
        self.screen.refresh_counts()

    def item_failed(self, item, error: BaseException) -> None:
        self.screen.log_line(f"   FAILED {item.source.name}: {error}", error=True)

    def log(self, message: str) -> None:
        self.screen.log_line(message, error=True)

    def run_done(self, result: TransferResult) -> None:
        self.screen.state.running = False
        self.screen.finish(result)

    def _overall_eta(self, fraction: float) -> float | None:
        state = self.screen.state
        if not state.running or state.total_items == 0:
            return None
        overall = (state.done_items + fraction) / state.total_items
        if overall <= 0.01:
            return None
        elapsed = time.monotonic() - state.started
        return elapsed * (1.0 - overall) / overall


class TransferScreen(Screen):
    """Two bars: the file being handled right now, and the whole run."""

    DEFAULT_CSS = """
    TransferScreen #summary {
        height: auto;
        padding: 0 1;
    }
    TransferScreen .row {
        height: 1;
        padding: 0 1;
    }
    TransferScreen #eta {
        color: $text-muted;
    }
    TransferScreen #finished {
        height: auto;
        padding: 0 1;
        color: $success;
    }
    TransferScreen #finished.warn {
        color: $warning;
    }
    TransferScreen #log {
        height: 1fr;
        border-top: solid $panel;
    }
    """

    BINDINGS = [
        Binding("escape", "abort", "Stop"),
        Binding("r", "rerun", "Run again"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, plan: Plan, archive: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.plan = plan
        self.archive = Path(archive)
        self.state = TransferState()
        self.jobs: list[TransferJob] = []
        self.cancel = asyncio.Event()
        self.reporter = TransferReporter(self)
        self._left_out: int = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("", id="summary")
        yield Static("", id="counts", classes="row")
        yield ProgressBar(id="overall-bar", show_eta=False)
        yield Static("", id="current", classes="row")
        yield ProgressBar(id="item-bar", show_eta=False)
        yield Static("", id="eta", classes="row")
        yield Static("", id="finished")
        yield RichLog(id="log", wrap=True, markup=False, max_lines=200)
        yield Footer()

    @property
    def overall_bar(self) -> ProgressBar:
        return self.query_one("#overall-bar", ProgressBar)

    @property
    def item_bar(self) -> ProgressBar:
        return self.query_one("#item-bar", ProgressBar)

    def on_mount(self) -> None:
        self.overall_bar.update(total=1.0, progress=0.0)
        self.item_bar.update(total=1.0, progress=0.0)
        self.refresh_summary()
        self.run_worker(self._prepare(), name="prepare", exclusive=True)

    # ------------------------------------------------------------- display

    def refresh_summary(self) -> None:
        events = self.plan.included()
        files = sum(len(event.files) for event in events)
        videos = sum(len(event.videos) for event in events)
        total = sum(event.size for event in events)
        self.query_one("#summary", Static).update(
            f"archive {self.archive}\n"
            f"{len(events)} events, {files} files ({videos} videos), {human_size(total)}"
        )
        self.refresh_counts()

    def refresh_counts(self) -> None:
        state = self.state
        overall = 0.0
        if state.total_items:
            overall = (state.done_items + state.current_fraction) / state.total_items
            self.overall_bar.update(total=1.0, progress=overall)
        self.query_one("#counts", Static).update(
            f"files {state.done_items}/{state.total_items} copied   "
            f"videos {state.videos_left}/{state.videos_total} left"
        )

    def set_current(self, text: str) -> None:
        self.query_one("#current", Static).update(text)

    def set_eta(self, seconds: float | None) -> None:
        self.query_one("#eta", Static).update(f"eta {format_eta(seconds)}")

    def log_line(self, message: str, error: bool = False) -> None:
        self.query_one("#log", RichLog).write(f"{'! ' if error else ''}{message}")

    def finish(self, result: TransferResult) -> None:
        parts = [f"copied {result.copied}"]
        if result.skipped:
            parts.append(f"{result.skipped} already there")
        if result.failed:
            parts.append(f"{result.failed} failed")
        if self._left_out:
            parts.append(f"{self._left_out} videos left for later")
        if result.cancelled:
            parts.append("stopped early, press r to finish the rest")
        headline = ", ".join(parts)
        if result.ok:
            text = headline
            classes: list[str] = []
        else:
            text = f"STOPPED: {headline}"
            classes = ["warn"]
        finished = self.query_one("#finished", Static)
        finished.set_classes(classes)
        finished.update(text)
        self.item_bar.update(total=1.0, progress=0.0)
        self.refresh_counts()
        self.set_eta(None)
        self.log_line(headline, error=not result.ok)
        if self.app.restructure is not None and not self.app.dry_run:
            self.run_worker(
                self._apply_changes(),
                name="restructure",
                exclusive=True,
                group="restructure",
            )

    async def _apply_changes(self) -> None:
        """Put the archive in the shape the plan asks for, after the copying."""
        from ..restructure import apply as apply_restructure

        change = self.app.restructure
        if change is None:
            return
        self.app.restructure = None
        await asyncio.to_thread(apply_restructure, change, self.archive, self.log_line)

    # -------------------------------------------------------------- workers

    async def _prepare(self) -> None:
        try:
            self.jobs = await self._build_jobs()
        except Cancelled:
            self.query_one("#finished", Static).update("run cancelled")
            return
        if not self.jobs:
            self.query_one("#finished", Static).update("nothing to copy")
            return

        wanted = sum(len(job.videos) for job in self.jobs)
        if wanted and not self.app.opts.handbrake:
            try:
                require_handbrake()
            except HandBrakeMissing as exc:
                await self.app.push_screen_wait(
                    Notice(
                        "HandBrakeCLI is not installed",
                        f"{exc}\n\n"
                        f"The {wanted} video(s) in this plan will be left alone for now, "
                        "the photos still get copied.\n\n"
                        "Install HandBrakeCLI, then press r to run again.",
                    )
                )
                self.jobs = [self._without_videos(job) for job in self.jobs]
                self._left_out = wanted
        await self._run()

    def _without_videos(self, job: TransferJob) -> TransferJob:
        return TransferJob(
            label=job.label,
            dest_dir=job.dest_dir,
            items=tuple(item for item in job.items if not item.is_video),
        )

    async def _build_jobs(self) -> list[TransferJob]:
        jobs: list[TransferJob] = []
        for event in self.plan.included():
            job = await self._build_job(event)
            if job is not None:
                jobs.append(job)
        return jobs

    def _archived(self, event: Event) -> Archived | None:
        """What the archive knows about this event, or None when not adopting."""
        if not self.app.archive_known:
            return None
        return self.app.reconcile.of(event)

    async def _build_job(self, event: Event) -> TransferJob | None:
        name = self.plan.name_of(event) or ""
        archived = self._archived(event)
        there = set(archived.present) if archived is not None else set()
        if self.app.strict and there and archived is not None and archived.home is None:
            # --strict will not move these into the folder the plan asked for,
            # and copying them would put every one of them in the archive twice
            self.log_line(
                f"left as it is: {format_range(event)} is already in the archive, "
                "under a different name, and --strict moves nothing"
            )
            return None
        if archived is not None and archived.home is not None:
            # the folder is already there: copy into it, do not make a twin
            target = archived.home
        elif not name:
            self.log_line(
                f"left on the card, not named: {format_range(event)}"
                if not (archived and archived.present)
                else f"left as it is: {format_range(event)} is inside "
                f"{archived.home.name if archived.home else 'a folder'} and is not named"
            )
            return None
        else:
            target = event_dir(self.archive, event.taken_start, name)
            if target.exists():
                decision = await self._ask_about_existing(event, target)
                if decision == "skip":
                    self.log_line(f"skipped {target.name}, it is already in the archive")
                    return None
                if decision == "cancel":
                    raise Cancelled
                if decision == "rename":
                    target = await self._ask_for_new_name(event, name)
                    if target is None:
                        return None
        target.mkdir(parents=True, exist_ok=True)
        # only what the archive is missing: files it already holds are the
        # restructure's business, copying them again would double them up
        wanted = [item for item in event.files if item.path not in there]
        items = build_items(target, wanted, existing=self._existing(target))
        return TransferJob(label=target.name, dest_dir=target, items=tuple(items))

    def _existing(self, target: Path) -> ExistingFiles | None:
        """What the folder already holds, so reruns do not copy it twice."""
        if self.app.index is None:
            return None
        folder = self.app.index.by_path(target)
        return folder.files if folder is not None else None

    async def _ask_about_existing(self, event: Event, target: Path) -> str:
        already = sum(1 for _ in target.iterdir())
        choice = await self.app.push_screen_wait(
            Choice(
                f"{target.name} already exists",
                [
                    ("Add to it", "merge"),
                    ("Skip event", "skip"),
                    ("New name", "rename"),
                    ("Cancel run", "cancel"),
                ],
                detail=f"{already} file(s) in there, this event has {len(event.files)}",
            )
        )
        return choice or "cancel"

    async def _ask_for_new_name(self, event: Event, name: str) -> Path | None:
        """The same event under another name, so it lands in another folder.

        The folder it came from stays in the running: asking for a new name is
        how you get away from it, so a name already taken gets a ``(2)``.
        """
        typed = await self.app.push_screen_wait(
            TextInput(
                "Name for this event",
                # the name on its own: the folder already has the date and
                # putting it in the box as well would double it up
                value=sanitize(name, fallback=""),
                hint="a different name makes a different folder, the date stays",
            )
        )
        if typed is None:
            return None
        cleaned = sanitize(typed, fallback="")
        if not cleaned:
            return None
        taken = existing_event_names(self.archive, event.taken_start)
        # unique_name counts whole folder names, so date it first and let
        # event_dir take the date off again
        wanted = unique_name(f"{format_date(event.taken_start)} {cleaned}", taken)
        return event_dir(self.archive, event.taken_start, wanted)

    async def _run(self) -> None:
        if not self.jobs:
            return
        self.cancel = asyncio.Event()
        transfer = Transfer(
            self.jobs,
            reporter=self.reporter,
            preset=self.app.opts.preset,
            faststart=self.app.opts.faststart,
            overwrite=self.app.opts.overwrite,
            checksum=self.app.opts.checksum,
            handbrake=str(self.app.opts.handbrake) if self.app.opts.handbrake else None,
            cancel=self.cancel,
        )
        self.state.videos_total = transfer.videos_total
        self.state.videos_left = transfer.videos_total
        await transfer.run()

    # -------------------------------------------------------------- actions

    def action_abort(self) -> None:
        if self.state.running:
            self.cancel.set()
            self.log_line("stopping after the current file")
            self.set_current("stopping...")

    def action_rerun(self) -> None:
        if self.state.running:
            return
        self.query_one("#finished", Static).update("")
        self._left_out = 0
        self.run_worker(self._run(), name="transfer", exclusive=True)

    def action_quit(self) -> None:
        if self.state.running:
            self.action_abort()
        self.app.exit()
