from __future__ import annotations

from datetime import datetime
from pathlib import Path

from digicam_archiver.layout import (
    build_items,
    event_dir,
    existing_event_names,
    month_dir,
    target_name,
    unique_event_dir,
    unique_file,
)
from digicam_archiver.model import MediaFile


def photo(name: str, taken: datetime) -> MediaFile:
    return MediaFile(
        path=Path("DCIM/100MEDIA") / name,
        kind="photo",
        taken=taken,
        size=10,
        stamp_source="exif",
    )


def video(name: str, taken: datetime) -> MediaFile:
    return MediaFile(
        path=Path("DCIM/100MEDIA") / name,
        kind="video",
        taken=taken,
        size=10,
        stamp_source="mtime",
    )


WHEN = datetime(2026, 8, 23, 14, 2)


def test_month_dir(tmp_path: Path) -> None:
    assert month_dir(tmp_path, WHEN) == tmp_path / "26-08"


def test_event_dir(tmp_path: Path) -> None:
    assert event_dir(tmp_path, WHEN, "Uni Friends Hangout") == (
        tmp_path / "26-08" / "26-08-23 Uni Friends Hangout"
    )


def test_event_dir_of_a_new_year(tmp_path: Path) -> None:
    assert month_dir(tmp_path, datetime(2027, 1, 3)) == tmp_path / "27-01"


def test_existing_event_names(tmp_path: Path) -> None:
    folder = month_dir(tmp_path, WHEN)
    folder.mkdir(parents=True)
    (folder / "26-08-23 One").mkdir()
    (folder / "26-08-24 Two").mkdir()
    (folder / "loose.txt").write_text("x")
    assert existing_event_names(tmp_path, WHEN) == {"26-08-23 One", "26-08-24 Two"}


def test_existing_event_names_of_an_empty_archive(tmp_path: Path) -> None:
    assert existing_event_names(tmp_path, WHEN) == set()


def test_unique_event_dir_avoids_the_one_that_is_there(tmp_path: Path) -> None:
    existing = month_dir(tmp_path, WHEN) / "26-08-23 Hangout"
    existing.mkdir(parents=True)
    assert unique_event_dir(tmp_path, WHEN, "Hangout") == (
        month_dir(tmp_path, WHEN) / "26-08-23 Hangout (2)"
    )


def test_unique_file(tmp_path: Path) -> None:
    assert unique_file(tmp_path, "DSCF0001.JPG") == tmp_path / "DSCF0001.JPG"
    (tmp_path / "DSCF0001.JPG").write_bytes(b"x")
    assert unique_file(tmp_path, "DSCF0001.JPG") == tmp_path / "DSCF0001_1.JPG"
    (tmp_path / "DSCF0001_1.JPG").write_bytes(b"x")
    assert unique_file(tmp_path, "DSCF0001.JPG") == tmp_path / "DSCF0001_2.JPG"


def test_unique_file_with_no_extension() -> None:
    assert unique_file(Path("/nowhere"), "clip") == Path("/nowhere/clip")


def test_target_name_renames_only_videos() -> None:
    assert target_name(photo("DSCF0001.JPG", WHEN)) == "DSCF0001.JPG"
    assert target_name(video("DSCF0003.AVI", WHEN)) == "DSCF0003.mp4"


def test_build_items_keeps_photo_names(tmp_path: Path) -> None:
    items = build_items(tmp_path, [photo("DSCF0001.JPG", WHEN)])
    assert items[0].dest == tmp_path / "DSCF0001.JPG"
    assert items[0].kind == "photo"
    assert not items[0].is_video


def test_build_items_converts_video_extension(tmp_path: Path) -> None:
    items = build_items(tmp_path, [video("DSCF0003.AVI", WHEN)])
    assert items[0].dest == tmp_path / "DSCF0003.mp4"
    assert items[0].is_video


def test_build_items_resolves_same_name_from_two_card_folders(tmp_path: Path) -> None:
    """103_FUJI and 104_FUJI both hold DSCF0001.JPG, they cannot share a name."""
    first = MediaFile(
        path=Path("DCIM/103_FUJI/DSCF0001.JPG"),
        kind="photo",
        taken=WHEN,
        size=1,
        stamp_source="exif",
    )
    second = MediaFile(
        path=Path("DCIM/104_FUJI/DSCF0001.JPG"),
        kind="photo",
        taken=WHEN,
        size=1,
        stamp_source="exif",
    )
    items = build_items(tmp_path, [first, second])
    assert [item.dest.name for item in items] == ["DSCF0001.JPG", "DSCF0001_1.JPG"]


def test_build_items_avoids_names_already_on_disk(tmp_path: Path) -> None:
    (tmp_path / "DSCF0001.JPG").write_bytes(b"x")
    items = build_items(tmp_path, [photo("DSCF0001.JPG", WHEN)])
    assert items[0].dest.name == "DSCF0001_1.JPG"
