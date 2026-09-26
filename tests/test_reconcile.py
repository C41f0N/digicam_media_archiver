"""Recognising what the archive already has, and what it would take to change it.

The interesting cases are all about a card that has been run before, so these
tests build a real archive with real jpegs and read it back with the real
index.  Nothing here writes to the archive: :func:`reconcile` only answers.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from digicam_archiver.model import MediaFile
from digicam_archiver.partition import Plan
from digicam_archiver.reconcile import (
    ArchiveIndex,
    MatchKind,
    build_index,
    parse_folder_name,
    reconcile,
)
from digicam_archiver.scan import scan
from digicam_archiver.transfer import copy_photo

from .conftest import write_photo

DAY_ONE = datetime(2026, 8, 23, 14, 2)
DAY_TWO = datetime(2026, 8, 24, 10, 0)
LATER = datetime(2026, 8, 23, 19, 0)


def card(root: Path, shots: list[tuple[str, datetime]]) -> Plan:
    """A card with the given photos, and a plan of them.

    Each photo gets a colour of its own, so two of them are never the same file
    unless the test means them to be.
    """
    for number, (name, taken) in enumerate(shots):
        write_photo(
            root / "DCIM" / "103_FUJI" / name,
            taken,
            colour=(number * 17 % 250, taken.minute, 30),
        )
    return Plan(source=root, files=scan(root))


def archive_event(archive: Path, date: str, name: str, sources: list[Path]) -> Path:
    """Put copies of ``sources`` in the archive, the way a transfer would.

    ``date`` is the event's own date, ``26-08-23``, which is also the month
    folder it is filed under.
    """
    folder = archive / date[:5] / f"{date} {name}"
    folder.mkdir(parents=True, exist_ok=True)
    for source in sources:
        copy_photo(source, folder / source.name)
    return folder


def plan_of(root: Path) -> Plan:
    return Plan(source=root, files=scan(root))


# ------------------------------------------------------------------ indexing


def test_parse_folder_name() -> None:
    assert parse_folder_name(Path("26-08-23 Uni Friends Hangout")) == (
        "26-08-23",
        "Uni Friends Hangout",
    )


def test_parse_folder_name_without_a_date() -> None:
    assert parse_folder_name(Path("Holiday snaps")) == ("", "Holiday snaps")


def test_index_of_an_archive_that_is_not_there(tmp_path: Path) -> None:
    plan = plan_of(tmp_path)
    assert build_index(tmp_path / "nowhere", plan).folders == ()


def test_index_reads_every_month_and_event(tmp_path: Path) -> None:
    plan = card(tmp_path / "card", [("DSCF0001.JPG", DAY_ONE)])
    archive_event(tmp_path / "a", "26-08-23", "One", [plan.files[0].path])
    archive_event(tmp_path / "a", "26-08-24", "Two", [plan.files[0].path])
    (tmp_path / "a" / "not a month").mkdir()
    (tmp_path / "a" / "26-08" / "26-08-25 Empty").mkdir()

    index = build_index(tmp_path / "a", plan)
    assert [folder.name for folder in index.folders] == ["One", "Two"]
    assert index.of_date("26-08-24")[0].path.name == "26-08-24 Two"
    assert index.by_path(tmp_path / "a" / "26-08" / "26-08-24 Two") is not None


def test_index_reports_progress(tmp_path: Path) -> None:
    plan = card(tmp_path / "card", [("DSCF0001.JPG", DAY_ONE)])
    archive_event(tmp_path / "a", "26-08-23", "One", [plan.files[0].path])
    archive_event(tmp_path / "a", "26-08-23", "Two", [plan.files[0].path])
    seen: list[tuple[int, int]] = []
    build_index(tmp_path / "a", plan, lambda done, total: seen.append((done, total)))
    assert seen == [(1, 2), (2, 2)]


def test_index_ignores_files_that_are_not_media(tmp_path: Path) -> None:
    plan = card(tmp_path / "card", [("DSCF0001.JPG", DAY_ONE)])
    folder = archive_event(tmp_path / "a", "26-08-23", "One", [plan.files[0].path])
    (folder / "notes.txt").write_text("a note")
    (folder / "DSCF0002.JPG.part").write_bytes(b"half")
    assert build_index(tmp_path / "a", plan).folders[0].files.names() == {"DSCF0001.JPG"}


# ------------------------------------------------------------------ matching


def test_a_fresh_card_matches_nothing(tmp_path: Path) -> None:
    plan = card(tmp_path / "card", [("DSCF0001.JPG", DAY_ONE), ("DSCF0002.JPG", LATER)])
    result = reconcile(plan, build_index(tmp_path / "a", plan))
    assert {item.kind for item in result.events.values()} == {MatchKind.NEW}
    assert result.new
    assert not result.archived


def test_a_rerun_matches_the_folder_and_adopts_its_name(tmp_path: Path) -> None:
    plan = card(
        tmp_path / "card",
        [("DSCF0001.JPG", DAY_ONE), ("DSCF0002.JPG", DAY_ONE + timedelta(minutes=20))],
    )
    folder = archive_event(
        tmp_path / "a", "26-08-23", "Uni Friends Hangout", [f.path for f in plan.files]
    )

    result = reconcile(plan, build_index(tmp_path / "a", plan))
    event = result.events[plan.events()[0].start]
    assert event.kind is MatchKind.MATCHED
    assert event.home == folder
    assert event.name == "Uni Friends Hangout"
    assert event.complete
    assert sorted(c.name for c in event.present.values()) == [
        "DSCF0001.JPG",
        "DSCF0002.JPG",
    ]
    assert all(c.folder == folder for c in event.present.values())
    assert not result.strangers


def test_a_partly_copied_event_is_partial(tmp_path: Path) -> None:
    plan = card(
        tmp_path / "card",
        [
            ("DSCF0001.JPG", DAY_ONE),
            ("DSCF0002.JPG", DAY_ONE + timedelta(minutes=20)),
            ("DSCF0003.JPG", DAY_ONE + timedelta(minutes=40)),
        ],
    )
    archive_event(
        tmp_path / "a",
        "26-08-23",
        "Hangout",
        [plan.files[0].path, plan.files[1].path],
    )
    event = reconcile(plan, build_index(tmp_path / "a", plan)).events[0]
    assert event.kind is MatchKind.PARTIAL
    assert event.name == "Hangout"
    assert [item.name for item in event.missing] == ["DSCF0003.JPG"]


def test_a_renamed_folder_is_adopted_as_it_stands(tmp_path: Path) -> None:
    plan = card(tmp_path / "card", [("DSCF0001.JPG", DAY_ONE)])
    folder = archive_event(tmp_path / "a", "26-08-23", "Kaisu Dinner", [plan.files[0].path])
    # a person fixed the spelling in a file browser, with the archive closed
    renamed = folder.parent / "26-08-23 Kaisu Dinner at home"
    folder.rename(renamed)

    event = reconcile(plan, build_index(tmp_path / "a", plan)).events[0]
    assert event.name == "Kaisu Dinner at home"
    assert event.home == renamed


def test_a_split_event_gives_the_folder_to_the_earlier_half(tmp_path: Path) -> None:
    """The archive has one folder; the plan has two events inside it."""
    plan = card(
        tmp_path / "card",
        [
            ("DSCF0001.JPG", DAY_ONE),
            ("DSCF0002.JPG", DAY_ONE + timedelta(minutes=20)),
            ("DSCF0003.JPG", DAY_ONE + timedelta(minutes=40)),
        ],
    )
    folder = archive_event(
        tmp_path / "a", "26-08-23", "Long day", [item.path for item in plan.files]
    )
    plan.split_before(2)  # a split the gap missed

    result = reconcile(plan, build_index(tmp_path / "a", plan))
    first, second = plan.events()
    earlier, later_half = result.events[first.start], result.events[second.start]

    assert earlier.kind is MatchKind.SPLIT
    assert earlier.home == folder
    assert earlier.name == "Long day"
    assert earlier.sharers == (second.start,)
    assert sorted(c.name for c in earlier.present.values()) == [
        "DSCF0001.JPG",
        "DSCF0002.JPG",
    ]

    assert later_half.kind is MatchKind.SPLIT
    assert later_half.home is None
    assert later_half.needs_a_name()
    assert later_half.present[plan.files[2].path].name == "DSCF0003.JPG"
    assert result.contested == frozenset({folder})


def test_a_merged_event_keeps_the_folder_of_its_date(tmp_path: Path) -> None:
    """The archive has two folders; the plan has one event spanning both."""
    plan = card(
        tmp_path / "card",
        [("DSCF0001.JPG", DAY_ONE), ("DSCF0002.JPG", DAY_TWO)],
    )
    first = archive_event(tmp_path / "a", "26-08-23", "Afternoon", [plan.files[0].path])
    archive_event(tmp_path / "a", "26-08-24", "Next morning", [plan.files[1].path])
    plan.merge_next(plan.events()[0])  # the user decided it was one outing

    event = reconcile(plan, build_index(tmp_path / "a", plan)).events[0]
    assert event.kind is MatchKind.MERGE
    assert event.home == first
    assert event.name == "Afternoon"
    assert event.donors == (first.parent / "26-08-24 Next morning",)
    assert event.complete


def test_a_merge_prefers_the_folder_filed_under_the_events_own_date(
    tmp_path: Path,
) -> None:
    plan = card(
        tmp_path / "card",
        [("DSCF0001.JPG", DAY_ONE), ("DSCF0002.JPG", DAY_ONE + timedelta(hours=1))],
    )
    archive_event(tmp_path / "a", "26-08-24", "Later", [plan.files[1].path])
    right = archive_event(tmp_path / "a", "26-08-23", "Actual", [plan.files[0].path])
    plan.merge_next(plan.events()[0])

    event = reconcile(plan, build_index(tmp_path / "a", plan)).events[0]
    assert event.home == right
    assert event.name == "Actual"


def test_a_video_is_matched_by_its_converted_name(tmp_path: Path, ffmpeg: str) -> None:
    from .conftest import write_video

    root = tmp_path / "card"
    write_photo(root / "DCIM" / "103_FUJI" / "DSCF0001.JPG", DAY_ONE)
    write_video(ffmpeg, root / "DCIM" / "103_FUJI" / "DSCF0002.AVI", seconds=0.3)
    os.utime(root / "DCIM" / "103_FUJI" / "DSCF0002.AVI", (DAY_ONE.timestamp(),) * 2)
    plan = Plan(source=root, files=scan(root))
    folder = archive_event(tmp_path / "a", "26-08-23", "Clip", [plan.files[0].path])
    (folder / "DSCF0002.mp4").write_bytes(b"an encoded movie")

    event = reconcile(plan, build_index(tmp_path / "a", plan)).events[0]
    assert event.kind is MatchKind.MATCHED
    claim = event.present[plan.files[1].path]
    assert claim.name == "DSCF0002.mp4"
    assert claim.path.exists()


def test_files_nobody_explains_are_reported_as_strangers(tmp_path: Path) -> None:
    plan = card(tmp_path / "card", [("DSCF0001.JPG", DAY_ONE)])
    folder = archive_event(tmp_path / "a", "26-08-23", "Hangout", [plan.files[0].path])
    (folder / "scan0001.jpg").write_bytes(b"put there by hand")

    result = reconcile(plan, build_index(tmp_path / "a", plan))
    event = result.events[0]
    assert event.kind is MatchKind.MATCHED
    assert event.strangers == ("scan0001.jpg",)
    assert result.strangers == frozenset({folder})


def test_a_stranger_in_a_folder_being_divided_makes_it_ambiguous(tmp_path: Path) -> None:
    """A merge that would move files out of a folder with a stranger in it."""
    plan = card(
        tmp_path / "card",
        [("DSCF0001.JPG", DAY_ONE), ("DSCF0002.JPG", DAY_ONE + timedelta(hours=6))],
    )
    folder = archive_event(tmp_path / "a", "26-08-23", "Afternoon", [plan.files[0].path])
    (folder / "scan0001.jpg").write_bytes(b"put there by hand")
    archive_event(tmp_path / "a", "26-08-23", "Evening", [plan.files[1].path])
    plan.merge_next(plan.events()[0])

    event = reconcile(plan, build_index(tmp_path / "a", plan)).events[0]
    assert event.kind is MatchKind.AMBIGUOUS
    assert event.donors == (folder.parent / "26-08-23 Evening",)
    assert event.strangers == ("scan0001.jpg",)


def test_a_photo_whose_content_changed_is_not_the_same_photo(tmp_path: Path) -> None:
    """Same name, same second, different shot: the head hash has to notice."""
    plan = card(tmp_path / "card", [("DSCF0001.JPG", DAY_ONE)])
    folder = tmp_path / "a" / "26-08" / "26-08-23 Hangout"
    folder.mkdir(parents=True)
    other = write_photo(folder / "DSCF0001.JPG", DAY_ONE, colour=(9, 200, 200))
    os.utime(other, (DAY_ONE.timestamp(), DAY_ONE.timestamp()))

    event = reconcile(plan, build_index(tmp_path / "a", plan)).events[0]
    assert event.kind is MatchKind.NEW
    assert event.home is None


def test_reconciling_never_touches_the_archive(tmp_path: Path) -> None:
    plan = card(tmp_path / "card", [("DSCF0001.JPG", DAY_ONE)])
    folder = archive_event(tmp_path / "a", "26-08-23", "Hangout", [plan.files[0].path])
    before = {path: path.stat().st_mtime_ns for path in folder.iterdir()}

    reconcile(plan, build_index(tmp_path / "a", plan))
    assert {path: path.stat().st_mtime_ns for path in folder.iterdir()} == before


def test_reconciling_twice_gives_the_same_answer(tmp_path: Path) -> None:
    plan = card(
        tmp_path / "card",
        [("DSCF0001.JPG", DAY_ONE), ("DSCF0002.JPG", LATER), ("DSCF0003.JPG", DAY_TWO)],
    )
    archive_event(tmp_path / "a", "26-08-23", "Long day", [item.path for item in plan.files])
    index = build_index(tmp_path / "a", plan)
    plan.split_before(2)
    assert reconcile(plan, index).lines() == reconcile(plan, index).lines()


def test_lines_read_as_a_summary(tmp_path: Path) -> None:
    plan = card(tmp_path / "card", [("DSCF0001.JPG", DAY_ONE), ("DSCF0002.JPG", DAY_TWO)])
    archive_event(tmp_path / "a", "26-08-23", "Hangout", [plan.files[0].path])
    lines = reconcile(plan, build_index(tmp_path / "a", plan)).lines()
    assert "matched" in lines[0] and "26-08-23 Hangout" in lines[0]
    assert "new" in lines[1] and "not in the archive" in lines[1]


def test_an_empty_index_makes_everything_new(tmp_path: Path) -> None:
    plan = card(tmp_path / "card", [("DSCF0001.JPG", DAY_ONE)])
    result = reconcile(plan, ArchiveIndex())
    assert result.events[0].kind is MatchKind.NEW


def test_two_card_folders_with_the_same_name_both_land_in_one_event(
    tmp_path: Path,
) -> None:
    root = tmp_path / "card"
    for folder in ("103_FUJI", "104_FUJI"):
        write_photo(root / "DCIM" / folder / "DSCF0001.JPG", DAY_ONE, colour=(7, 7, 7))
    plan = Plan(source=root, files=scan(root))
    assert len(plan.files) == 2

    archive_event(tmp_path / "a", "26-08-23", "Same", [plan.files[0].path])
    event = reconcile(plan, build_index(tmp_path / "a", plan)).events[0]
    # byte identical photos are the same media, so the second one is a copy
    assert event.kind is MatchKind.PARTIAL
    assert len(event.present) == 1
    assert len(event.missing) == 1


@pytest.mark.parametrize("gap", [timedelta(hours=4), timedelta(hours=8)])
def test_the_gap_does_not_change_what_is_recognised(tmp_path: Path, gap) -> None:
    plan = card(
        tmp_path / "card",
        [("DSCF0001.JPG", DAY_ONE), ("DSCF0002.JPG", DAY_TWO)],
    )
    archive_event(tmp_path / "a", "26-08-23", "Hangout", [plan.files[0].path])
    plan.set_gap(gap)
    event = reconcile(plan, build_index(tmp_path / "a", plan)).events[0]
    assert event.name == "Hangout"


def test_an_excluded_event_is_left_out_of_the_answer(tmp_path: Path) -> None:
    """A skipped event must not claim anything, or a folder would look divided."""
    plan = card(
        tmp_path / "card",
        [("DSCF0001.JPG", DAY_ONE), ("DSCF0002.JPG", DAY_ONE + timedelta(hours=6))],
    )
    archive_event(tmp_path / "a", "26-08-23", "Hangout", [plan.files[0].path])
    archive_event(tmp_path / "a", "26-08-23", "Other", [plan.files[1].path])
    first, second = plan.events()
    plan.toggle_excluded(first)

    result = reconcile(plan, build_index(tmp_path / "a", plan))
    assert first.start not in result.events
    assert result.of(first).kind is MatchKind.NEW
    assert result.of(second).name == "Other"


def test_a_file_that_is_no_longer_on_the_card_is_a_stranger(tmp_path: Path) -> None:
    plan = card(tmp_path / "card", [("DSCF0001.JPG", DAY_ONE)])
    folder = archive_event(tmp_path / "a", "26-08-23", "Hangout", [plan.files[0].path])
    gone = write_photo(folder / "DSCF0009.JPG", DAY_ONE, colour=(1, 2, 3))
    os.utime(gone, (DAY_ONE.timestamp(), DAY_ONE.timestamp()))
    assert reconcile(plan, build_index(tmp_path / "a", plan)).events[0].strangers == (
        "DSCF0009.JPG",
    )


def test_a_mediafile_helper_is_not_needed(tmp_path: Path) -> None:
    """The plan is the only input, so a plan of nothing is still a valid answer."""
    empty = Plan(source=tmp_path, files=[])
    assert reconcile(empty, ArchiveIndex()).events == {}


def test_reports_the_number_of_files_an_event_is_missing(tmp_path: Path) -> None:
    plan = card(
        tmp_path / "card",
        [("DSCF0001.JPG", DAY_ONE), ("DSCF0002.JPG", DAY_ONE + timedelta(minutes=5))],
    )
    archive_event(tmp_path / "a", "26-08-23", "Hangout", [plan.files[0].path])
    event = reconcile(plan, build_index(tmp_path / "a", plan)).events[0]
    assert isinstance(event.missing, tuple)
    assert all(isinstance(item, MediaFile) for item in event.missing)
