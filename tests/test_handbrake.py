from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from digicam_archiver.handbrake import (
    DEFAULT_PRESET,
    EncodeCancelled,
    EncodeProgress,
    HandBrakeFailed,
    HandBrakeMissing,
    ProgressReader,
    build_argv,
    encode,
    list_presets,
    parse_line,
    require_handbrake,
    split_buffer,
)

TEXT = "Encoding: task 1 of 1, 42.31 % (55.61 fps, avg 61.14 fps, ETA 00h33m34s)"
TEXT_NO_ETA = "Encoding: task 1 of 1, 0.13 %"
TEXT_TWO_TASKS = "Encoding: task 2 of 3, 10.00 % (12.00 fps, avg 11.00 fps, ETA 00h01m05s)"


def test_parse_text_line() -> None:
    progress = parse_line(TEXT)
    assert progress is not None
    assert progress.fraction == pytest.approx(0.4231)
    assert progress.fps == pytest.approx(55.61)
    assert progress.eta_seconds == 33 * 60 + 34
    assert progress.task == 1
    assert progress.task_count == 1


def test_parse_text_line_without_eta() -> None:
    progress = parse_line(TEXT_NO_ETA)
    assert progress is not None
    assert progress.fraction == pytest.approx(0.0013)
    assert progress.eta_seconds is None
    assert progress.fps is None


def test_parse_text_line_counts_tasks() -> None:
    progress = parse_line(TEXT_TWO_TASKS)
    assert progress is not None
    assert progress.task == 2
    assert progress.task_count == 3
    assert progress.fraction_for_task == pytest.approx((1 + 0.1) / 3)


@pytest.mark.parametrize(
    "line",
    [
        "",
        "   ",
        "HandBrakeCLI 1.11.2 (2026010100000)",
        "Muxing: task 1 of 1, 99.00 %",
        "sync audio 1 (1), 1 track(s), 44100Hz, 1.710kbps",
    ],
)
def test_non_progress_lines_are_ignored(line: str) -> None:
    assert parse_line(line) is None


def test_parse_json_line() -> None:
    payload = json.dumps({"Progress": 12.5, "State": "WORKING", "ETA": 90, "Rate": 30.5})
    progress = parse_line(payload)
    assert progress is not None
    assert progress.fraction == pytest.approx(0.125)
    assert progress.eta_seconds == 90
    assert progress.fps == pytest.approx(30.5)


def test_parse_json_line_with_fraction_and_nesting() -> None:
    payload = json.dumps({"Progress": {"Fraction": 0.25}, "ETA": {"Seconds": 5}})
    progress = parse_line(payload)
    assert progress is not None
    assert progress.fraction == pytest.approx(0.25)


def test_parse_json_ignores_the_version_block() -> None:
    payload = json.dumps({"Major": 1, "Minor": 11, "Point": 2})
    assert parse_line(payload) is None


def test_parse_broken_json_falls_back_to_text() -> None:
    assert parse_line('{"Progress": 12.5, oops') is None


def test_split_buffer_handles_both_line_endings() -> None:
    lines, rest = split_buffer("a\nb\rc\r\nd")
    assert lines == ["a", "b", "c"]
    assert rest == "d"


def test_progress_reader_over_newlines() -> None:
    reader = ProgressReader()
    assert reader.feed(f"{TEXT_NO_ETA}\n{TEXT}\n") is not None
    assert reader.latest.fraction == pytest.approx(0.4231)


def test_progress_reader_over_carriage_returns() -> None:
    """Piped output keeps its updates on one line, separated by CR."""
    reader = ProgressReader()
    reader.feed(f"{TEXT_NO_ETA}\r{TEXT}\r")
    assert reader.latest.fraction == pytest.approx(0.4231)
    assert reader.saw_progress


def test_progress_reader_keeps_a_partial_line() -> None:
    reader = ProgressReader()
    assert reader.feed("Encoding: task 1 of 1, 4") is None
    parsed = reader.feed("2.00 %\n")
    assert parsed is not None
    assert parsed.fraction == pytest.approx(0.42)


def test_build_argv() -> None:
    argv = build_argv("/usr/bin/HandBrakeCLI", Path("in.avi"), Path("out.mp4"))
    assert argv == [
        "/usr/bin/HandBrakeCLI",
        "-i",
        "in.avi",
        "-o",
        "out.mp4",
        "--preset",
        DEFAULT_PRESET,
    ]


def test_build_argv_with_faststart_and_json() -> None:
    argv = build_argv(
        "hb",
        Path("in.avi"),
        Path("out.mp4"),
        preset="HQ 720p30 Surround",
        faststart=True,
        json_progress=True,
    )
    assert "--optimize" in argv
    assert "--json" in argv
    assert argv[argv.index("--preset") + 1] == "HQ 720p30 Surround"


def test_require_handbrake_complains_clearly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("digicam_archiver.handbrake.shutil.which", lambda _: None)
    with pytest.raises(HandBrakeMissing) as info:
        require_handbrake()
    assert "pacman" in str(info.value)


def test_list_presets_reads_the_real_names(tmp_path: Path) -> None:
    script = tmp_path / "hb"
    script.write_text('#!/bin/sh\necho "Fast 720p30"\necho "HQ 720p30 Surround"\n')
    script.chmod(0o755)
    assert list_presets(str(script)) == ["Fast 720p30", "HQ 720p30 Surround"]


def test_encode_runs_handbrake_and_reports_progress(
    tmp_path: Path, fake_handbrake: Path
) -> None:
    source = tmp_path / "in.avi"
    source.write_bytes(b"avi")
    dest = tmp_path / "out" / "in.mp4"
    seen: list[EncodeProgress] = []

    asyncio.run(
        encode(
            source,
            dest,
            binary=str(fake_handbrake),
            on_progress=seen.append,
        )
    )

    assert dest.exists() and dest.stat().st_size == 4096
    assert seen, "no progress was reported"
    assert seen[-1].fraction == pytest.approx(1.0)


def test_encode_creates_the_destination_folder(tmp_path: Path, fake_handbrake: Path) -> None:
    source = tmp_path / "in.avi"
    source.write_bytes(b"avi")
    dest = tmp_path / "a" / "b" / "in.mp4"
    asyncio.run(encode(source, dest, binary=str(fake_handbrake)))
    assert dest.exists()


def test_encode_failure_leaves_nothing_behind(tmp_path: Path, broken_handbrake: Path) -> None:
    source = tmp_path / "in.avi"
    source.write_bytes(b"avi")
    dest = tmp_path / "in.mp4"
    with pytest.raises(HandBrakeFailed) as info:
        asyncio.run(encode(source, dest, binary=str(broken_handbrake)))
    assert "cannot open input" in str(info.value)
    assert not dest.exists()


def test_encode_cancellation_removes_the_partial_file(
    tmp_path: Path, slow_handbrake: Path
) -> None:
    source = tmp_path / "in.avi"
    source.write_bytes(b"avi")
    dest = tmp_path / "in.mp4"
    cancel = asyncio.Event()

    async def scenario() -> None:
        async def stop_soon() -> None:
            await asyncio.sleep(0.1)
            cancel.set()

        stopper = asyncio.create_task(stop_soon())
        try:
            await encode(source, dest, binary=str(slow_handbrake), cancel=cancel)
        finally:
            await stopper

    with pytest.raises(EncodeCancelled):
        asyncio.run(scenario())
    assert not dest.exists()


def test_encode_cancelled_before_it_starts(tmp_path: Path, fake_handbrake: Path) -> None:
    source = tmp_path / "in.avi"
    source.write_bytes(b"avi")
    dest = tmp_path / "in.mp4"
    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises(EncodeCancelled):
        asyncio.run(encode(source, dest, binary=str(fake_handbrake), cancel=cancel))
