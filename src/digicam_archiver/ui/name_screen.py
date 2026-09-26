"""Screen two: give every event its name, one at a time."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static

from ..layout import event_dir
from ..model import Event, format_date, format_range
from ..naming import sanitize
from ..partition import Plan
from .dialogs import ArchiveDir, Notice
from .widgets import PreviewPane


class NameScreen(Screen):
    """One event per row; the input names the highlighted one."""

    DEFAULT_CSS = """
    NameScreen #body {
        height: 1fr;
    }
    NameScreen #names {
        width: 52%;
        min-width: 40;
        border-right: solid $panel;
    }
    NameScreen #name-label {
        height: 1;
        padding: 0 1;
    }
    NameScreen #name-input {
        margin: 0 1;
    }
    NameScreen #destination {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    NameScreen #status {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
    }
    NameScreen #status.warn {
        color: $warning;
    }
    """

    BINDINGS = [
        Binding("ctrl+n", "next", "Next event", show=False),
        Binding("ctrl+p", "previous", "Previous event", show=False),
        Binding("ctrl+s", "save", "Save name", show=False),
        Binding("ctrl+t", "to_transfer", "Start the copy"),
        Binding("escape", "back", "Back to events"),
        Binding("q", "quit", "Quit", show=False),
    ]

    def __init__(self, plan: Plan, **kwargs) -> None:
        super().__init__(**kwargs)
        self.plan = plan
        self._events: list[Event] = plan.included()
        self._rebuilding = False

    @property
    def archive(self) -> Path | None:
        archive = getattr(self.app, "archive", None)
        return None if archive is None else Path(archive)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            yield ListView(id="names")
            with Vertical():
                yield Static("Event name", id="name-label")
                yield Input(placeholder="Uni Friends Hangout", id="name-input")
                yield Static("", id="destination")
                yield PreviewPane(colour=self.app.preview_colour, id="preview")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self._rebuild_list()
        if self._events:
            self._select(0)
            self.query_one("#name-input", Input).focus()
        else:
            self._say("no events left to name", warn=True)

    # ----------------------------------------------------------------- list

    def _rebuild_list(self) -> None:
        view = self.query_one("#names", ListView)
        self._rebuilding = True
        try:
            view.clear()
            for event in self._events:
                name = self.plan.name_of(event)
                label = Text()
                label.append(f"{format_range(event)}\n", style="dim")
                label.append(
                    f"  {name}" if name else "  (not named yet)",
                    style="bold green" if name else "dim italic",
                )
                view.append(ListItem(Label(label)))
        finally:
            self._rebuilding = False

    def _position(self) -> int:
        index = self.query_one("#names", ListView).index
        return 0 if index is None else index

    def _current(self) -> Event | None:
        position = self._position()
        if not (0 <= position < len(self._events)):
            return None
        return self._events[position]

    def _select(self, position: int) -> None:
        if not self._events:
            return
        position = max(0, min(position, len(self._events) - 1))
        self.query_one("#names", ListView).index = position
        event = self._events[position]
        field = self.query_one("#name-input", Input)
        existing = self.plan.name_of(event)
        field.value = existing if existing is not None else f"{format_date(event.taken_start)} "
        field.cursor_position = len(field.value)
        self._show_destination(event)
        videos = len(event.videos)
        extra = f"first of {len(event.files)} files"
        if videos:
            extra += f", {videos} video" + ("s" if videos != 1 else "")
        self.query_one("#preview", PreviewPane).show_file(event.first, extra)
        named = sum(1 for item in self._events if self.plan.name_of(item))
        self._say(f"event {position + 1} of {len(self._events)}, {named} named")

    def _show_destination(self, event: Event) -> None:
        target = self.query_one("#destination", Static)
        archive = self.archive
        if archive is None:
            target.update(Text("archive directory not chosen yet", style="dim"))
            return
        name = self.plan.name_of(event) or "<not named yet>"
        target.update(
            Text(
                f"goes to {event_dir(archive, event.taken_start, sanitize(name))}", style="dim"
            )
        )

    def _say(self, message: str, warn: bool = False) -> None:
        status = self.query_one("#status", Static)
        status.set_classes(["warn"] if warn else [])
        status.update(message)

    # --------------------------------------------------------------- events

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if self._rebuilding:
            return
        index = event.list_view.index
        if index is None or not (0 <= index < len(self._events)):
            return
        self._select(index)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.query_one("#name-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._commit(event.input.value):
            self._select(self._position() + 1)

    # -------------------------------------------------------------- actions

    def _commit(self, raw: str) -> bool:
        current = self._current()
        if current is None:
            return False
        cleaned = sanitize(raw, fallback="")
        if not cleaned:
            self._say("an event needs a name, even one word", warn=True)
            return False
        taken = {
            self.plan.name_of(event)
            for event in self._events
            if event is not current and self.plan.name_of(event)
        }
        if cleaned in taken:
            self._say(f"{cleaned!r} is already used in this plan", warn=True)
            return False
        self.plan.set_name(current, cleaned)
        self._rebuild_list()
        self._show_destination(current)
        return True

    def action_save(self) -> None:
        if self._commit(self.query_one("#name-input", Input).value):
            self._say("name saved")

    def action_next(self) -> None:
        self._select(self._position() + 1)

    def action_previous(self) -> None:
        self._select(self._position() - 1)

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_quit(self) -> None:
        self.app.exit()

    def action_to_transfer(self) -> None:
        self.run_worker(self._to_transfer(), name="to-transfer", exclusive=True)

    async def _to_transfer(self) -> None:
        from .transfer_screen import TransferScreen

        unnamed = [event for event in self._events if not self.plan.name_of(event)]
        if unnamed:
            self._say(
                f"{len(unnamed)} event(s) still unnamed: put the cursor on one and type a name",
                warn=True,
            )
            return
        if self.app.dry_run:
            await self.app.push_screen_wait(
                Notice("Dry run, nothing was copied", self._dry_run_text())
            )
            return
        if self.app.archive is None:
            chosen = await self.app.push_screen_wait(ArchiveDir())
            if chosen is None:
                return
            self.app.archive = Path(chosen)
            self._say(f"archive: {self.app.archive}")
        self.app.push_screen(TransferScreen(self.plan, Path(self.app.archive)))

    def _dry_run_text(self) -> str:
        from ..layout import event_dir as target_of

        archive = self.archive or Path("?")
        lines = [f"archive would be {archive}", ""]
        for event in self._events:
            name = self.plan.name_of(event) or "?"
            lines.append(f"{target_of(archive, event.taken_start, name)}")
            lines.append(f"    {len(event.files)} files, {len(event.videos)} videos")
        return "\n".join(lines)
