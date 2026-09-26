from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from digicam_archiver.fingerprint import key_of
from digicam_archiver.layout import (
    ExistingFiles,
    build_items,
    event_dir,
    existing_event_names,
    month_dir,
    target_name,
    unique_event_dir,
    unique_file,
)
from digicam_archiver.model import MediaFile
from digicam_archiver.transfer import copy_photo

from .conftest import write_photo


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


# --------------------------------------------------------------------- reruns


def real_photo(
    root: Path, name: str, taken: datetime, colour: tuple[int, int, int]
) -> MediaFile:
    """A file on disk under ``root``, so it can be fingerprinted."""
    path = write_photo(root / "DCIM" / "100MEDIA" / name, taken, colour=colour)
    return MediaFile(
        path=path,
        kind="photo",
        taken=taken,
        size=path.stat().st_size,
        stamp_source="exif",
    )


def test_build_items_keeps_the_name_of_a_file_that_is_already_there(
    tmp_path: Path,
) -> None:
    item = real_photo(tmp_path, "DSCF0001.JPG", WHEN, (10, 200, 30))
    copy_photo(item.path, tmp_path / "DSCF0001.JPG")
    assert build_items(tmp_path, [item])[0].dest == tmp_path / "DSCF0001.JPG"


def test_build_items_finds_an_already_archived_file_under_a_suffixed_name(
    tmp_path: Path,
) -> None:
    """A rerun after a name collision must not add a second copy."""
    item = real_photo(tmp_path, "DSCF0002.JPG", WHEN, (200, 10, 30))
    copy_photo(item.path, tmp_path / "DSCF0001_1.JPG")
    assert build_items(tmp_path, [item])[0].dest == tmp_path / "DSCF0001_1.JPG"


def test_build_items_suffixes_when_the_name_holds_other_photos(tmp_path: Path) -> None:
    item = real_photo(tmp_path, "DSCF0001.JPG", WHEN, (10, 200, 30))
    (tmp_path / "DSCF0001.JPG").write_bytes(b"x" * item.size)
    # same size, different bytes, different mtime: the head hash has to say no
    os.utime(tmp_path / "DSCF0001.JPG", (0, 0))
    assert build_items(tmp_path, [item])[0].dest.name == "DSCF0001_1.JPG"


def test_build_items_still_reuses_when_the_mtime_was_lost(tmp_path: Path) -> None:
    """A backup restore drops the mtime; the head hash still knows the file."""
    item = real_photo(tmp_path, "DSCF0001.JPG", WHEN, (10, 200, 30))
    dest = tmp_path / "DSCF0001.JPG"
    copy_photo(item.path, dest)
    os.utime(dest, (0, 0))
    assert build_items(tmp_path, [item])[0].dest == dest


def test_build_items_hands_out_one_archived_file_only_once(tmp_path: Path) -> None:
    """Two byte identical photos on the card cannot share one archived name."""
    first = real_photo(tmp_path, "DSCF0001.JPG", WHEN, (10, 200, 30))
    second_path = tmp_path / "card" / "DCIM" / "101MEDIA" / "DSCF0001.JPG"
    copy_photo(first.path, second_path)
    second = MediaFile(
        path=second_path,
        kind="photo",
        taken=WHEN,
        size=first.size,
        stamp_source="exif",
    )
    items = build_items(tmp_path, [first, second])
    assert [item.dest.name for item in items] == ["DSCF0001.JPG", "DSCF0001_1.JPG"]


def test_build_items_reuses_an_existing_video(tmp_path: Path) -> None:
    source = tmp_path / "card" / "DSCF0003.AVI"
    source.parent.mkdir()
    source.write_bytes(b"x" * 100)
    item = MediaFile(path=source, kind="video", taken=WHEN, size=100, stamp_source="mtime")
    (tmp_path / "DSCF0003.mp4").write_bytes(b"encoded")
    assert build_items(tmp_path, [item])[0].dest == tmp_path / "DSCF0003.mp4"


def test_build_items_ignores_part_and_dot_files(tmp_path: Path) -> None:
    (tmp_path / "DSCF0001.JPG.part").write_bytes(b"x")
    (tmp_path / ".digicam").write_bytes(b"x")
    items = build_items(tmp_path, [photo("DSCF0001.JPG", WHEN)])
    assert items[0].dest == tmp_path / "DSCF0001.JPG"


def test_build_items_uses_what_the_caller_already_knows(tmp_path: Path) -> None:
    """Reconcile hands over the fingerprints, so nothing is read from disk."""
    item = real_photo(tmp_path, "DSCF0001.JPG", WHEN, (10, 200, 30))
    copy_photo(item.path, tmp_path / "DSCF0001_1.JPG")
    existing = ExistingFiles.of(tmp_path, {item.size})
    items = build_items(
        tmp_path, [item], existing=existing, keys={item.path: key_of(item.path)}
    )
    assert items[0].dest == tmp_path / "DSCF0001_1.JPG"


def test_existing_files_reads_a_folder(tmp_path: Path) -> None:
    (tmp_path / "DSCF0001.JPG").write_bytes(b"photo bytes")
    (tmp_path / "DSCF0003.mp4").write_bytes(b"video")
    (tmp_path / "notes.txt").write_bytes(b"x")
    (tmp_path / "DSCF0004.JPG.part").write_bytes(b"x")
    existing = ExistingFiles.of(tmp_path)
    assert existing.by_stem == {"DSCF0003": ["DSCF0003.mp4"]}
    assert existing.names() == {"DSCF0001.JPG", "DSCF0003.mp4"}
    assert len(existing.by_content) == 1


def test_existing_files_only_hashes_the_sizes_asked_for(tmp_path: Path) -> None:
    (tmp_path / "big.jpg").write_bytes(b"x" * 5000)
    (tmp_path / "small.jpg").write_bytes(b"x" * 10)
    existing = ExistingFiles.of(tmp_path, {10})
    # the stat map is free and holds both, the content map only what was asked for
    assert existing.names() == {"big.jpg", "small.jpg"}
    assert existing.by_content == {key_of(tmp_path / "small.jpg").content: ["small.jpg"]}


def test_existing_files_hands_out_one_name_per_copy(tmp_path: Path) -> None:
    first = tmp_path / "DSCF0001.JPG"
    second = tmp_path / "DSCF0001_1.JPG"
    first.write_bytes(b"photo bytes")
    second.write_bytes(b"photo bytes")
    os.utime(first, (0, 0))
    os.utime(second, (0, 0))
    existing = ExistingFiles.of(tmp_path)
    # same content, and the same timestamps here, so both maps hold both names
    assert existing.by_stat == {key_of(first).stat: ["DSCF0001.JPG", "DSCF0001_1.JPG"]}
    assert existing.by_content == {key_of(first).content: ["DSCF0001.JPG", "DSCF0001_1.JPG"]}
