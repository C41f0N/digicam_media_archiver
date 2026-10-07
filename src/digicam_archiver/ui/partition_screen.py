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
from ..reconcile import MatchKind
from .widgets import NodeRef, PreviewPane, gap_text

GAP_STEP = timedelta(minutes=15)

STYLE_EVENT = "bold"
STYLE_EXCLUDED = "dim italic"
STYLE_NAMED = "bold green"

#: what the archive already holds for an event, in the tree label
BADGES: dict[MatchKind, tuple[str, str]] = {
    MatchKind.MATCHED: ("[in the archive]", "dim green"),
    MatchKind.PARTIAL: ("[partly there]", "dim yellow"),
    MatchKind.SPLIT: ("[was one event, now two]", "yellow"),
    MatchKind.MERGE: ("[was two events, now one]", "yellow"),
    MatchKind.AMBIGUOUS: ("[which folder is this?]", "bold red"),
}


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
        # priority: the tree is a ScrollView and would otherwise swallow
        # these for horizontal scrolling once a row is wider than the pane
        Binding("right", "expand", "Open", show=False, priority=True),
        Binding("left", "collapse", "Close", show=False, priority=True),
        Binding("shift+right", "pane_right", "Scroll right", show=False, priority=True),
        Binding("shift+left", "pane_left", "Scroll left", show=False, priority=True),
        Binding("s", "split", "Split here"),
        Binding("m", "merge", "Merge next"),
        Binding("x", "exclude", "Skip event"),
        Binding("alt+x", "skip_above", "Skip all above"),
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
        elif self.app.archive_known:
            self.run_worker(self._load_archive(), name="archive", group="archive")

    async def _load_archive(self) -> None:
        """Read the archive, then show which events are already in it."""
        self._say("reading the archive to see what is in there already...")
        try:
            await self.app.load_index(on_progress=self._index_progress)
        except OSError as exc:
            self._say(f"could not read the archive: {exc}", warn=True)
            return
        archived = sum(1 for e in self.plan.events() if self.app.adoption_of(e))
        if not archived:
            self._say("nothing in the archive matches these events, they are all new")
            return
        self._rebuild(keep=self._current_start())
        self._say(f"{archived} of {len(self.plan.events())} events are already archived")

    def _index_progress(self, done: int, total: int) -> None:
        self._say(f"reading the archive, {done} of {total} photos")

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
        badge = self._badge(event)
        if badge is not None:
            text, style = badge
            label.append(f"   {text}", style=style)
        return label

    def _badge(self, event: Event) -> tuple[str, str] | None:
        """The archive badge, or None when there is nothing worth saying."""
        if not self.app.archive_known or self.plan.is_excluded(event):
            return None
        archived = self.app.reconcile.of(event)
        badge = BADGES.get(archived.kind)
        if badge is not None and not archived.home:
            # files are in the archive but no folder can be this event's home
            return ("[partly here, name it to split the folder]", "dim yellow")
        return badge

    def _rebuild(self, keep: int | None = None) -> None:
        self.app.refresh_reconcile()
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
        parts = [
            f"{len(self.plan.events())} events",
            f"{len(self.plan.included())} to archive",
        ]
        if self.app.archive_known:
            archived = sum(1 for e in self.plan.events() if self.app.adoption_of(e))
            parts.append(f"{archived} already archived")
        parts.append(f"gap {gap_text(self.plan.gap)}")
        self._say(", ".join(parts))

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

    def action_pane_right(self) -> None:
        """Scroll the list sideways, past the edge of the pane."""
        tree = self.query_one("#events", Tree)
        tree.scroll_right(animate=False)
        if not tree.show_horizontal_scrollbar:
            self._say("everything fits, nothing to scroll")

    def action_pane_left(self) -> None:
        """Scroll the list back towards its left edge."""
        tree = self.query_one("#events", Tree)
        tree.scroll_left(animate=False)
        if not tree.show_horizontal_scrollbar or tree.scroll_x == 0:
            self._say("back at the left edge")

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

    def action_skip_above(self) -> None:
        ref = self._current_ref()
        if ref is None or ref.kind != "event":
            return
        event = self._event_of(ref.start)
        if event is None:
            return
        self._rebuild(keep=event.start)
        self._say_skip_above(event, self.plan.skip_above(event))

    def _say_skip_above(self, event: Event, newly_skipped: int) -> None:
        above = next(
            (i for i, other in enumerate(self.plan.events()) if other.start == event.start),
            len(self.plan.events()),
        )
        if above == 0:
            self._say("there are no events before this one")
        elif newly_skipped:
            self._say(f"skipped {newly_skipped} of {above} events before this one")
        else:
            self._say(f"the {above} events before this one are already skipped")

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
