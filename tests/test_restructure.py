"""Moving archived files around when the plan no longer agrees with them."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from digicam_archiver.partition import Plan
from digicam_archiver.restructure import (
    apply,
    asks_for_a_decision,
    describe,
    plan_restructure,
)

from .test_reconcile import DAY_ONE, archive_event, card


def restructure(plan: Plan, archive: Path):
    from digicam_archiver.reconcile import build_index, reconcile

    index = build_index(archive, plan)
    return plan_restructure(plan, index, reconcile(plan, index), archive)


def names(folder: Path) -> set[str]:
    return {path.name for path in folder.iterdir()}


def test_a_rerun_with_the_same_partition_changes_nothing(tmp_path: Path) -> None:
    plan = card(tmp_path / "card", [("DSCF0001.JPG", DAY_ONE)])
    folder = archive_event(
        tmp_path / "a", "26-08-23", "Hangout", [item.path for item in plan.files]
    )
    plan.set_name(plan.events()[0], "Hangout")

    result = restructure(plan, tmp_path / "a")
    assert result.is_empty
    assert result.move_count == 0
    assert names(folder) == {"DSCF0001.JPG"}


def test_a_split_moves_the_later_photos_into_a_new_folder(tmp_path: Path) -> None:
    plan = card(
        tmp_path / "card",
        [
            ("DSCF0001.JPG", DAY_ONE),
            ("DSCF0002.JPG", DAY_ONE + timedelta(minutes=20)),
            ("DSCF0003.JPG", DAY_ONE + timedelta(minutes=40)),
        ],
    )
    folder = archive_event(
        tmp_path / "a", "26-08-23", "Whole day", [item.path for item in plan.files]
    )
    plan.set_name(plan.events()[0], "Whole day")
    plan.split_before(2)
    plan.set_name(plan.events()[1], "Evening")

    result = restructure(plan, tmp_path / "a")
    assert result.move_count == 1
    assert describe(result) == ("move DSCF0003.JPG: 26-08-23 Whole day -> 26-08-23 Evening")
    assert not result.blocked

    apply(result, tmp_path / "a", lambda _message: None)
    assert names(folder) == {"DSCF0001.JPG", "DSCF0002.JPG"}
    evening = tmp_path / "a" / "26-08" / "26-08-23 Evening"
    assert names(evening) == {"DSCF0003.JPG"}


def test_a_merge_moves_the_later_photos_back(tmp_path: Path) -> None:
    plan = card(
        tmp_path / "card",
        [("DSCF0001.JPG", DAY_ONE), ("DSCF0002.JPG", DAY_ONE + timedelta(hours=6))],
    )
    first = archive_event(tmp_path / "a", "26-08-23", "Afternoon", [plan.files[0].path])
    second = archive_event(tmp_path / "a", "26-08-23", "Evening", [plan.files[1].path])
    plan.set_name(plan.events()[0], "Afternoon")
    plan.merge_next(plan.events()[0])
    plan.set_name(plan.events()[0], "Afternoon")

    result = restructure(plan, tmp_path / "a")
    assert result.move_count == 1
    assert second in result.vacant

    apply(result, tmp_path / "a", lambda _message: None)
    assert names(first) == {"DSCF0001.JPG", "DSCF0002.JPG"}
    assert not second.exists()


def test_a_rename_is_a_rename_not_a_rebuild(tmp_path: Path) -> None:
    plan = card(tmp_path / "card", [("DSCF0001.JPG", DAY_ONE)])
    folder = archive_event(
        tmp_path / "a", "26-08-23", "Old name", [item.path for item in plan.files]
    )
    plan.set_name(plan.events()[0], "New name")

    result = restructure(plan, tmp_path / "a")
    assert result.move_count == 0
    assert [folder for folder in result.changes if folder.renamed]

    apply(result, tmp_path / "a", lambda _message: None)
    renamed = tmp_path / "a" / "26-08" / "26-08-23 New name"
    assert names(renamed) == {"DSCF0001.JPG"}
    assert not folder.exists()


def test_a_stranger_in_the_way_makes_it_ask_first(tmp_path: Path) -> None:
    """The user chose to stop and ask, not to guess."""
    plan = card(
        tmp_path / "card",
        [
            ("DSCF0001.JPG", DAY_ONE),
            ("DSCF0002.JPG", DAY_ONE + timedelta(minutes=20)),
            ("DSCF0003.JPG", DAY_ONE + timedelta(minutes=40)),
        ],
    )
    folder = archive_event(
        tmp_path / "a", "26-08-23", "Whole day", [item.path for item in plan.files]
    )
    (folder / "scan0001.jpg").write_bytes(b"put there by hand")
    plan.set_name(plan.events()[0], "Whole day")
    plan.split_before(2)
    plan.set_name(plan.events()[1], "Evening")

    result = restructure(plan, tmp_path / "a")
    assert asks_for_a_decision(result)
    assert result.blocked == (folder,)
    # the folder itself survives, it is still the first event's home
    assert folder not in result.vacant
    assert "hold on" in describe(result)

    # asking does not mean guessing: the move still only touches known files
    apply(result, tmp_path / "a", lambda _message: None)
    assert names(folder) == {"DSCF0001.JPG", "DSCF0002.JPG", "scan0001.jpg"}


def test_a_file_already_in_the_way_is_left_alone(tmp_path: Path) -> None:
    plan = card(
        tmp_path / "card",
        [
            ("DSCF0001.JPG", DAY_ONE),
            ("DSCF0002.JPG", DAY_ONE + timedelta(minutes=20)),
            ("DSCF0003.JPG", DAY_ONE + timedelta(minutes=40)),
        ],
    )
    archive_event(tmp_path / "a", "26-08-23", "Whole day", [item.path for item in plan.files])
    plan.set_name(plan.events()[0], "Whole day")
    plan.split_before(2)
    plan.set_name(plan.events()[1], "Evening")
    (tmp_path / "a" / "26-08" / "26-08-23 Evening").mkdir(parents=True)
    (tmp_path / "a" / "26-08" / "26-08-23 Evening" / "DSCF0003.JPG").write_bytes(b"other")

    result = restructure(plan, tmp_path / "a")
    assert result.move_count == 0
    assert result.conflicts
    assert "in the way" in describe(result)
    assert not result.is_empty


def test_the_run_leaves_a_note_of_what_moved(tmp_path: Path) -> None:
    plan = card(
        tmp_path / "card",
        [("DSCF0001.JPG", DAY_ONE), ("DSCF0002.JPG", DAY_ONE + timedelta(hours=6))],
    )
    archive_event(tmp_path / "a", "26-08-23", "Afternoon", [plan.files[0].path])
    second = archive_event(tmp_path / "a", "26-08-23", "Evening", [plan.files[1].path])
    plan.set_name(plan.events()[0], "Afternoon")
    plan.merge_next(plan.events()[0])
    plan.set_name(plan.events()[0], "Afternoon")

    lines: list[str] = []
    apply(restructure(plan, tmp_path / "a"), tmp_path / "a", lines.append)
    written = list((tmp_path / "a" / ".digicam").glob("changes-*.json"))
    assert len(written) == 1
    note = json.loads(written[0].read_text())
    assert note["moved"] == [
        {
            "from": str(second / "DSCF0002.JPG"),
            "to": str(tmp_path / "a" / "26-08" / "26-08-23 Afternoon" / "DSCF0002.JPG"),
            "name": "DSCF0002.JPG",
        }
    ]
    assert any("wrote down" in line for line in lines)


def test_an_untouched_archive_is_not_written_to(tmp_path: Path) -> None:
    """No changes, no journal, nothing to undo."""
    plan = card(tmp_path / "card", [("DSCF0001.JPG", DAY_ONE)])
    archive_event(tmp_path / "a", "26-08-23", "Hangout", [item.path for item in plan.files])
    plan.set_name(plan.events()[0], "Hangout")

    apply(restructure(plan, tmp_path / "a"), tmp_path / "a", lambda _m: None)
    assert not (tmp_path / "a" / ".digicam").exists()


def test_a_folder_the_card_does_not_touch_is_never_removed(tmp_path: Path) -> None:
    """An archive folder with nothing to do with this run stays as it is."""
    plan = card(tmp_path / "card", [("DSCF0001.JPG", DAY_ONE)])
    archive_event(tmp_path / "a", "26-08-23", "Hangout", [item.path for item in plan.files])
    other = archive_event(tmp_path / "a", "25-01-02", "Some other day", [plan.files[0].path])
    (other / "notes.txt").write_text("from another card")
    plan.set_name(plan.events()[0], "Hangout")

    result = restructure(plan, tmp_path / "a")
    assert other not in result.vacant
    apply(result, tmp_path / "a", lambda _message: None)
    assert names(other) == {"DSCF0001.JPG", "notes.txt"}


def test_a_card_photo_with_no_home_yet_is_not_in_the_archive(tmp_path: Path) -> None:
    """Nothing to move: the card photo is simply not there yet."""
    plan = card(tmp_path / "card", [("DSCF0001.JPG", DAY_ONE)])
    folder = archive_event(
        tmp_path / "a", "26-08-23", "Hangout", [item.path for item in plan.files]
    )
    plan.set_name(plan.events()[0], "Hangout")

    result = restructure(plan, tmp_path / "a")
    assert result.move_count == 0
    assert names(folder) == {"DSCF0001.JPG"}
    assert not (tmp_path / "a" / ".digicam").exists()
    assert datetime.now().year == 2026
