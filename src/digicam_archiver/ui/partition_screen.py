"""Screen one: check the automatic partitioning and fix it by hand."""

from __future__ import annotations

from datetime import timedelta

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Footer, Header, Static, Tree
from textual.widgets.tree import TreeNode

from ..model import Event, format_range, human_size
from ..partition import Plan
from .widgets import NodeRef, PreviewPane, gap_text

GAP_STEP = timedelta(minutes=15)

STYLE_EVENT = "bold"
STYLE_EXCLUDED = "dim italic"
STYLE_NAMED = "bold green"


class PartitionScreen(Screen):
    """Events on the left, preview on the right."""

    DEFAULT_CSS = """
    PartitionScreen #body {
        height: 1fr;
    }
    PartitionScreen #events {
        width: 52%;
        min-width: 40;
        border-right: solid $panel;
    }
    PartitionScreen #status {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
    }
    PartitionScreen #status.warn {
        color: $warning;
    }
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("right", "expand", "Open", show=False),
        Binding("left", "collapse", "Close", show=False),
        Binding("s", "split", "Split here"),
        Binding("m", "merge", "Merge next"),
        Binding("x", "exclude", "Skip event"),
        Binding("left_square_bracket", "gap_down", "Shorter gap"),
        Binding("right_square_bracket", "gap_up", "Longer gap"),
        Binding("g", "auto", "Re-run auto"),
        Binding("c", "colour", "Colour"),
        Binding("n", "next", "Name events"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, plan: Plan, **kwargs) -> None:
        super().__init__(**kwargs)
        self.plan = plan
        self._event_nodes: dict[int, TreeNode] = {}
        self._status = ""
        self._pending_select: int | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            yield Tree("Events", id="events")
            yield PreviewPane(colour=self.app.preview_colour, id="preview")
        yield Static("", id="status")
        yield Footer()

    def on_mount(self) -> None:
        # left and right arrows are the only way nodes open and close
        self.query_one("#events", Tree).auto_expand = False
        self._rebuild(keep=0)
        if not self._event_nodes:
            self._say("no photos or videos found", warn=True)

    # ------------------------------------------------------------------ tree

    def _event_label(self, event: Event, number: int) -> Text:
        excluded = self.plan.is_excluded(event)
        name = self.plan.name_of(event)
        videos = len(event.videos)
        summary = f"{len(event.files)} files"
        if videos:
            summary += f", {videos} video" + ("s" if videos != 1 else "")
        summary += f", {human_size(event.size)}"
        label = Text()
        label.append(f"{number:>3} ", style="dim")
        label.append(
            f"{format_range(event)}  ", style=STYLE_EXCLUDED if excluded else STYLE_EVENT
        )
        label.append(summary, style="dim")
        if name:
            label.append(f"   {name}", style=STYLE_NAMED)
        if excluded:
            label.append("   [skipped]", style="dim yellow")
        return label

    def _rebuild(self, keep: int | None = None) -> None:
        tree = self.query_one("#events", Tree)
        selected = keep if keep is not None else self._current_start()
        tree.clear()
        self._event_nodes.clear()
        for number, event in enumerate(self.plan.events(), start=1):
            node = tree.root.add(
                self._event_label(event, number),
                data=NodeRef("event", event.start, event.start),
            )
            self._event_nodes[event.start] = node
        tree.root.expand()
        self._pending_select = selected
        tree.focus()
        # node line numbers only exist once the tree has laid itself out
        self.call_after_refresh(self._apply_selection)
        self._say(
            f"{len(self.plan.events())} events, "
            f"{len(self.plan.included())} to archive, gap {gap_text(self.plan.gap)}"
        )

    def _apply_selection(self) -> None:
        start = self._pending_select
        if start is None or not self.is_mounted:
            return
        node = self._event_nodes.get(start)
        if node is None:
            return
        # move_cursor, not select_node: select_node would toggle the node open
        self.query_one("#events", Tree).move_cursor(node)
        self._pending_select = None

    def _load_files(self, event: Event) -> None:
        """Fill an event's children on first expand; a card can hold thousands."""
        node = self._event_nodes.get(event.start)
        if node is None or node.children:
            return
        for offset, item in enumerate(event.files):
            mark = "V" if item.is_video else " "
            node.add_leaf(
                Text(f" {mark} {item.taken:%H:%M:%S}  {item.name}", style="dim"),
                data=NodeRef("file", event.start, event.start + offset),
            )

    def _current_ref(self) -> NodeRef | None:
        tree = self.query_one("#events", Tree)
        node = tree.cursor_node
        if node is None or node.data is None:
            return None
        ref: NodeRef = node.data
        return ref

    def _current_start(self) -> int | None:
        ref = self._current_ref()
        return None if ref is None else ref.start

    def _event_of(self, start: int) -> Event | None:
        return next((e for e in self.plan.events() if e.start == start), None)

    def _say(self, message: str, warn: bool = False) -> None:
        status = self.query_one("#status", Static)
        status.set_classes(["warn"] if warn else [])
        status.update(message)

    # --------------------------------------------------------------- events

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        ref: NodeRef | None = event.node.data
        if ref is None:
            return
        item = self.plan.files[ref.index]
        if ref.kind == "event":
            owner = self._event_of(ref.start)
            if owner is not None:
                self._load_files(owner)
                extra = "whole event" if len(owner.files) > 1 else ""
                self.query_one("#preview", PreviewPane).show_file(item, extra)
            return
        self.query_one("#preview", PreviewPane).show_file(item)

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        ref: NodeRef | None = event.node.data
        if ref is None or ref.kind != "event":
            return
        owner = self._event_of(ref.start)
        if owner is not None:
            self._load_files(owner)

    # -------------------------------------------------------------- actions

    def action_cursor_down(self) -> None:
        self.query_one("#events", Tree).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#events", Tree).action_cursor_up()

    def action_expand(self) -> None:
        """Open the highlighted event, or step into it when it is already open."""
        tree = self.query_one("#events", Tree)
        node = tree.cursor_node
        if node is None or node.data is None:
            return
        event = self._event_of(node.data.start)
        if event is not None:
            self._load_files(event)
        if not node.children:
            return
        if node.is_expanded:
            tree.move_cursor(node.children[0])
        else:
            node.expand()

    def action_collapse(self) -> None:
        tree = self.query_one("#events", Tree)
        node = tree.cursor_node
        if node is None:
            return
        if node.children and node.is_expanded:
            node.collapse()
            return
        parent = node.parent
        if parent is not None and parent.data is not None:
            tree.move_cursor(parent)

    def action_split(self) -> None:
        ref = self._current_ref()
        if ref is None:
            return
        if ref.kind != "file":
            self._say("open an event and put the cursor on a file to split there", warn=True)
            return
        if ref.index <= ref.start:
            self._say("this file already starts its event", warn=True)
            return
        if self.plan.split_before(ref.index):
            self._rebuild(keep=ref.start)
            self._say(f"split before {self.plan.files[ref.index].name}")

    def action_merge(self) -> None:
        ref = self._current_ref()
        if ref is None:
            return
        event = self._event_of(ref.start)
        if event is None:
            return
        if self.plan.merge_next(event):
            self._rebuild(keep=event.start)
            self._say(f"merged with the next event, {len(self.plan.events())} left")

    def action_exclude(self) -> None:
        ref = self._current_ref()
        if ref is None:
            return
        event = self._event_of(ref.start)
        if event is None:
            return
        excluded = self.plan.toggle_excluded(event)
        self._rebuild(keep=event.start)
        self._say("skipped, it will not be archived" if excluded else "back in the plan")

    def action_gap_down(self) -> None:
        self.plan.set_gap(self.plan.gap - GAP_STEP)
        self._rebuild()

    def action_gap_up(self) -> None:
        self.plan.set_gap(self.plan.gap + GAP_STEP)
        self._rebuild()

    def action_auto(self) -> None:
        self.plan.forced_starts.clear()
        self.plan.suppressed_splits.clear()
        self._rebuild()
        self._say(f"re-partitioned from timestamps alone, gap {gap_text(self.plan.gap)}")

    def action_colour(self) -> None:
        pane = self.query_one("#preview", PreviewPane)
        pane.colour = not pane.colour
        self._say("colour preview" if pane.colour else "plain ascii preview")

    def action_next(self) -> None:
        from .name_screen import NameScreen

        if not self.plan.included():
            self._say("every event is skipped, nothing to archive", warn=True)
            return
        self.app.push_screen(NameScreen(self.plan))

    def action_quit(self) -> None:
        self.app.exit()
