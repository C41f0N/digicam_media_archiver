from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

from digicam_archiver.preview import (
    UPPER_HALF_BLOCK,
    PreviewError,
    extract_frame,
    fit_box,
    format_offset,
    load_photo,
    render_image,
    render_media,
    video_duration,
)

from .conftest import write_photo, write_video


def test_fit_box_keeps_a_wide_photo_short() -> None:
    cols, rows = fit_box(16 / 9, 80, 40)
    assert cols == 80
    assert rows == pytest.approx(80 / (16 / 9 * 2), abs=1)


def test_fit_box_shrinks_to_the_available_height() -> None:
    cols, rows = fit_box(4.0, 100, 10)
    assert rows == 10
    assert cols == 80


def test_fit_box_of_a_tall_phone_photo() -> None:
    cols, rows = fit_box(0.5, 60, 40)
    assert rows == 40
    assert cols == 40


def test_fit_box_never_returns_zero() -> None:
    assert fit_box(1.0, 0, 0) == (0, 0)
    assert fit_box(1.0, 1, 1) == (1, 1)


def test_render_image_colour_uses_half_blocks() -> None:
    image = Image.new("RGB", (20, 20), (10, 20, 30))
    text = render_image(image, 20, 10, colour=True)
    lines = text.plain.splitlines()
    assert len(lines) == 10
    assert all(line == UPPER_HALF_BLOCK * 20 for line in lines)


def test_render_image_keeps_the_aspect_ratio() -> None:
    """A 4:3 photo in a 20x10 box is 20 wide and 7 tall, not 10."""
    text = render_image(Image.new("RGB", (40, 30), (0, 0, 0)), 20, 10, colour=True)
    assert len(text.plain.splitlines()) == 8


def test_render_image_colour_carries_both_pixels() -> None:
    """Top half is the foreground, bottom half the background, so both show."""
    image = Image.new("RGB", (1, 2))
    image.putpixel((0, 0), (255, 0, 0))
    image.putpixel((0, 1), (0, 0, 255))
    text = render_image(image, 1, 1, colour=True)
    assert text.spans, "no styled spans in the rendered text"
    style = text.spans[0][2]
    assert style.color.triplet.hex.lower() == "#ff0000"
    assert style.bgcolor.triplet.hex.lower() == "#0000ff"


def test_render_image_mono_uses_the_ramp() -> None:
    white = render_image(Image.new("RGB", (20, 20), (255, 255, 255)), 10, 5, colour=False)
    black = render_image(Image.new("RGB", (20, 20), (0, 0, 0)), 10, 5, colour=False)
    assert white.plain.splitlines()[0] == "@" * 10
    assert black.plain.splitlines()[0] == " " * 10


def test_load_photo_without_a_bound_decodes_everything(tmp_path: Path) -> None:
    path = write_photo(tmp_path / "big.jpg", size=(1200, 900))
    assert load_photo(path).size == (1200, 900)


def test_load_photo_drafts_a_smaller_decode(tmp_path: Path) -> None:
    path = write_photo(tmp_path / "big.jpg", size=(1200, 900))
    small = load_photo(path, max_pixels=(100, 100))
    assert small.width < 1200 and small.height < 900
    # libjpeg only halves, so the aspect has to survive
    assert small.width / small.height == pytest.approx(1200 / 900, rel=0.02)


def test_load_photo_still_flips_a_rotated_photo(tmp_path: Path) -> None:
    path = tmp_path / "sideways.jpg"
    image = Image.new("RGB", (1200, 900), (10, 20, 30))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotate 90 degrees
    image.save(path, exif=exif)
    turned = load_photo(path, max_pixels=(100, 100))
    assert turned.height > turned.width
    assert turned.width / turned.height == pytest.approx(900 / 1200, rel=0.02)


def test_render_media_of_a_photo(tmp_path: Path) -> None:
    path = write_photo(tmp_path / "a.jpg", datetime(2026, 8, 23, 14, 2))
    result = render_media(path, 40, 20, colour=True, is_video=False)
    assert result.width > 0 and result.height > 0
    assert UPPER_HALF_BLOCK in result.text.plain
    assert result.note == ""


def test_render_media_is_cached(tmp_path: Path) -> None:
    path = write_photo(tmp_path / "b.jpg", datetime(2026, 8, 23, 14, 2))
    first = render_media(path, 30, 10, colour=False, is_video=False)
    second = render_media(path, 30, 10, colour=False, is_video=False)
    assert first is second


def test_render_media_cache_separates_sizes(tmp_path: Path) -> None:
    path = write_photo(tmp_path / "c.jpg", datetime(2026, 8, 23, 14, 2))
    assert render_media(path, 30, 10, colour=False) is not render_media(
        path, 31, 10, colour=False
    )


def test_render_media_on_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        render_media(tmp_path / "nope.jpg", 20, 10)


def test_format_offset() -> None:
    assert format_offset(0) == "0:00"
    assert format_offset(9) == "0:09"
    assert format_offset(75) == "1:15"
    assert format_offset(605) == "10:05"


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe is not installed")
def test_video_duration(tmp_path: Path, ffmpeg: str) -> None:
    clip = write_video(ffmpeg, tmp_path / "clip.avi", seconds=2)
    duration = video_duration(clip)
    assert duration is not None
    assert 1.5 <= duration <= 2.5


def test_video_duration_of_junk(tmp_path: Path) -> None:
    junk = tmp_path / "junk.avi"
    junk.write_bytes(b"not a video")
    assert video_duration(junk) is None


def test_extract_frame(tmp_path: Path, ffmpeg: str) -> None:
    clip = write_video(ffmpeg, tmp_path / "clip.avi", seconds=2)
    image, offset = extract_frame(clip, 0.2)
    assert image.width > 0 and image.height > 0
    assert offset == pytest.approx(0.4, abs=0.1)


def test_extract_frame_from_junk(tmp_path: Path) -> None:
    junk = tmp_path / "junk.avi"
    junk.write_bytes(b"not a video")
    with pytest.raises(PreviewError):
        extract_frame(junk)


def test_render_media_of_a_video(tmp_path: Path, ffmpeg: str) -> None:
    clip = write_video(ffmpeg, tmp_path / "clip.avi", seconds=1)
    result = render_media(clip, 30, 12, colour=True, is_video=True)
    assert "still at" in result.note
    assert result.text.plain.strip()
