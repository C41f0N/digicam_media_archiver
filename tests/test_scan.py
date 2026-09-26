from __future__ import annotations

from datetime import datetime
from pathlib import Path

from digicam_archiver.scan import find_media, media_root, scan

from .conftest import set_mtime, write_photo


def test_media_root_prefers_dcim(tmp_path: Path) -> None:
    (tmp_path / "DCIM").mkdir()
    assert media_root(tmp_path) == tmp_path / "DCIM"


def test_media_root_falls_back_to_the_folder(tmp_path: Path) -> None:
    assert media_root(tmp_path) == tmp_path


def test_find_media_digests_everything_we_care_about(tmp_path: Path) -> None:
    write_photo(tmp_path / "DCIM" / "100MEDIA" / "DSCF0001.JPG", datetime(2026, 8, 23, 10))
    write_photo(tmp_path / "DCIM" / "101PANOR" / "DSCF0002.JPEG", datetime(2026, 8, 23, 10, 1))
    (tmp_path / "DCIM" / "101PANOR" / "DSCF0002.THM").write_bytes(b"x")
    (tmp_path / "DCIM" / "101PANOR" / "DSCF0003.mp4").write_bytes(b"x")
    (tmp_path / "DCIM" / "camlog.json").write_text("{}")

    names = sorted(path.name for path, _ in find_media(tmp_path))
    assert names == ["DSCF0001.JPG", "DSCF0002.JPEG"]


def test_find_media_ignores_thumbnail_and_other_video_formats(tmp_path: Path) -> None:
    (tmp_path / "clip.avi").write_bytes(b"x")
    (tmp_path / "clip.THM").write_bytes(b"x")
    (tmp_path / "already.MP4").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("x")
    found = find_media(tmp_path)
    assert [path.name for path, _ in found] == ["clip.avi"]


def test_find_media_without_dcim(tmp_path: Path) -> None:
    write_photo(tmp_path / "loose.JPG", datetime(2026, 8, 23, 10))
    assert [path.name for path, _ in find_media(tmp_path)] == ["loose.JPG"]


def test_scan_sorts_by_capture_time_and_records_the_source(digicam: Path) -> None:
    files = scan(digicam)
    assert [f.taken for f in files] == sorted(f.taken for f in files)
    assert [f.taken.strftime("%H:%M") for f in files] == [
        "14:02",
        "14:05",
        "14:07",
        "18:00",
        "10:00",
    ]
    sources = {f.name: f.stamp_source for f in files}
    assert sources["DSCF0001.JPG"] == "exif"
    assert sources["DSCF0003.AVI"] == "mtime"


def test_scan_counts_sizes(digicam: Path) -> None:
    files = scan(digicam)
    assert all(f.size > 0 for f in files)
    assert sum(f.size for f in files) == sum(f.path.stat().st_size for f in files)


def test_scan_of_empty_folder(tmp_path: Path) -> None:
    assert scan(tmp_path) == []


def test_scan_skips_folders_that_only_look_like_media(tmp_path: Path) -> None:
    (tmp_path / "DCIM" / "104_FUJI.JPG").mkdir(parents=True)
    write_photo(tmp_path / "DCIM" / "104_FUJI" / "DSCF0001.JPG", datetime(2026, 8, 23, 10))
    assert [f.name for f in scan(tmp_path)] == ["DSCF0001.JPG"]


def test_avi_without_mtime_uses_now_not_crash(tmp_path: Path) -> None:
    path = tmp_path / "clip.avi"
    path.write_bytes(b"x")
    set_mtime(path, datetime(2026, 8, 23, 14, 7))
    files = scan(tmp_path)
    assert files[0].kind == "video"
