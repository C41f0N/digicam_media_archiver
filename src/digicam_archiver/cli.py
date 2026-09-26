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
        "--no-adopt",
        action="store_true",
        help=(
            "ignore what the archive already holds: every event gets a fresh "
            "folder and existing folders are asked about, the old behaviour"
        ),
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "never move files the archive already holds.  New files are still "
            "copied, since that only adds; with --print-plan it also exits 2 "
            "when the archive would have to change or a folder holds files the "
            "card does not account for"
        ),
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


def print_rerun(plan: Plan, archive: Path, stream=None) -> int:
    """Say what the archive already holds, and what a run would change.

    Returns the number of things worth stopping for, so ``--strict`` can turn
    them into an error instead of a question.
    """
    from .reconcile import build_index, reconcile
    from .restructure import describe, plan_restructure

    stream = sys.stdout if stream is None else stream
    if not archive.is_dir():
        print(f"{archive}: no such archive directory, everything is new", file=stream)
        return 0
    index = build_index(archive, plan)
    answer = reconcile(plan, index)
    print(f"archive {archive}: {len(index.folders)} event folder(s)", file=stream)
    for event in plan.events():
        archived = answer.of(event)
        state = archived.kind.value
        if archived.home is not None:
            state += f" {archived.home.name}"
        if archived.present:
            state += f", {len(archived.present)} of {len(event.files)} there"
        elif not archived.home:
            state += ", nothing there"
        print(f"  {event.taken_start:%a %d.%m.%Y %H:%M}  {state}", file=stream)
    for folder, names in sorted(answer.strangers_by_folder().items()):
        print(
            f"  ! {folder.name} has files no event accounts for: {', '.join(names)}",
            file=stream,
        )
    change = plan_restructure(plan, index, answer, archive)
    if change.is_empty:
        print("  the archive would stay as it is", file=stream)
    else:
        print("  the archive would change:", file=stream)
        for line in describe(change).splitlines():
            print(f"    {line}", file=stream)
    return len(answer.strangers) + (0 if change.is_empty else 1)


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
        if args.archive and not args.no_adopt:
            concerns = print_rerun(plan, args.archive)
            if args.strict and concerns:
                print("strict: the archive would have to change", file=sys.stderr)
                return 2
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
    if app.strict_refused:
        print(
            "strict: left the archive as it was, moved nothing",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
