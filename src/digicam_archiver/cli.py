"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import timedelta
from pathlib import Path

from .handbrake import DEFAULT_PRESET, handbrake_binary
from .model import human_size
from .partition import DEFAULT_GAP, Plan
from .scan import scan

LOG_FORMAT = "%(levelname)s %(name)s: %(message)s"


def parse_gap(raw: str) -> timedelta:
    """``4h``, ``90m``, ``1h30m`` or a bare number of minutes."""
    text = raw.strip().lower()
    if not text:
        raise argparse.ArgumentTypeError("empty gap")
    try:
        if "h" in text and "m" in text:
            hours, minutes = text.split("h", 1)
            return timedelta(hours=int(hours), minutes=int(minutes.rstrip("m")))
        if text.endswith("h"):
            return timedelta(hours=int(text[:-1]))
        if text.endswith("m"):
            return timedelta(minutes=int(text[:-1]))
        return timedelta(minutes=int(text))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"cannot read {raw!r} as a gap, try 4h or 90m"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="digicam-archiver",
        description=(
            "Read a digicam folder, split the photos and videos into events by "
            "timestamp, name each event, then copy them into the archive, "
            "converting AVI to MP4 with HandBrake."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "keys in the first screen:\n"
            "  s split before the highlighted file   m merge with the next event\n"
            "  x skip this event                     [ ] shrink or grow the gap\n"
            "  g re-partition from timestamps alone  c colour or plain ascii\n"
            "  n go on to naming                     q quit\n"
            "\nthe archive gets one folder per month (26-08) and one per event\n"
            "inside it (26-08-23 Uni Friends Hangout)."
        ),
    )
    parser.add_argument(
        "source",
        type=Path,
        help="the digicam folder, or the card mount that contains DCIM",
    )
    parser.add_argument(
        "-a",
        "--archive",
        type=Path,
        help="where the archive lives, asked for later if left out",
    )
    parser.add_argument(
        "-g",
        "--gap",
        type=parse_gap,
        default=DEFAULT_GAP,
        metavar="DURATION",
        help="silence that starts a new event, default 4h (also [ and ] in the UI)",
    )
    parser.add_argument(
        "-p",
        "--preset",
        default=DEFAULT_PRESET,
        help=f"HandBrake preset for the video conversion, default {DEFAULT_PRESET!r}",
    )
    parser.add_argument(
        "--handbrake",
        type=Path,
        metavar="PATH",
        help="HandBrakeCLI to use, when it is not the one on PATH",
    )
    parser.add_argument(
        "--faststart",
        action="store_true",
        help="move the mp4 index to the front so it streams without a full download",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="convert and copy again even when the archive already has the file",
    )
    parser.add_argument(
        "--checksum",
        action="store_true",
        help="compare sha256 of every copied photo, slow but certain",
    )
    parser.add_argument(
        "--print-plan",
        action="store_true",
        help="print the partitioning as text and exit, no interface",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="go through the interface but stop before copying anything",
    )
    parser.add_argument(
        "--ascii",
        action="store_true",
        help="plain ascii preview instead of colour, for terminals without truecolor",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="log what the scanner is doing",
    )
    return parser


def check_source(source: Path) -> list:
    if not source.exists():
        raise SystemExit(f"{source}: no such directory")
    if not source.is_dir():
        raise SystemExit(f"{source}: not a directory")
    files = scan(source)
    if not files:
        raise SystemExit(
            f"{source}: no JPG or AVI files found"
            + ("" if (source / "DCIM").is_dir() else " (no DCIM folder either)")
        )
    return files


def print_plan(plan: Plan, stream=None) -> None:
    stream = sys.stdout if stream is None else stream
    photos = sum(1 for item in plan.files if item.kind == "photo")
    videos = len(plan.files) - photos
    print(
        f"{len(plan.files)} files from {plan.source} "
        f"({photos} photos, {videos} videos, {human_size(sum(f.size for f in plan.files))}) "
        f"-> {len(plan.events())} events",
        file=stream,
    )
    for line in plan.lines():
        print(line, file=stream)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING, format=LOG_FORMAT
    )

    files = check_source(args.source)
    plan = Plan(source=args.source, files=files, gap=args.gap)

    if args.print_plan:
        print_plan(plan)
        return 0

    from .ui.app import ArchiverApp

    if handbrake_binary() is None:
        print(
            "note: HandBrakeCLI not found, videos cannot be converted.\n"
            "      install it with: sudo pacman -S handbrake-cli",
            file=sys.stderr,
        )
    app = ArchiverApp(
        plan,
        archive=args.archive,
        opts=args,
        preview_colour=not args.ascii,
        dry_run=args.dry_run,
    )
    app.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
