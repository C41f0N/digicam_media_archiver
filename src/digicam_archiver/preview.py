"""Draw photos and videos into the terminal.

Colour mode paints two pixels per cell with U+2580 (upper half block): the
foreground colour is the top pixel, the background the bottom one.  That is the
most a character cell can carry, so it is the closest a terminal gets to the
picture.  Mono mode falls back to the classic luminance ramp for terminals
without truecolor.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps
from rich.color import Color
from rich.style import Style
from rich.text import Text

UPPER_HALF_BLOCK = "\u2580"
RAMP = " .:-=+*#%@"
MAX_PREVIEW_BYTES = 192 * 1024 * 1024

#: A terminal cell is about twice as tall as it is wide.
CELL_ASPECT = 2.0


class PreviewError(RuntimeError):
    """Preview could not be produced; the UI shows the message instead."""


def ffmpeg_binary() -> str | None:
    return shutil.which("ffmpeg")


def ffprobe_binary() -> str | None:
    return shutil.which("ffprobe")


@dataclass(frozen=True, slots=True)
class RenderResult:
    text: Text
    width: int
    height: int
    note: str = ""


def fit_box(aspect: float, max_cols: int, max_rows: int) -> tuple[int, int]:
    """Largest ``(cols, rows)`` that fits and keeps the source aspect ratio."""
    if max_cols < 1 or max_rows < 1:
        return 0, 0
    cols = max_cols
    rows = round(cols / (max(aspect, 1e-6) * CELL_ASPECT))
    if rows > max_rows:
        rows = max_rows
        cols = round(aspect * CELL_ASPECT * rows)
    return max(1, min(cols, max_cols)), max(1, min(rows, max_rows))


@lru_cache(maxsize=4096)
def _style(fg: tuple[int, int, int], bg: tuple[int, int, int]) -> Style:
    return Style(color=Color.from_rgb(*fg), bgcolor=Color.from_rgb(*bg))


def render_image(image: Image.Image, cols: int, rows: int, colour: bool = True) -> Text:
    """Render a PIL image into a block of text that keeps its aspect ratio."""
    width, height = fit_box(image.width / image.height, cols, rows)
    if colour:
        target = image.convert("RGB").resize((width, height * 2), Image.LANCZOS)
        pixels = np.asarray(target, dtype=np.uint8)
        top = pixels[0::2]
        bottom = pixels[1::2]
        lines: list[list[tuple[str, Style]]] = []
        for y in range(height):
            row: list[tuple[str, Style]] = []
            top_row = top[y]
            bottom_row = bottom[y]
            for x in range(width):
                style = _style(
                    (int(top_row[x][0]), int(top_row[x][1]), int(top_row[x][2])),
                    (int(bottom_row[x][0]), int(bottom_row[x][1]), int(bottom_row[x][2])),
                )
                row.append((UPPER_HALF_BLOCK, style))
            lines.append(row)
    else:
        target = image.convert("L").resize((width, height), Image.LANCZOS)
        grey = np.asarray(target, dtype=np.uint8)
        steps = len(RAMP) - 1
        lines = []
        for y in range(height):
            row = [
                (RAMP[min(steps, int(grey[y][x]) * (steps + 1) // 256)], Style())
                for x in range(width)
            ]
            lines.append(row)
    text = Text(no_wrap=True, overflow="crop")
    for row in lines:
        for char, style in row:
            text.append(char, style)
        text.append("\n")
    return text


def load_photo(path: Path) -> Image.Image:
    with Image.open(path) as img:
        img.load()
        return ImageOps.exif_transpose(img) or img


def video_duration(path: Path) -> float | None:
    probe = ffprobe_binary()
    if probe is None:
        return None
    try:
        result = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        seconds = float(result.stdout.strip())
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def extract_frame(path: Path, fraction: float = 0.2) -> tuple[Image.Image, float]:
    """Pull one representative frame out of a video with ffmpeg.

    Returns the frame and the timestamp it was taken at, in seconds.
    """
    ffmpeg = ffmpeg_binary()
    if ffmpeg is None:
        raise PreviewError("ffmpeg not found, cannot preview videos")
    duration = video_duration(path)
    offset = max(0.0, (duration or 0.0) * fraction)
    command = [
        ffmpeg,
        "-nostdin",
        "-v",
        "error",
        "-ss",
        f"{offset:.2f}",
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-",
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreviewError(f"ffmpeg failed: {exc}") from exc
    if result.returncode != 0 or not result.stdout:
        detail = result.stderr.decode("utf-8", "ignore").strip().splitlines()
        raise PreviewError(detail[-1] if detail else "ffmpeg produced no frame")
    with Image.open(io.BytesIO(result.stdout)) as img:
        img.load()
        return img.convert("RGB"), offset


_CACHED: OrderedDict[tuple, RenderResult] = OrderedDict()
_CACHE_LIMIT = 16


def render_media(
    path: Path,
    cols: int,
    rows: int,
    colour: bool = True,
    is_video: bool = False,
    fraction: float = 0.2,
) -> RenderResult:
    """Render a photo or a still of a video, with a small cache.

    Called from the UI on resize and on every cursor move, so the same picture
    at the same size must not be decoded twice.
    """
    key = (str(path), cols, rows, colour, is_video, round(fraction, 3))
    cached = _CACHED.get(key)
    if cached is not None:
        _CACHED.move_to_end(key)
        return cached

    note = ""
    if is_video:
        image, offset = extract_frame(path, fraction)
        note = f"still at {format_offset(offset)}"
    else:
        if path.stat().st_size > MAX_PREVIEW_BYTES:
            raise PreviewError("file too large to preview")
        image = load_photo(path)

    result = RenderResult(
        text=render_image(image, cols, rows, colour=colour),
        width=fit_box(image.width / image.height, cols, rows)[0],
        height=fit_box(image.width / image.height, cols, rows)[1],
        note=note,
    )
    del image
    _CACHED[key] = result
    while len(_CACHED) > _CACHE_LIMIT:
        _CACHED.popitem(last=False)
    return result


def format_offset(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60:d}:{total % 60:02d}"
