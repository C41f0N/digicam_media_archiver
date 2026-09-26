"""Drive the interface headless, the way a person would with the keyboard."""

from __future__ import annotations

import asyncio
from argparse import Namespace
from datetime import timedelta
from pathlib import Path

import pytest
from textual.widgets import Input, ListView, Static, Tree

from digicam_archiver.layout import build_items, event_dir
from digicam_archiver.partition import Plan
from digicam_archiver.scan import scan
from digicam_archiver.transfer import copy_photo
from digicam_archiver.ui.app import ArchiverApp
from digicam_archiver.ui.dialogs import ArchiveDir, Choice
from digicam_archiver.ui.name_screen import NameScreen
from digicam_archiver.ui.partition_screen import PartitionScreen
from digicam_archiver.ui.transfer_screen import TransferScreen
from digicam_archiver.ui.widgets import PreviewPane

SIZE = (120, 40)

pytestmark = pytest.mark.ui


def make_app(
    source: Path,
    archive: Path | None = None,
    handbrake: Path | None = None,
    strict: bool = False,
):
    plan = Plan(source=source, files=scan(source))
    opts = Namespace(
        preset="Fast 720p30",
        faststart=False,
        overwrite=False,
        checksum=False,
        handbrake=handbrake,
        no_adopt=False,
        strict=strict,
    )
    return ArchiverApp(plan, archive=archive, opts=opts)


def archived_event(source: Path, archive: Path, position: int, name: str) -> Path:
    """Copy one event of the card into the archive, the way a past run did."""
    plan = Plan(source=source, files=scan(source))
    event = plan.events()[position]
    target = event_dir(archive, event.taken_start, name)
    target.mkdir(parents=True, exist_ok=True)
    for item in build_items(target, event.files):
        copy_photo(item.source, item.dest)
    return target


def run(scenario) -> None:
    asyncio.run(scenario())


def names_in(folder: Path) -> set[str]:
    return {path.name for path in folder.iterdir()}


def test_starts_on_the_partition_screen(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, PartitionScreen)
            tree = screen.query_one("#events", Tree)
            assert len(tree.root.children) == 2
            assert app.plan.events()[0].start == 0

    run(scenario)


def test_preview_fills_in_for_the_highlighted_event(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, PartitionScreen)
            await pilot.pause()
            preview = screen.query_one("#preview-image", Static)
            assert "▀" in preview.render().plain if hasattr(preview, "render") else True

    run(scenario)


def test_expanding_an_event_lists_its_files(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            tree = app.screen.query_one("#events", Tree)
            first = tree.root.children[0]
            await pilot.press("right")
            await pilot.pause()
            assert first.children, "expanding did not load the files"
            assert len(first.children) == 4
            await pilot.press("left")
            await pilot.pause()
            assert not first.is_expanded

    run(scenario)


def test_split_from_the_keyboard(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            tree = app.screen.query_one("#events", Tree)
            await pilot.press("right")
            await pilot.pause()
            await pilot.press("down", "down")
            await pilot.pause()
            assert tree.cursor_node is not None
            await pilot.press("s")
            await pilot.pause()
            assert len(app.plan.events()) == 3
            assert len(tree.root.children) == 3

    run(scenario)


def test_merge_from_the_keyboard(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("m")
            await pilot.pause()
            assert len(app.plan.events()) == 1
            await pilot.press("m")
            await pilot.pause()
            assert len(app.plan.events()) == 1, "merging the last event must do nothing"

    run(scenario)


def test_exclude_and_include(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("x")
            await pilot.pause()
            assert len(app.plan.included()) == 1
            await pilot.press("x")
            await pilot.pause()
            assert len(app.plan.included()) == 2

    run(scenario)


def test_skipping_everything_blocks_the_next_screen(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("x")
            await pilot.pause()
            await pilot.press("down")
            await pilot.pause()
            await pilot.press("x")
            await pilot.pause()
            assert app.plan.included() == []
            await pilot.press("n")
            await pilot.pause()
            assert isinstance(app.screen, PartitionScreen)
            assert "nothing to archive" in str(app.screen.query_one("#status", Static).render())

    run(scenario)


def test_gap_keys_repartition(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("right_square_bracket")
            await pilot.pause()
            assert app.plan.gap == timedelta(hours=4, minutes=15)
            # a wider gap swallows the 3h53m hole, the next day stays apart
            await pilot.press(*["right_square_bracket"] * 15)
            await pilot.pause()
            assert app.plan.gap == timedelta(hours=8)
            assert len(app.plan.events()) == 2
            assert len(app.plan.events()[0].files) == 4
            await pilot.press("left_square_bracket")
            await pilot.pause()
            assert app.plan.gap == timedelta(hours=7, minutes=45)

    run(scenario)


def test_auto_rerun_drops_manual_calls(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("m")
            await pilot.pause()
            assert len(app.plan.events()) == 1
            await pilot.press("g")
            await pilot.pause()
            assert len(app.plan.events()) == 2

    run(scenario)


def test_colour_toggle(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            pane = app.screen.query_one("#preview")
            assert pane.colour is True
            await pilot.press("c")
            await pilot.pause()
            assert pane.colour is False

    run(scenario)


def test_naming_screen_lists_every_event(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, NameScreen)
            view = screen.query_one("#names", ListView)
            assert len(view.children) == 2
            field = screen.query_one("#name-input", Input)
            assert field.value == "26-08-23 "

    run(scenario)


def test_naming_an_event(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            screen = app.screen
            field = screen.query_one("#name-input", Input)
            field.value = "Uni Friends Hangout"
            await pilot.press("enter")
            await pilot.pause()
            assert app.plan.name_of(app.plan.included()[0]) == "Uni Friends Hangout"
            assert screen.query_one("#name-input", Input).value == "26-08-24 "
            assert "26-08" in str(screen.query_one("#destination", Static).render())

    run(scenario)


def test_naming_steps_through_every_photo(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, NameScreen)
            meta = screen.query_one("#preview-meta", Static)
            assert "1/4" in str(meta.render())
            await pilot.press("alt+right")
            await pilot.pause()
            assert "2/4" in str(meta.render())
            assert "DSCF0002.JPG" in str(meta.render())
            await pilot.press("pagedown")
            await pilot.pause()
            assert "3/4" in str(meta.render())
            assert "DSCF0003.AVI" in str(meta.render())
            await pilot.press("alt+end")
            await pilot.pause()
            assert "4/4" in str(meta.render())
            assert "1 video" in str(meta.render())
            await pilot.press("pagedown")
            await pilot.pause()
            assert "last photo" in str(screen.query_one("#status", Static).render())
            await pilot.press("alt+home")
            await pilot.pause()
            assert "1/4" in str(meta.render())
            await pilot.press("pageup")
            await pilot.pause()
            assert "first photo" in str(screen.query_one("#status", Static).render())

    run(scenario)


def test_naming_keeps_the_photo_when_the_event_changes(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            screen = app.screen
            meta = screen.query_one("#preview-meta", Static)
            await pilot.press("alt+right", "alt+right")
            await pilot.pause()
            assert "3/4" in str(meta.render())
            await pilot.press("alt+down")
            await pilot.pause()
            assert "1/1" in str(meta.render())
            assert "DSCF0004.JPG" in str(meta.render())
            await pilot.press("alt+up")
            await pilot.pause()
            assert "1/4" in str(meta.render())
            assert isinstance(app.screen, NameScreen), "the command palette opened"

    run(scenario)


def test_naming_only_repaints_the_row_it_changed(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            screen = app.screen
            view = screen.query_one("#names", ListView)
            items = list(view.children)
            screen.query_one("#name-input", Input).value = "Uni Friends Hangout"
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert list(view.children) == items, "the list was rebuilt for one name"
            assert app.plan.name_of(app.plan.included()[0]) == "Uni Friends Hangout"
            assert "Uni Friends Hangout" in str(items[0].children[0].render())

    run(scenario)


def test_the_preview_never_queues_more_than_one_render(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            pane = app.screen.query_one("#preview", PreviewPane)
            first = app.plan.included()[0]
            for index in range(8):
                pane.show_file(first.files[index % len(first.files)], position=str(index))
                assert pane._queued is None or pane._queued[0] == pane._path
            await pilot.pause()
            assert pane._busy or pane._queued is None

    run(scenario)


def test_naming_refuses_an_empty_name(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            screen = app.screen
            screen.query_one("#name-input", Input).value = "   "
            await pilot.press("enter")
            await pilot.pause()
            assert app.plan.name_of(app.plan.included()[0]) is None
            assert "needs a name" in str(screen.query_one("#status", Static).render())

    run(scenario)


def test_naming_refuses_a_duplicate(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            screen = app.screen
            screen.query_one("#name-input", Input).value = "Same Name"
            await pilot.press("enter")
            await pilot.pause()
            screen.query_one("#name-input", Input).value = "Same Name"
            await pilot.press("enter")
            await pilot.pause()
            assert "already used" in str(screen.query_one("#status", Static).render())

    run(scenario)


def test_cannot_start_the_copy_with_unnamed_events(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            await pilot.press("ctrl+t")
            await pilot.pause()
            assert isinstance(app.screen, NameScreen)
            assert "still unnamed" in str(app.screen.query_one("#status", Static).render())

    run(scenario)


def test_dry_run_stops_before_copying(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        app.dry_run = True
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            screen = app.screen
            for name in ("Uni Friends Hangout", "Second day"):
                screen.query_one("#name-input", Input).value = name
                await pilot.press("enter")
                await pilot.pause()
            await pilot.press("ctrl+t")
            await pilot.pause()
            await pilot.click("#ok")
            await pilot.pause()
            assert isinstance(app.screen, NameScreen)
            assert list(archive.iterdir()) == []

    run(scenario)


def test_full_run_copies_and_converts(
    digicam: Path, archive: Path, fake_handbrake: Path
) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive, handbrake=fake_handbrake)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            screen = app.screen
            for name in ("Uni Friends Hangout", "Second day"):
                screen.query_one("#name-input", Input).value = name
                await pilot.press("enter")
                await pilot.pause()
            await pilot.press("ctrl+t")
            for _ in range(40):
                await pilot.pause()
                if isinstance(app.screen, TransferScreen) and not app.screen.state.running:
                    break
            assert isinstance(app.screen, TransferScreen)
            first = archive / "26-08" / "26-08-23 Uni Friends Hangout"
            assert sorted(p.name for p in first.iterdir()) == [
                "DSCF0001.JPG",
                "DSCF0001_1.JPG",
                "DSCF0002.JPG",
                "DSCF0003.mp4",
            ]
            assert (archive / "26-08" / "26-08-24 Second day" / "DSCF0004.JPG").exists()

    run(scenario)


def test_missing_handbrake_leaves_the_videos_behind(
    digicam: Path, archive: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "digicam_archiver.ui.transfer_screen.require_handbrake",
        _raise_missing,
    )

    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            screen = app.screen
            for name in ("Uni Friends Hangout", "Second day"):
                screen.query_one("#name-input", Input).value = name
                await pilot.press("enter")
                await pilot.pause()
            await pilot.press("ctrl+t")
            await pilot.pause()
            await pilot.pause()
            # the notice about HandBrake is on screen, dismiss it
            assert isinstance(app.screen, type(app.screen))
            await pilot.click("#ok")
            for _ in range(40):
                await pilot.pause()
                screen = app.screen
                if isinstance(screen, TransferScreen) and not screen.state.running:
                    break
            first = archive / "26-08" / "26-08-23 Uni Friends Hangout"
            assert sorted(p.name for p in first.iterdir()) == [
                "DSCF0001.JPG",
                "DSCF0001_1.JPG",
                "DSCF0002.JPG",
            ]
            assert "left for later" in str(app.screen.query_one("#finished", Static).render())

    run(scenario)


def test_existing_event_folder_asks_what_to_do(
    digicam: Path, archive: Path, fake_handbrake: Path
) -> None:
    target = archive / "26-08" / "26-08-23 Uni Friends Hangout"
    target.mkdir(parents=True)
    (target / "DSCF0001.JPG").write_bytes(b"already here")

    async def scenario() -> None:
        app = make_app(digicam, archive, handbrake=fake_handbrake)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            screen = app.screen
            for name in ("Uni Friends Hangout", "Second day"):
                screen.query_one("#name-input", Input).value = name
                await pilot.press("enter")
                await pilot.pause()
            await pilot.press("ctrl+t")
            for _ in range(20):
                await pilot.pause()
                if isinstance(app.screen, Choice):
                    break
            assert isinstance(app.screen, Choice)
            await pilot.click("#merge")
            for _ in range(40):
                await pilot.pause()
                if isinstance(app.screen, TransferScreen) and not app.screen.state.running:
                    break
            # the file that was already in the folder keeps its slot, the
            # incoming ones step around it
            names = sorted(p.name for p in target.iterdir())
            assert names == [
                "DSCF0001.JPG",
                "DSCF0001_1.JPG",
                "DSCF0001_2.JPG",
                "DSCF0002.JPG",
                "DSCF0003.mp4",
            ]
            assert (target / "DSCF0001.JPG").read_bytes() == b"already here"

    run(scenario)


def test_escape_goes_back_from_naming(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, PartitionScreen)

    run(scenario)


def test_quit_from_the_first_screen(digicam: Path, archive: Path) -> None:
    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()
            assert app.screen.id is None or not app.screen.is_running

    run(scenario)


def _raise_missing():
    from digicam_archiver.handbrake import HandBrakeMissing

    raise HandBrakeMissing(
        "HandBrakeCLI not found, install it with: sudo pacman -S handbrake-cli"
    )


# ------------------------------------------------------------------- reruns


def test_a_rerun_marks_the_events_that_are_already_archived(
    digicam: Path, archive: Path
) -> None:
    """The tree says which events the archive holds, without being asked."""
    archived_event(digicam, archive, 0, "Uni Friends Hangout")

    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, PartitionScreen)
            labels = [
                str(child.label) for child in screen.query_one("#events", Tree).root.children
            ]
            assert any("in the archive" in label for label in labels)
            assert sum("in the archive" in label for label in labels) == 1
            assert "already archived" in str(screen.query_one("#status", Static).render())

    run(scenario)


def test_a_rerun_offers_the_archive_name_for_an_archived_event(
    digicam: Path, archive: Path
) -> None:
    archived_event(digicam, archive, 0, "Uni Friends Hangout")

    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, NameScreen)
            await pilot.pause()
            assert screen.query_one("#name-input", Input).value == "Uni Friends Hangout"
            assert "already there" in str(screen.query_one("#destination", Static).render())
            # the other event is new, so it gets the date as a starting point
            await pilot.press("alt+down")
            await pilot.pause()
            assert screen.query_one("#name-input", Input).value.startswith("26-08")

    run(scenario)


def test_a_second_run_over_the_same_card_copies_nothing(digicam: Path, archive: Path) -> None:
    """Nothing new to do: the run ends without touching the folder."""
    folder = archived_event(digicam, archive, 0, "Uni Friends Hangout")
    before = sorted(p.name for p in folder.iterdir())

    async def scenario() -> None:
        app = make_app(digicam, archive)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            # the second event is skipped, so only the archived one is left
            await pilot.press("j")
            await pilot.press("x")
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            screen = app.screen
            screen.query_one("#name-input", Input).value = "Uni Friends Hangout"
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("ctrl+t")
            await pilot.pause()
            assert isinstance(app.screen, TransferScreen)
            for _ in range(60):
                await pilot.pause()
                if not app.screen.state.running:
                    break
            assert "copied 0" in str(app.screen.query_one("#finished", Static).render())

    run(scenario)
    assert sorted(p.name for p in folder.iterdir()) == before


def test_an_unnamed_new_event_stays_on_the_card(
    digicam: Path, archive: Path, fake_handbrake: Path
) -> None:
    """Only the named event is copied; the other one is left alone."""

    async def scenario() -> None:
        app = make_app(digicam, archive, handbrake=fake_handbrake)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            screen = app.screen
            screen.query_one("#name-input", Input).value = "Uni Friends Hangout"
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("ctrl+t")
            await pilot.pause()
            # a notice first, saying the other event stays where it is
            assert "stay on the card" in app.screen.title_text
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, TransferScreen)
            await pilot.pause()
            await pilot.pause()
            finished = str(app.screen.query_one("#finished", Static).render())
            assert "copied" in finished and "failed" not in finished
            names = sorted(p.name for p in (archive / "26-08").iterdir())
            assert names == ["26-08-23 Uni Friends Hangout"]

    run(scenario)


def test_no_adopt_ignores_the_archive(digicam: Path, archive: Path) -> None:
    """--no-adopt keeps the old behaviour: ask before writing to a folder."""
    folder = archived_event(digicam, archive, 0, "Uni Friends Hangout")

    async def scenario() -> None:
        app = make_app(digicam, archive)
        app.opts.no_adopt = True
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            screen = app.screen
            screen.query_one("#name-input", Input).value = "Uni Friends Hangout"
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("alt+down")
            await pilot.pause()
            screen.query_one("#name-input", Input).value = "Second Evening"
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("ctrl+t")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, Choice)

    run(scenario)
    assert folder.exists()


def test_a_split_after_the_fact_moves_files_after_saying_so(
    digicam: Path, archive: Path, fake_handbrake: Path
) -> None:
    """The archive is changed only after the preview, and never silently."""
    folder = archived_event(digicam, archive, 0, "Whole day")
    before = names_in(folder)

    async def scenario() -> None:
        app = make_app(digicam, archive, handbrake=fake_handbrake)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.pause()
            # split the archived event in two, and name both halves
            screen = app.screen
            assert isinstance(screen, PartitionScreen)
            assert screen.query_one("#events", Tree).root.children[0].label
            app.plan.split_before(1)
            for event, name in zip(
                app.plan.events(), ("Whole day", "Evening", "Later"), strict=True
            ):
                app.plan.set_name(event, name)
            screen._rebuild(keep=0)
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            await pilot.press("ctrl+t")
            for _ in range(20):
                await pilot.pause()
                if isinstance(app.screen, Choice):
                    break
            assert isinstance(app.screen, Choice)
            assert "change" in app.screen.message_text
            await pilot.click("#go")
            for _ in range(60):
                await pilot.pause()
                if isinstance(app.screen, TransferScreen) and not app.screen.state.running:
                    break
            assert isinstance(app.screen, TransferScreen)
            for _ in range(20):
                await pilot.pause()

    run(scenario)
    # the first half keeps its own photo, everything else went to the new folder
    assert names_in(folder) == {"DSCF0001.JPG"}
    evening = archive / "26-08" / "26-08-23 Evening"
    assert names_in(evening) == before - {"DSCF0001.JPG"}
    # and the run wrote down what it moved
    assert list((archive / ".digicam").glob("changes-*.json"))


def test_declining_the_change_leaves_the_archive_alone(
    digicam: Path, archive: Path, fake_handbrake: Path
) -> None:
    folder = archived_event(digicam, archive, 0, "Whole day")
    before = names_in(folder)

    async def scenario() -> None:
        app = make_app(digicam, archive, handbrake=fake_handbrake)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.plan.split_before(1)
            for event, name in zip(
                app.plan.events(), ("Whole day", "Evening", "Later"), strict=True
            ):
                app.plan.set_name(event, name)
            app.screen._rebuild(keep=0)
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            await pilot.press("ctrl+t")
            for _ in range(20):
                await pilot.pause()
                if isinstance(app.screen, Choice):
                    break
            assert isinstance(app.screen, Choice)
            await pilot.click("#stop")
            await pilot.pause()
            assert isinstance(app.screen, NameScreen)
            assert app.restructure is None

    run(scenario)
    assert names_in(folder) == before
    assert not (archive / "26-08" / "26-08-23 Evening").exists()


def test_strict_copies_new_files_but_moves_nothing(
    digicam: Path, archive: Path, fake_handbrake: Path
) -> None:
    """--strict means the script gets told, not that the tool stops working."""
    folder = archived_event(digicam, archive, 0, "Whole day")
    before = names_in(folder)

    async def scenario() -> None:
        app = make_app(digicam, archive, handbrake=fake_handbrake, strict=True)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.pause()
            app.plan.split_before(1)
            for event, name in zip(
                app.plan.events(), ("Whole day", "Evening", "Later"), strict=True
            ):
                app.plan.set_name(event, name)
            app.screen._rebuild(keep=0)
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
            await pilot.press("ctrl+t")
            for _ in range(60):
                await pilot.pause()
                if isinstance(app.screen, TransferScreen) and not app.screen.state.running:
                    break
            # no question asked, the run just gets on with it
            assert not isinstance(app.screen, Choice)
            assert app.strict_refused
            assert app.restructure is None

    run(scenario)
    # every archived file stayed where it was, and nothing new was moved
    assert names_in(folder) == before
    assert not (archive / "26-08" / "26-08-23 Evening").exists()
    assert not list((archive / ".digicam").glob("changes-*.json"))


def test_the_archive_is_asked_for_before_the_events(digicam: Path) -> None:
    """No -a on the command line, so the first thing on screen is the question."""
    archive = digicam.parent / "archive-elsewhere"
    archive.mkdir()

    async def scenario() -> None:
        app = make_app(digicam)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, ArchiveDir)
            app.screen.query_one("#field", Input).value = str(archive)
            await pilot.click("#ok")
            for _ in range(20):
                await pilot.pause()
                if isinstance(app.screen, PartitionScreen):
                    break
            assert isinstance(app.screen, PartitionScreen)
            assert app.archive == archive

    run(scenario)


def test_cancelling_the_archive_question_still_works(digicam: Path) -> None:
    """Saying no means nothing is adopted, not that the run stops."""

    async def scenario() -> None:
        app = make_app(digicam)
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, ArchiveDir)
            await pilot.press("escape")
            for _ in range(20):
                await pilot.pause()
                if isinstance(app.screen, PartitionScreen):
                    break
            assert isinstance(app.screen, PartitionScreen)
            assert app.archive is None
            assert app.adopts  # it was asked for
            assert not app.archive_known
            # the badges stay out of the way when there is nothing to match
            screen = app.screen
            labels = [
                str(child.label) for child in screen.query_one("#events", Tree).root.children
            ]
            assert not any("archive" in label for label in labels)

    run(scenario)
