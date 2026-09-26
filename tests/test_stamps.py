from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from digicam_archiver.stamps import (
    capture_time,
    exif_datetime,
    mtime,
    parse_exif_datetime,
)

from .conftest import set_mtime, write_photo


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026:08:23 14:02:11", datetime(2026, 8, 23, 14, 2, 11)),
        ("2026-08-23 14:02:11", datetime(2026, 8, 23, 14, 2, 11)),
        ("2026:08:23 14:02", datetime(2026, 8, 23, 14, 2)),
        ("2026:08:23T14:02:11", datetime(2026, 8, 23, 14, 2, 11)),
        (b"2026:08:23 14:02:11", datetime(2026, 8, 23, 14, 2, 11)),
    ],
)
def test_parse_exif_datetime(raw, expected) -> None:
    assert parse_exif_datetime(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "not a date", "2026:13:45 99:99:99", 42, b""])
def test_parse_exif_datetime_rejects_junk(raw) -> None:
    assert parse_exif_datetime(raw) is None


def test_exif_datetime_from_ifd0(tmp_path: Path) -> None:
    path = write_photo(tmp_path / "a.jpg", datetime(2026, 8, 23, 14, 2, 11))
    assert exif_datetime(path) == datetime(2026, 8, 23, 14, 2, 11)


def test_exif_datetime_from_exif_ifd(tmp_path: Path) -> None:
    """Real cameras keep DateTimeOriginal in the Exif sub-IFD, not IFD0."""
    path = write_photo(
        tmp_path / "b.jpg", datetime(2026, 8, 23, 14, 2, 11), exif_in_sub_ifd=True
    )
    assert exif_datetime(path) == datetime(2026, 8, 23, 14, 2, 11)


def test_exif_datetime_missing(tmp_path: Path) -> None:
    path = write_photo(tmp_path / "c.jpg", None)
    assert exif_datetime(path) is None


def test_exif_datetime_on_broken_file(tmp_path: Path) -> None:
    path = tmp_path / "broken.jpg"
    path.write_bytes(b"not an image at all")
    assert exif_datetime(path) is None


def test_capture_time_prefers_exif(tmp_path: Path) -> None:
    taken = datetime(2026, 8, 23, 14, 2, 11)
    path = write_photo(tmp_path / "d.jpg", taken)
    set_mtime(path, datetime(2020, 1, 1))
    assert capture_time(path, "photo") == (taken, "exif")


def test_capture_time_falls_back_to_mtime(tmp_path: Path) -> None:
    path = write_photo(tmp_path / "e.jpg", None)
    set_mtime(path, datetime(2026, 8, 23, 9, 0))
    assert capture_time(path, "photo") == (datetime(2026, 8, 23, 9, 0), "mtime")


def test_capture_time_for_video_uses_mtime(tmp_path: Path) -> None:
    path = tmp_path / "clip.avi"
    path.write_bytes(b"avi")
    set_mtime(path, datetime(2026, 8, 23, 14, 7))
    assert capture_time(path, "video") == (datetime(2026, 8, 23, 14, 7), "mtime")


def test_mtime(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"x")
    set_mtime(path, datetime(2026, 8, 23, 14, 7))
    assert mtime(path) == datetime(2026, 8, 23, 14, 7)
