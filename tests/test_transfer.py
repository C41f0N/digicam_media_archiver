from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from digicam_archiver.layout import TransferItem, build_items
from digicam_archiver.model import human_size
from digicam_archiver.partition import Plan
from digicam_archiver.scan import scan
from digicam_archiver.transfer import (
    NullReporter,
    Transfer,
    TransferCancelled,
    TransferJob,
    already_done,
    copy_photo,
    sha256_of,
)


def photo_item(source: Path, dest: Path) -> TransferItem:
    return TransferItem(source=source, dest=dest, kind="photo")


def video_item(source: Path, dest: Path) -> TransferItem:
    return TransferItem(source=source, dest=dest, kind="video")


class Recorder(NullReporter):
    def __init__(self) -> None:
        self.started: list[str] = []
        self.done: list[tuple[str, bool]] = []
        self.failed: list[str] = []
        self.messages: list[str] = []
        self.fractions: list[float] = []
        self.events: list[str] = []

    def item_start(self, item, index, total) -> None:
        self.started.append(item.source.name)

    def item_progress(self, item, done_units, total_units, progress) -> None:
        if total_units:
            self.fractions.append(done_units / total_units)

    def item_done(self, item, skipped) -> None:
        self.done.append((item.dest.name, skipped))

    def item_failed(self, item, error) -> None:
        self.failed.append(item.source.name)

    def log(self, message: str) -> None:
        self.messages.append(message)

    def event_start(self, index, total, dest_dir) -> None:
        self.events.append(dest_dir.name)


def test_copy_photo_keeps_the_bytes(tmp_path: Path) -> None:
    source = tmp_path / "a.jpg"
    source.write_bytes(b"0123456789" * 1000)
    dest = tmp_path / "out" / "a.jpg"
    copy_photo(source, dest)
    assert dest.read_bytes() == source.read_bytes()
    assert sha256_of(source) == sha256_of(dest)


def test_copy_photo_leaves_no_part_file(tmp_path: Path) -> None:
    source = tmp_path / "a.jpg"
    source.write_bytes(b"x")
    copy_photo(source, tmp_path / "a.jpg")
    assert list(tmp_path.iterdir()) == [source]


def test_copy_photo_stops_when_cancelled(tmp_path: Path) -> None:
    source = tmp_path / "big.bin"
    source.write_bytes(b"x" * (3 * 1024 * 1024))
    dest = tmp_path / "out" / "big.bin"
    cancel = asyncio.Event()
    cancel.set()

    with pytest.raises(TransferCancelled) as info:
        copy_photo(source, dest, None, cancel)
    assert "cancelled" in str(info.value)
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_copy_photo_stops_midway(tmp_path: Path) -> None:
    source = tmp_path / "big.bin"
    source.write_bytes(b"x" * (3 * 1024 * 1024))
    dest = tmp_path / "out" / "big.bin"
    cancel = asyncio.Event()

    def stop_after_first_chunk(done: int, _total: int) -> None:
        if done:
            cancel.set()

    with pytest.raises(TransferCancelled):
        copy_photo(source, dest, stop_after_first_chunk, cancel)
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_already_done_for_photos(tmp_path: Path) -> None:
    source = tmp_path / "card" / "a.jpg"
    source.parent.mkdir()
    source.write_bytes(b"12345")
    dest = tmp_path / "archive" / "a.jpg"
    dest.parent.mkdir()
    assert not already_done(photo_item(source, dest))
    dest.write_bytes(b"12345")
    assert already_done(photo_item(source, dest))
    dest.write_bytes(b"123")
    assert not already_done(photo_item(source, dest))


def test_already_done_for_videos_ignores_size(tmp_path: Path) -> None:
    """An mp4 is a different size from the avi by design."""
    source = tmp_path / "a.avi"
    source.write_bytes(b"x" * 100)
    dest = tmp_path / "a.mp4"
    assert not already_done(video_item(source, dest))
    dest.write_bytes(b"y" * 7)
    assert already_done(video_item(source, dest))


def test_already_done_checksum(tmp_path: Path) -> None:
    source = tmp_path / "card" / "a.jpg"
    source.parent.mkdir()
    source.write_bytes(b"hello")
    dest = tmp_path / "archive" / "a.jpg"
    dest.parent.mkdir()
    dest.write_bytes(b"hello")
    assert already_done(photo_item(source, dest), checksum=True)
    dest.write_bytes(b"other")
    assert not already_done(photo_item(source, dest), checksum=True)


def test_transfer_copies_photos(tmp_path: Path) -> None:
    source = tmp_path / "a.jpg"
    source.write_bytes(b"photo")
    dest_dir = tmp_path / "archive" / "26-08" / "26-08-23 One"
    job = TransferJob(
        label="One", dest_dir=dest_dir, items=(photo_item(source, dest_dir / "a.jpg"),)
    )
    reporter = Recorder()
    result = asyncio.run(Transfer([job], reporter=reporter).run())
    assert result.ok
    assert result.copied == 1
    assert (dest_dir / "a.jpg").read_bytes() == b"photo"
    assert reporter.started == ["a.jpg"]
    assert reporter.done == [("a.jpg", False)]
    assert reporter.events == ["26-08-23 One"]


def test_transfer_creates_the_event_folder(tmp_path: Path) -> None:
    source = tmp_path / "a.jpg"
    source.write_bytes(b"photo")
    dest = tmp_path / "deep" / "nest" / "a.jpg"
    asyncio.run(Transfer([TransferJob("One", dest.parent, (photo_item(source, dest),))]).run())
    assert dest.exists()


def test_transfer_converts_videos(tmp_path: Path, fake_handbrake: Path) -> None:
    source = tmp_path / "a.avi"
    source.write_bytes(b"avi")
    dest_dir = tmp_path / "archive"
    job = TransferJob(
        label="One",
        dest_dir=dest_dir,
        items=(video_item(source, dest_dir / "a.mp4"),),
    )
    reporter = Recorder()
    transfer = Transfer([job], reporter=reporter, handbrake=str(fake_handbrake))
    result = asyncio.run(transfer.run())
    assert result.ok
    assert (dest_dir / "a.mp4").exists()
    assert max(reporter.fractions) == 1.0
    assert transfer.videos_left == 0


def test_transfer_counts_videos_left(tmp_path: Path, fake_handbrake: Path) -> None:
    jobs = []
    for index in range(3):
        source = tmp_path / f"{index}.avi"
        source.write_bytes(b"avi")
        dest_dir = tmp_path / f"event{index}"
        jobs.append(
            TransferJob(
                label=str(index),
                dest_dir=dest_dir,
                items=(video_item(source, dest_dir / f"{index}.mp4"),),
            )
        )
    transfer = Transfer(jobs, handbrake=str(fake_handbrake))
    assert transfer.videos_total == 3
    assert transfer.videos_left == 3
    asyncio.run(transfer.run())
    assert transfer.videos_left == 0


def test_transfer_skips_what_is_already_there(tmp_path: Path, fake_handbrake: Path) -> None:
    source = tmp_path / "a.avi"
    source.write_bytes(b"avi")
    dest_dir = tmp_path / "archive"
    dest = dest_dir / "a.mp4"
    dest_dir.mkdir()
    dest.write_bytes(b"already converted")
    job = TransferJob("One", dest_dir, (video_item(source, dest),))
    reporter = Recorder()
    result = asyncio.run(
        Transfer([job], reporter=reporter, handbrake=str(fake_handbrake)).run()
    )
    assert result.ok
    assert result.skipped == 1
    assert result.copied == 0
    assert dest.read_bytes() == b"already converted"
    assert reporter.done == [("a.mp4", True)]


def test_transfer_overwrite_reconverts(tmp_path: Path, fake_handbrake: Path) -> None:
    source = tmp_path / "a.avi"
    source.write_bytes(b"avi")
    dest_dir = tmp_path / "archive"
    dest_dir.mkdir()
    dest = dest_dir / "a.mp4"
    dest.write_bytes(b"stale")
    job = TransferJob("One", dest_dir, (video_item(source, dest),))
    result = asyncio.run(Transfer([job], overwrite=True, handbrake=str(fake_handbrake)).run())
    assert result.copied == 1
    assert dest.read_bytes() != b"stale"


def test_transfer_records_failures_and_carries_on(
    tmp_path: Path, broken_handbrake: Path
) -> None:
    broken = tmp_path / "bad.avi"
    broken.write_bytes(b"avi")
    good = tmp_path / "good.jpg"
    good.write_bytes(b"jpg")
    dest_dir = tmp_path / "archive"
    job = TransferJob(
        label="One",
        dest_dir=dest_dir,
        items=(
            video_item(broken, dest_dir / "bad.mp4"),
            photo_item(good, dest_dir / "good.jpg"),
        ),
    )
    reporter = Recorder()
    result = asyncio.run(
        Transfer([job], reporter=reporter, handbrake=str(broken_handbrake)).run()
    )
    assert not result.ok
    assert result.failed == 1
    assert result.copied == 1
    assert reporter.failed == ["bad.avi"]
    assert (dest_dir / "good.jpg").exists()
    assert not (dest_dir / "bad.mp4").exists()


def test_transfer_stops_when_asked(tmp_path: Path, slow_handbrake: Path) -> None:
    jobs = []
    for index in range(4):
        source = tmp_path / f"{index}.avi"
        source.write_bytes(b"avi")
        dest_dir = tmp_path / f"event{index}"
        jobs.append(
            TransferJob(
                label=str(index),
                dest_dir=dest_dir,
                items=(video_item(source, dest_dir / f"{index}.mp4"),),
            )
        )
    transfer = Transfer(jobs, handbrake=str(slow_handbrake))

    async def scenario():
        async def stop_soon() -> None:
            await asyncio.sleep(0.2)
            transfer.cancel.set()

        stopper = asyncio.create_task(stop_soon())
        try:
            return await transfer.run()
        finally:
            await stopper

    result = asyncio.run(scenario())
    assert result.cancelled
    assert not result.ok
    done = list(jobs[0].dest_dir.glob("*.mp4"))
    assert len(done) <= 1


def test_transfer_checksum_mismatch_is_caught(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "a.jpg"
    source.write_bytes(b"photo")
    dest_dir = tmp_path / "archive"
    job = TransferJob(
        label="One", dest_dir=dest_dir, items=(photo_item(source, dest_dir / "a.jpg"),)
    )
    import digicam_archiver.transfer as transfer_module

    monkeypatch.setattr(
        transfer_module, "sha256_of", lambda path: f"hash of {path}", raising=True
    )
    result = asyncio.run(Transfer([job], checksum=True).run())
    assert result.failed == 1
    assert not (dest_dir / "a.jpg").exists()


def test_transfer_of_an_empty_plan(tmp_path: Path) -> None:
    result = asyncio.run(Transfer([]).run())
    assert result.ok
    assert result.copied == 0


def test_end_to_end_from_a_real_card(
    digicam: Path, archive: Path, fake_handbrake: Path
) -> None:
    """Scan, name, copy: the whole path with a real jpeg and a real avi."""
    plan = Plan(source=digicam, files=scan(digicam))
    events = plan.events()
    assert len(events) == 2
    plan.set_name(events[0], "Uni Friends Hangout")
    plan.set_name(events[1], "Second day")

    jobs = []
    for event in plan.included():
        target = archive / "26-08" / f"{event.taken_start:%y-%m-%d} {plan.name_of(event)}"
        jobs.append(
            TransferJob(
                label=plan.name_of(event) or "",
                dest_dir=target,
                items=tuple(build_items(target, event.files)),
            )
        )

    result = asyncio.run(Transfer(jobs, handbrake=str(fake_handbrake)).run())
    assert result.ok
    first = archive / "26-08" / "26-08-23 Uni Friends Hangout"
    assert sorted(p.name for p in first.iterdir()) == [
        "DSCF0001.JPG",
        "DSCF0001_1.JPG",
        "DSCF0002.JPG",
        "DSCF0003.mp4",
    ]
    second = archive / "26-08" / "26-08-24 Second day"
    assert [p.name for p in second.iterdir()] == ["DSCF0004.JPG"]
    # the card is left exactly as it was
    assert (digicam / "DCIM" / "103_FUJI" / "DSCF0003.AVI").exists()


def test_job_helpers(tmp_path: Path) -> None:
    source = tmp_path / "a.avi"
    source.write_bytes(b"x" * 50)
    item = video_item(source, tmp_path / "a.mp4")
    job = TransferJob("One", tmp_path, (item,))
    assert job.videos == (item,)
    assert job.bytes_expected == 50


def test_plan_and_layout_agree_on_collision_names(digicam: Path, tmp_path: Path) -> None:
    files = scan(digicam)
    same_name = [f for f in files if f.name == "DSCF0001.JPG"]
    assert len(same_name) == 2
    items = build_items(tmp_path, same_name)
    assert len({item.dest.name for item in items}) == 2
    assert human_size(sum(f.size for f in files)).endswith(("B", "KB", "MB"))
