"""Shared fixtures: fake digicam cards, real jpgs and avis, fake HandBrake."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "ui: drives the text user interface")


@pytest.fixture
def ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found is None:  # pragma: no cover - depends on the machine
        pytest.skip("ffmpeg is not installed")
    return found


def set_mtime(path: Path, when: datetime) -> None:
    stamp = when.timestamp()
    os.utime(path, (stamp, stamp))


def write_photo(
    path: Path,
    taken: datetime | None = None,
    size: tuple[int, int] = (64, 48),
    colour: tuple[int, int, int] = (200, 100, 50),
    exif_in_sub_ifd: bool = False,
    write_exif: bool = True,
) -> Path:
    """A real jpeg, with EXIF capture time when asked for."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, colour)
    exif = Image.Exif()
    if taken is not None and write_exif:
        text = taken.strftime("%Y:%m:%d %H:%M:%S")
        if exif_in_sub_ifd:
            exif.get_ifd(0x8769)[0x9003] = text
        else:
            exif[0x9003] = text
    image.save(path, exif=exif)
    if taken is not None:
        set_mtime(path, taken)
    return path


def write_video(
    ffmpeg_bin: str, path: Path, seconds: float = 1.0, size: tuple[int, int] = (160, 120)
) -> Path:
    """A real avi, so ffmpeg and HandBrake have something to chew on."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg_bin,
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={seconds}:size={size[0]}x{size[1]}:rate=10",
            "-c:v",
            "mjpeg",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


FAKE_HANDBRAKE = """#!/bin/sh
# Minimal stand-in for HandBrakeCLI: prints progress, then writes the output.
while [ $# -gt 0 ]; do
  case "$1" in
    -i) src="$2"; shift 2 ;;
    -o) dst="$2"; shift 2 ;;
    *) shift ;;
  esac
done
for step in 1 2 3 4 5; do
  printf "Encoding: task 1 of 1, $((step * 20)).00 %%\\n"
  printf "Encoding: task 1 of 1, $((step * 20)).00 %% (12.50 fps, avg 11.00 fps, ETA 00h00m10s)\\r"
  sleep 0.02
done
mkdir -p "$(dirname "$dst")"
head -c 4096 /dev/urandom > "$dst"
exit 0
"""

BROKEN_HANDBRAKE = """#!/bin/sh
echo "HandBrakeCLI: cannot open input file" >&2
exit 3
"""

SLOW_HANDBRAKE = """#!/bin/sh
# Takes long enough to be cancelled half way through.
while [ $# -gt 0 ]; do
  case "$1" in
    -i) src="$2"; shift 2 ;;
    -o) dst="$2"; shift 2 ;;
    *) shift ;;
  esac
done
i=0
while [ $i -lt 100 ]; do
  printf "Encoding: task 1 of 1, $i.00 %%\\n"
  i=$((i + 1))
  sleep 0.05
done
mkdir -p "$(dirname "$dst")"
head -c 2048 /dev/urandom > "$dst"
"""


def write_fake_handbrake(path: Path, body: str = FAKE_HANDBRAKE) -> Path:
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.fixture
def fake_handbrake(tmp_path: Path) -> Path:
    return write_fake_handbrake(tmp_path / "fake-HandBrakeCLI")


@pytest.fixture
def slow_handbrake(tmp_path: Path) -> Path:
    return write_fake_handbrake(tmp_path / "slow-HandBrakeCLI", SLOW_HANDBRAKE)


@pytest.fixture
def broken_handbrake(tmp_path: Path) -> Path:
    return write_fake_handbrake(tmp_path / "broken-HandBrakeCLI", BROKEN_HANDBRAKE)


@pytest.fixture
def digicam(tmp_path: Path, ffmpeg: str) -> Path:
    """A card with two DCIM folders and a two day timeline.

    14:02, 14:05, 14:07 and 18:00 on 23 August are one event under the default
    four hour gap; the photo on 24 August starts the second one.  ``DSCF0001``
    appears in both DCIM folders, which is what the rename logic is for.
    """
    root = tmp_path / "card"
    write_photo(root / "DCIM" / "103_FUJI" / "DSCF0001.JPG", datetime(2026, 8, 23, 14, 2))
    write_photo(root / "DCIM" / "103_FUJI" / "DSCF0002.JPG", datetime(2026, 8, 23, 14, 5))
    write_video(ffmpeg, root / "DCIM" / "103_FUJI" / "DSCF0003.AVI")
    set_mtime(root / "DCIM" / "103_FUJI" / "DSCF0003.AVI", datetime(2026, 8, 23, 14, 7))
    write_photo(root / "DCIM" / "104_FUJI" / "DSCF0001.JPG", datetime(2026, 8, 23, 18, 0))
    write_photo(root / "DCIM" / "104_FUJI" / "DSCF0004.JPG", datetime(2026, 8, 24, 10, 0))
    (root / "DCIM" / "104_FUJI" / "DSCF0002.THM").write_bytes(b"thumbnail")
    return root


@pytest.fixture
def archive(tmp_path: Path) -> Path:
    target = tmp_path / "archive"
    target.mkdir()
    return target


@pytest.fixture
def python() -> str:
    return sys.executable
