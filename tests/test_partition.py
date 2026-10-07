from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from digicam_archiver.model import MediaFile
from digicam_archiver.partition import DEFAULT_GAP, Plan, build_events


def make_files(*stamps: datetime) -> list[MediaFile]:
    return [
        MediaFile(
            path=Path(f"DCIM/100MEDIA/DSCF{index:04d}.JPG"),
            kind="photo",
            taken=stamp,
            size=1000 + index,
            stamp_source="exif",
        )
        for index, stamp in enumerate(stamps, start=1)
    ]


@pytest.fixture
def files() -> list[MediaFile]:
    """Three shots over four hours, then the next morning."""
    return make_files(
        datetime(2026, 8, 23, 14, 0),
        datetime(2026, 8, 23, 14, 30),
        datetime(2026, 8, 23, 18, 0),
        datetime(2026, 8, 24, 9, 0),
    )


def test_no_files_no_events() -> None:
    assert build_events([], DEFAULT_GAP) == []


def test_one_file_one_event(files) -> None:
    assert len(build_events(files[:1], DEFAULT_GAP)) == 1


def test_silence_longer_than_gap_splits(files) -> None:
    events = build_events(files, DEFAULT_GAP)
    assert [event.start for event in events] == [0, 3]
    assert [len(event.files) for event in events] == [3, 1]


def test_gap_exactly_at_the_limit_stays_together() -> None:
    files = make_files(datetime(2026, 8, 23, 14, 0), datetime(2026, 8, 23, 18, 0))
    assert len(build_events(files, DEFAULT_GAP)) == 1


def test_a_minute_past_the_limit_splits() -> None:
    files = make_files(datetime(2026, 8, 23, 14, 0), datetime(2026, 8, 23, 18, 1))
    assert len(build_events(files, DEFAULT_GAP)) == 2


def test_forced_start_wins_over_a_short_gap() -> None:
    files = make_files(datetime(2026, 8, 23, 14, 0), datetime(2026, 8, 23, 14, 1))
    events = build_events(files, DEFAULT_GAP, forced_starts={1})
    assert [event.start for event in events] == [0, 1]


def test_suppressed_split_beats_a_long_gap() -> None:
    files = make_files(datetime(2026, 8, 23, 14, 0), datetime(2026, 8, 25, 14, 0))
    events = build_events(files, DEFAULT_GAP, suppressed_splits={1})
    assert [event.start for event in events] == [0]


def test_event_spanning_midnight_stays_one_event() -> None:
    files = make_files(datetime(2026, 8, 23, 23, 30), datetime(2026, 8, 24, 1, 0))
    events = build_events(files, DEFAULT_GAP)
    assert len(events) == 1
    assert events[0].taken_start.date() != events[0].taken_end.date()


# ------------------------------------------------------------------- Plan


@pytest.fixture
def plan(files) -> Plan:
    return Plan(source=Path("/card"), files=files, gap=DEFAULT_GAP)


def test_plan_events(plan: Plan) -> None:
    assert [event.start for event in plan.events()] == [0, 3]


def test_split_before(plan: Plan, files) -> None:
    assert plan.split_before(2)
    assert [event.start for event in plan.events()] == [0, 2, 3]
    assert plan.files[2] is files[2]


def test_split_before_cannot_touch_the_first_file(plan: Plan) -> None:
    assert not plan.split_before(0)
    assert not plan.split_before(len(plan.files))
    assert not plan.split_before(-1)


def test_split_before_an_already_split_index_is_harmless(plan: Plan) -> None:
    assert plan.split_before(3)
    assert plan.split_before(3)
    assert [event.start for event in plan.events()] == [0, 3]


def test_merge_next(plan: Plan) -> None:
    first = plan.events()[0]
    assert plan.merge_next(first)
    assert [event.start for event in plan.events()] == [0]


def test_merge_next_on_the_last_event_does_nothing(plan: Plan) -> None:
    last = plan.events()[-1]
    assert not plan.merge_next(last)
    assert len(plan.events()) == 2


def test_merge_undoes_a_manual_split(plan: Plan) -> None:
    plan.split_before(2)
    merged = next(event for event in plan.events() if event.start == 0)
    assert plan.merge_next(merged)
    assert [event.start for event in plan.events()] == [0, 3]


def test_merge_drops_the_name_of_the_swallowed_event(plan: Plan) -> None:
    plan.split_before(2)
    second = next(event for event in plan.events() if event.start == 2)
    plan.set_name(second, "Swallowed")
    first = next(event for event in plan.events() if event.start == 0)
    plan.merge_next(first)
    assert plan.name_of(plan.events()[0]) is None
    assert 2 not in plan.names


def test_exclude_and_include(plan: Plan) -> None:
    first = plan.events()[0]
    assert plan.toggle_excluded(first) is True
    assert plan.is_excluded(first)
    assert [event.start for event in plan.included()] == [3]
    assert plan.toggle_excluded(first) is False
    assert [event.start for event in plan.included()] == [0, 3]


def test_toggle_above_skips_every_event_before_the_cursor(plan: Plan) -> None:
    plan.split_before(2)  # events starting at 0, 2 and 3
    cursor = plan.events()[-1]
    assert plan.toggle_above(cursor) == 2
    assert [event.start for event in plan.included()] == [cursor.start]
    assert [event.start for event in plan.events() if plan.is_excluded(event)] == [0, 2]


def test_toggle_above_is_a_toggle(plan: Plan) -> None:
    plan.split_before(2)
    cursor = plan.events()[-1]
    plan.toggle_above(cursor)
    assert plan.toggle_above(cursor) == 0
    assert [event.start for event in plan.included()] == [0, 2, 3]


def test_toggle_above_on_the_first_event_is_a_noop(plan: Plan) -> None:
    first = plan.events()[0]
    assert plan.toggle_above(first) == 0
    assert not plan.is_excluded(first)
    assert [event.start for event in plan.included()] == [0, 3]


def test_toggle_above_flips_events_one_by_one(plan: Plan) -> None:
    plan.split_before(2)
    events = plan.events()
    plan.toggle_excluded(events[0])  # the first one was skipped by hand
    cursor = events[-1]
    plan.toggle_above(cursor)  # flips both: first comes back, middle gets skipped
    assert [event.start for event in plan.events() if plan.is_excluded(event)] == [2]
    plan.toggle_above(cursor)  # and back the other way
    assert [event.start for event in plan.events() if plan.is_excluded(event)] == [0]


def test_changing_the_gap_keeps_manual_splits(plan: Plan) -> None:
    plan.split_before(2)
    plan.set_gap(timedelta(minutes=20))
    starts = [event.start for event in plan.events()]
    assert starts == [0, 1, 2, 3]


def test_changing_the_gap_keeps_manual_merges(plan: Plan) -> None:
    plan.merge_next(plan.events()[0])
    plan.set_gap(timedelta(minutes=10))
    starts = [event.start for event in plan.events()]
    assert starts == [0, 1, 2]
    assert 3 not in starts


def test_set_gap_has_a_floor(plan: Plan) -> None:
    plan.set_gap(timedelta(minutes=1))
    assert plan.gap == timedelta(minutes=5)


def test_auto_rerun_clears_manual_calls(plan: Plan) -> None:
    plan.split_before(2)
    plan.merge_next(plan.events()[0])
    plan.forced_starts.clear()
    plan.suppressed_splits.clear()
    assert [event.start for event in plan.events()] == [0, 3]


def test_names_survive_a_gap_change(plan: Plan) -> None:
    first = plan.events()[0]
    plan.set_name(first, "Uni Friends")
    plan.set_gap(timedelta(hours=8))
    assert plan.name_of(plan.events()[0]) == "Uni Friends"


def test_plan_lines_describe_every_file(plan: Plan) -> None:
    lines = plan.lines()
    assert lines[0].strip().startswith("1.")
    assert any("DSCF0001.JPG" in line for line in lines)
    assert any("exif" in line for line in lines)


def test_index_of_file(plan: Plan, files) -> None:
    assert plan.index_of_file(files[2].path) == 2
