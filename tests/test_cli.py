from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path

import pytest

from digicam_archiver.cli import build_parser, check_source, main, parse_gap, print_plan
from digicam_archiver.partition import Plan
from digicam_archiver.scan import scan


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
