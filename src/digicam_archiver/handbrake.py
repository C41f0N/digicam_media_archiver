"""Drive HandBrakeCLI and read its progress.

HandBrake reports progress as human text::

    Encoding: task 1 of 1, 42.31 % (55.61 fps, avg 61.14 fps, ETA 00h33m34s)

and, with ``--json``, as JSON.  The line ending depends on whether stdout is a
terminal: piped it writes one update per line, on a tty it uses carriage
returns.  So the reader splits on both, and the parser understands both formats
-- the text line is the default because it has been stable for a decade, the
JSON parser is there for builds that switch over.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PRESET = "Fast 720p30"
INSTALL_HINT = "install it with: sudo pacman -S handbrake-cli"

#: How often the reader wakes up to check for new output and for cancellation.
POLL_INTERVAL = 0.1
#: How often a stalled encode still reports its last known progress.
KEEPALIVE_INTERVAL = 1.0

_TEXT_RE = re.compile(
    r"Encoding:.*?([\d.]+)\s*%"
    r"(?:\s*\(([\d.]+)\s*fps,\s*avg\s*[\d.]+\s*fps,\s*ETA\s*(\d+)h(\d+)m(\d+)s\))?",
    re.IGNORECASE,
)
_TASK_RE = re.compile(r"task\s+(\d+)\s+of\s+(\d+)", re.IGNORECASE)

_PERCENT_KEYS = ("progress", "percentdone", "percent", "fraction", "percentcomplete")
_ETA_KEYS = ("eta", "etatime", "etaseconds", "secondsleft", "remain")
_RATE_KEYS = ("rate", "fps", "framerate", "avgframerate", "averagefps")


class HandBrakeMissing(RuntimeError):
    """HandBrakeCLI is not on PATH."""


class HandBrakeFailed(RuntimeError):
    """HandBrakeCLI exited non-zero, or produced nothing."""


class EncodeCancelled(RuntimeError):
    """The user aborted the encode."""


@dataclass(slots=True)
class EncodeProgress:
    fraction: float | None = None
    fps: float | None = None
    eta_seconds: int | None = None
    task: int = 1
    task_count: int = 1
    phase: str = "Encoding"

    @property
    def percent(self) -> float | None:
        return None if self.fraction is None else self.fraction * 100.0

    @property
    def fraction_for_task(self) -> float | None:
        if self.fraction is None or self.task_count <= 0:
            return None
        overall = (self.task - 1 + self.fraction) / self.task_count
        return min(max(overall, 0.0), 1.0)


def handbrake_binary() -> str | None:
    return shutil.which("HandBrakeCLI") or shutil.which("handbrakecli")


def require_handbrake() -> str:
    found = handbrake_binary()
    if found is None:
        raise HandBrakeMissing(f"HandBrakeCLI not found, {INSTALL_HINT}")
    return found


def list_presets(binary: str | None = None) -> list[str]:
    """The preset names this HandBrake build actually has."""
    found = binary or require_handbrake()
    try:
        result = subprocess.run(
            [found, "--preset-list"],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except OSError as exc:  # pragma: no cover - defensive
        raise HandBrakeMissing(str(exc)) from exc
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def build_argv(
    binary: str,
    source: Path,
    dest: Path,
    preset: str = DEFAULT_PRESET,
    faststart: bool = False,
    json_progress: bool = False,
) -> list[str]:
    argv = [binary, "-i", str(source), "-o", str(dest), "--preset", preset]
    if faststart:
        argv.append("--optimize")
    if json_progress:
        argv.append("--json")
    return argv


def _find_number(payload: object, keys: tuple[str, ...]) -> float | None:
    """Find the first matching key anywhere inside a decoded JSON blob."""
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key.lower().replace("_", "") in keys and isinstance(value, (int, float)):
                return float(value)
        for value in payload.values():
            found = _find_number(value, keys)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_number(value, keys)
            if found is not None:
                return found
    return None


def _from_json(payload: object) -> EncodeProgress | None:
    fraction = _find_number(payload, _PERCENT_KEYS)
    if fraction is None:
        return None
    if fraction > 1.0:
        fraction /= 100.0
    fraction = min(max(fraction, 0.0), 1.0)
    eta = _find_number(payload, _ETA_KEYS)
    rate = _find_number(payload, _RATE_KEYS)
    task = _find_number(payload, ("task", "tasknumber"))
    task_count = _find_number(payload, ("taskcount",))
    return EncodeProgress(
        fraction=fraction,
        fps=rate,
        eta_seconds=int(eta) if eta is not None else None,
        task=int(task) if task else 1,
        task_count=int(task_count) if task_count else 1,
    )


def parse_line(line: str) -> EncodeProgress | None:
    """Parse one progress line, JSON or text. None if it is not progress."""
    stripped = line.strip()
    if not stripped:
        return None

    if stripped.startswith("{"):
        end = stripped.rfind("}")
        try:
            payload = json.loads(stripped[: end + 1] if end != -1 else stripped)
        except json.JSONDecodeError:
            payload = None
        if payload is not None:
            return _from_json(payload)

    match = _TEXT_RE.search(stripped)
    if match is None:
        return None
    percent, fps, eta_h, eta_m, eta_s = match.groups()
    eta = int(eta_h) * 3600 + int(eta_m) * 60 + int(eta_s) if eta_h is not None else None
    task, task_count = 1, 1
    task_match = _TASK_RE.search(stripped)
    if task_match:
        task, task_count = int(task_match.group(1)), int(task_match.group(2))
    return EncodeProgress(
        fraction=min(max(float(percent) / 100.0, 0.0), 1.0),
        fps=float(fps) if fps else None,
        eta_seconds=eta,
        task=task,
        task_count=task_count,
    )


def split_buffer(buffer: str) -> tuple[list[str], str]:
    """Split on newlines *and* carriage returns; return the lines and the tail."""
    *complete, remainder = re.split(r"[\r\n]+", buffer)
    return complete, remainder


class ProgressReader:
    """Feed raw text in, get the newest :class:`EncodeProgress` out."""

    def __init__(self) -> None:
        self._buffer = ""
        self.latest = EncodeProgress()
        self.saw_progress = False

    def feed(self, chunk: str) -> EncodeProgress | None:
        self._buffer += chunk
        lines, self._buffer = split_buffer(self._buffer)
        newest: EncodeProgress | None = None
        for line in lines:
            parsed = parse_line(line)
            if parsed is not None:
                self.latest = parsed
                self.saw_progress = True
                newest = parsed
        return newest


async def encode(
    source: Path,
    dest: Path,
    preset: str = DEFAULT_PRESET,
    faststart: bool = False,
    binary: str | None = None,
    on_progress=None,
    cancel: asyncio.Event | None = None,
    json_progress: bool = False,
) -> None:
    """Convert one AVI to MP4, reporting progress as it goes.

    Raises :class:`EncodeCancelled` if ``cancel`` is set, and
    :class:`HandBrakeFailed` if HandBrake does not leave a usable file behind.
    """
    hb = binary or require_handbrake()
    if cancel is not None and cancel.is_set():
        raise EncodeCancelled(f"cancelled before starting: {source.name}")
    argv = build_argv(
        hb, source, dest, preset=preset, faststart=faststart, json_progress=json_progress
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    reader = ProgressReader()
    tail: list[str] = []
    keepalive = asyncio.get_running_loop().time()

    def note(text: str) -> None:
        tail.extend(text.splitlines()[-3:])
        del tail[:-40]

    try:
        assert process.stdout is not None
        while True:
            if cancel is not None and cancel.is_set():
                raise EncodeCancelled(f"cancelled: {source.name}")
            try:
                read = await asyncio.wait_for(process.stdout.read(4096), timeout=POLL_INTERVAL)
            except TimeoutError:
                read = None

            if read is None:
                now = asyncio.get_running_loop().time()
                if (
                    on_progress is not None
                    and reader.saw_progress
                    and now - keepalive >= KEEPALIVE_INTERVAL
                ):
                    on_progress(reader.latest)
                    keepalive = now
                if process.returncode is not None:
                    break
                continue

            if not read:
                break
            text = read.decode("utf-8", "ignore")
            note(text)
            parsed = reader.feed(text)
            if parsed is not None and on_progress is not None:
                on_progress(parsed)
                keepalive = asyncio.get_running_loop().time()

        await process.wait()
    except EncodeCancelled:
        await _terminate(process)
        dest.unlink(missing_ok=True)
        raise
    except asyncio.CancelledError:
        await _terminate(process)
        dest.unlink(missing_ok=True)
        raise
    finally:
        if process.returncode is None:
            await _terminate(process)

    if not dest.exists() or dest.stat().st_size == 0:
        detail = "\n".join(tail[-8:]) or f"exit code {process.returncode}"
        dest.unlink(missing_ok=True)
        raise HandBrakeFailed(f"HandBrakeCLI failed on {source.name}:\n{detail}")


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:  # pragma: no cover - stubborn child
        with contextlib.suppress(ProcessLookupError):
            process.kill()
