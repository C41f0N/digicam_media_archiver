from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path

import pytest

from digicam_archiver.cli import (
    build_parser,
    check_source,
    main,
    parse_gap,
    print_plan,
    print_rerun,
)
from digicam_archiver.layout import build_items, event_dir
from digicam_archiver.partition import Plan
from digicam_archiver.scan import scan
from digicam_archiver.transfer import copy_photo


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("4h", timedelta(hours=4)),
        ("90m", timedelta(minutes=90)),
        ("1h30m", timedelta(hours=1, minutes=30)),
        ("45", timedelta(minutes=45)),
        ("2H", timedelta(hours=2)),
        (" 3h ", timedelta(hours=3)),
    ],
)
def test_parse_gap(raw: str, expected: timedelta) -> None:
    assert parse_gap(raw) == expected


@pytest.mark.parametrize("raw", ["", "abc", "4x", "h", "-"])
def test_parse_gap_rejects_junk(raw: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        parse_gap(raw)


def test_parser_defaults() -> None:
    args = build_parser().parse_args(["/card"])
    assert args.gap == timedelta(hours=4)
    assert args.preset == "Fast 720p30"
    assert args.archive is None
    assert args.faststart is False


def test_parser_reads_the_flags() -> None:
    args = build_parser().parse_args(
        [
            "/card",
            "-a",
            "/archive",
            "-g",
            "2h",
            "-p",
            "HQ 720p30 Surround",
            "--faststart",
            "--ascii",
        ]
    )
    assert args.archive == Path("/archive")
    assert args.gap == timedelta(hours=2)
    assert args.preset == "HQ 720p30 Surround"
    assert args.faststart is True
    assert args.ascii is True


def test_check_source_rejects_a_missing_folder(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        check_source(tmp_path / "nope")


def test_check_source_rejects_a_file(tmp_path: Path) -> None:
    path = tmp_path / "card"
    path.write_text("not a folder")
    with pytest.raises(SystemExit):
        check_source(path)


def test_check_source_rejects_an_empty_folder(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as info:
        check_source(tmp_path)
    assert "no JPG or AVI" in str(info.value)


def test_check_source_finds_the_media(digicam: Path) -> None:
    assert len(check_source(digicam)) == 5


def test_print_plan(digicam: Path, capsys) -> None:
    plan = Plan(source=digicam, files=scan(digicam))
    print_plan(plan)
    out = capsys.readouterr().out
    assert "2 events" in out
    assert "26-08-23 14:02" in out
    assert "DSCF0003.AVI" in out


def test_main_print_plan(digicam: Path, capsys) -> None:
    assert main([str(digicam), "--print-plan"]) == 0
    out = capsys.readouterr().out
    assert "5 files from" in out
    assert "4 photos" in out and "1 videos" in out


def test_main_print_plan_honours_the_gap(digicam: Path, capsys) -> None:
    assert main([str(digicam), "--print-plan", "-g", "30m"]) == 0
    out = capsys.readouterr().out
    # a 30 minute gap cuts the afternoon in two
    assert "3 events" in out


def test_main_on_a_missing_source(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main([str(tmp_path / "nope"), "--print-plan"])


def test_help_mentions_the_keys(capsys) -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--help"])
    out = capsys.readouterr().out
    assert "split before the highlighted file" in out
    assert "26-08-23" in out


# --------------------------------------------------------------------- reruns


def test_print_plan_says_what_the_archive_already_has(
    digicam: Path, archive: Path, capsys
) -> None:
    plan = Plan(source=digicam, files=scan(digicam))
    folder = event_dir(archive, plan.events()[0].taken_start, "Uni Friends Hangout")
    folder.mkdir(parents=True)
    for item in build_items(folder, plan.events()[0].files):
        if not item.is_video:
            copy_photo(item.source, item.dest)

    print_rerun(plan, archive)
    out = capsys.readouterr().out
    assert "1 event folder(s)" in out
    assert "partial 26-08-23 Uni Friends Hangout" in out
    assert "3 of 4 there" in out
    assert "the archive would stay as it is" in out


def test_print_plan_says_what_a_split_would_move(digicam: Path, archive: Path, capsys) -> None:
    plan = Plan(source=digicam, files=scan(digicam))
    folder = event_dir(archive, plan.events()[0].taken_start, "Whole day")
    folder.mkdir(parents=True)
    for item in build_items(folder, plan.events()[0].files):
        if not item.is_video:
            copy_photo(item.source, item.dest)
    plan.split_before(2)
    for event, name in zip(plan.events(), ("Whole day", "Evening", "Later"), strict=True):
        plan.set_name(event, name)

    concerns = print_rerun(plan, archive)
    out = capsys.readouterr().out
    assert concerns == 1
    assert "the archive would change" in out
    assert "move DSCF0001_1.JPG: 26-08-23 Whole day -> 26-08-23 Evening" in out


def test_strict_turns_a_rerun_question_into_an_error(
    digicam: Path, archive: Path, capsys
) -> None:
    plan = Plan(source=digicam, files=scan(digicam))
    folder = event_dir(archive, plan.events()[0].taken_start, "Whole day")
    folder.mkdir(parents=True)
    for item in build_items(folder, plan.events()[0].files):
        if not item.is_video:
            copy_photo(item.source, item.dest)

    assert main([str(digicam), "--print-plan", "-a", str(archive)]) == 0
    assert "the archive would stay as it is" in capsys.readouterr().out
    assert main([str(digicam), "--print-plan", "-a", str(archive), "--strict"]) == 0


def test_strict_stops_when_the_archive_would_change(
    digicam: Path, archive: Path, capsys
) -> None:
    plan = Plan(source=digicam, files=scan(digicam))
    folder = event_dir(archive, plan.events()[0].taken_start, "Whole day")
    folder.mkdir(parents=True)
    (folder / "scan0001.jpg").write_bytes(b"put there by hand")
    copy_photo(plan.events()[0].files[0].path, folder / "DSCF0001.JPG")

    code = main([str(digicam), "--print-plan", "-a", str(archive), "--strict"])
    assert code == 2
    assert "strict" in capsys.readouterr().err


def test_no_adopt_keeps_the_archive_out_of_the_print_plan(
    digicam: Path, archive: Path, capsys
) -> None:
    assert main([str(digicam), "--print-plan", "-a", str(archive), "--no-adopt"]) == 0
    out = capsys.readouterr().out
    assert "event folder(s)" not in out
    assert "5 files from" in out


def test_print_rerun_on_an_archive_that_is_not_there(
    tmp_path: Path, digicam: Path, capsys
) -> None:
    plan = Plan(source=digicam, files=scan(digicam))
    assert print_rerun(plan, tmp_path / "nowhere") == 0
    assert "everything is new" in capsys.readouterr().out
