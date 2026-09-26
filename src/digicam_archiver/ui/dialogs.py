"""Small modal dialogs: confirmations, choices, and one-line text input."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static


class Dialog(ModalScreen):
    """Shared chrome so every dialog looks the same."""

    DEFAULT_CSS = """
    Dialog {
        align: center middle;
    }
    Dialog > Vertical {
        width: 66;
        max-width: 90%;
        height: auto;
        background: $surface;
        border: round $accent;
        padding: 1 2;
    }
    Dialog .message {
        height: auto;
        margin-bottom: 1;
    }
    Dialog .buttons {
        height: auto;
        align: center middle;
    }
    Dialog .buttons Button {
        margin: 0 1;
        min-width: 14;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def action_cancel(self) -> None:
        self.dismiss(None)


class Notice(Dialog):
    """A message with a single dismiss button."""

    DEFAULT_CSS = (
        Dialog.DEFAULT_CSS
        + """
    Notice .body {
        height: auto;
        max-height: 20;
    }
    """
    )

    def __init__(self, title: str, body: str) -> None:
        super().__init__()
        self.title_text = title
        self.body_text = body

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.title_text, classes="message")
            yield Static(self.body_text, classes="body")
            with Horizontal(classes="buttons"):
                yield Button("OK", variant="primary", id="ok")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss("ok")


class Choice(Dialog):
    """Pick one of a few options. Returns the button id, or None if cancelled."""

    def __init__(self, message: str, choices: list[tuple[str, str]], detail: str = "") -> None:
        super().__init__()
        self.message_text = message
        self.choices = choices
        self.detail_text = detail

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.message_text, classes="message")
            if self.detail_text:
                yield Static(self.detail_text, classes="detail")
            with Horizontal(classes="buttons"):
                for label, value in self.choices:
                    yield Button(label, id=value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id)


class TextInput(Dialog):
    """One line of text, prefilled. Returns the text, or None if cancelled."""

    DEFAULT_CSS = (
        Dialog.DEFAULT_CSS
        + """
    TextInput Input {
        width: 100%;
    }
    TextInput .error {
        color: $error;
        height: auto;
    }
    TextInput .hint {
        color: $text-muted;
        height: auto;
    }
    """
    )

    def __init__(
        self,
        message: str,
        value: str = "",
        placeholder: str = "",
        hint: str = "",
    ) -> None:
        super().__init__()
        self.message_text = message
        self.value = value
        self.placeholder = placeholder
        self.hint = hint
        self._error = ""

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.message_text, classes="message")
            yield Input(
                value=self.value,
                placeholder=self.placeholder,
                id="field",
            )
            yield Static(self.hint, classes="hint")
            yield Static("", classes="error", id="error")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("OK", variant="primary", id="ok")

    def on_mount(self) -> None:
        field = self.query_one("#field", Input)
        field.focus()
        field.cursor_position = len(field.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        self.dismiss(self.query_one("#field", Input).value)


class ArchiveDir(Dialog):
    """Ask where the archive lives, creating it if need be."""

    DEFAULT_CSS = (
        Dialog.DEFAULT_CSS
        + """
    ArchiveDir Input {
        width: 100%;
    }
    ArchiveDir .error {
        color: $error;
        height: auto;
    }
    """
    )

    def __init__(self, suggestion: Path | None = None) -> None:
        super().__init__()
        self.suggestion = suggestion
        self.error = ""

    def compose(self) -> ComposeResult:
        start = (
            str(self.suggestion)
            if self.suggestion
            else str(Path.home() / "Pictures" / "archive")
        )
        with Vertical():
            yield Label("Archive directory", classes="message")
            yield Input(value=start, placeholder="~/Pictures/archive", id="field")
            yield Static("", classes="error", id="error")
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Use this", variant="primary", id="ok")

    def on_mount(self) -> None:
        field = self.query_one("#field", Input)
        field.focus()
        field.cursor_position = len(field.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit(event.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        self._submit(self.query_one("#field", Input).value)

    def _submit(self, raw: str) -> None:
        text = raw.strip()
        if not text:
            self.query_one("#error", Static).update("give me a directory")
            return
        path = Path(text).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.query_one("#error", Static).update(str(exc))
            return
        if not path.is_dir():
            self.query_one("#error", Static).update("not a directory")
            return
        self.dismiss(path)
