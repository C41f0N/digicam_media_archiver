# digicam-media-archiver

Read a digicam card (or a folder of JPG and AVI dumps), work out which shots
belong together, name each group, then copy them into an archive and turn the
AVI clips into MP4 with HandBrake.

Nothing on the card is ever moved, renamed or deleted. Everything the tool
produces goes into a new file in the archive, and a run that stops half way can
be repeated: files that are already in place are skipped.

## What you need

- Python 3.11 or newer
- `ffmpeg` and `ffprobe` on `PATH` for the video previews (photos need neither)
- `HandBrakeCLI` for the conversion, optional but you will want it:
  `sudo pacman -S handbrake-cli`

## Install

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

## Run

```sh
.venv/bin/digicam-archiver ~/pics/card-2026-08-23
```

The source can be the card mount that holds `DCIM`, or any folder inside it.
To see the grouping without touching anything:

```sh
.venv/bin/digicam-archiver ~/pics/card-2026-08-23 --print-plan
```

Useful flags:

| flag | what it does |
| --- | --- |
| `-a, --archive DIR` | where the archive lives, asked for later if left out |
| `-g, --gap 4h` | silence that starts a new event, `90m` and `1h30m` work too |
| `-p, --preset NAME` | HandBrake preset, default `Fast 720p30` |
| `--handbrake PATH` | use a HandBrakeCLI that is not on `PATH` |
| `--faststart` | move the mp4 index to the front so it streams |
| `--overwrite` | copy and convert again even when the file is already there |
| `--checksum` | compare sha256 of every copied photo, slow but certain |
| `--print-plan` | print the partitioning and exit |
| `--dry-run` | walk through the interface, stop before copying |
| `--ascii` | plain preview instead of colour, for dull terminals |

## The three screens

### 1. Events

The left side lists the events the tool worked out, one line each, with the
file count, the size and any name you already gave it. The right side shows the
highlighted shot as ASCII art, or a still from the video. Videos are drawn from
a frame about a fifth of the way in.

The preview decodes in the background, one shot at a time, and a shot that has
moved off the screen before it was finished is thrown away, so holding an arrow
key never queues up work. The next event's first shot is warmed up while you
read the current one, which is why moving down the list feels instant.

| key | action |
| --- | --- |
| `up` `down` / `k` `j` | move |
| `right` / `left` | open an event to see its files, or close it again |
| `s` | split before the highlighted file |
| `m` | merge this event with the next one |
| `x` | leave this event out of the run, press again to put it back |
| `[` `]` | shorter or longer gap, 15 minutes at a time |
| `g` | forget the manual splits and regroup with the current gap |
| `c` | colour or plain preview |
| `n` | go on to naming |
| `q` | quit |

`g` throws away the manual calls, so change the gap first and press `g` when a
card needs a different shape.

### 2. Names

Every event needs a name before anything is copied. The field is prefilled with
the date, so typing over it is one keystroke.

| key | action |
| --- | --- |
| `alt+down` / `alt+up` | next or previous event (`ctrl+n` also goes down) |
| `alt+left` / `alt+right` | older or newer photo in this event |
| `pageup` / `pagedown` | the same, for keyboards without working alt keys |
| `alt+home` / `alt+end` | first or last photo of the event |
| `ctrl+s` | save the name you typed |
| `ctrl+t` | start the copy |
| `escape` | back to the events |
| `q` | quit |

The photo keys walk through every file of the highlighted event, videos
included, so you can check what actually landed in a group before naming it. The
line under the field shows the folder the event will land in, and the second
line of the preview shows which photo you are on, like `3/42`. A second event
with the same date gets its own folder, never a merge.

`ctrl+p` is the command palette, so it is not a shortcut here.

### 3. Copy and convert

| key | action |
| --- | --- |
| `escape` | stop after the file in flight, then `r` to finish the rest |
| `r` | run again, for the files left behind |
| `q` | quit |

Photos are copied in 1 MB chunks through a `.part` file, so an interrupted copy
never leaves a half written photo in the archive. Videos are converted to MP4
with HandBrake, which is slow, so the screen shows a per file bar, an overall
bar, the remaining time and a log of what happened.

If HandBrakeCLI is missing the run still goes ahead, the photos are copied and
the videos are counted as left for later.

When an event folder is already in the archive the run asks: add to it, skip the
event, give it a new name, or cancel the run. Files that are already there are
skipped unless `--overwrite` is given, and a name clash gets a `_1`, `_2` suffix
so nothing is overwritten by accident.

## How the timestamps work

- JPG: `DateTimeOriginal` from the EXIF block, the file's mtime when it has none
- AVI: the file's mtime, that is when the digicam wrote it
- events: a file that starts more than `--gap` after the one before it opens a
  new event

All times are local camera time, no timezone is assumed.

## The archive

```
archive/26-08/26-08-23 Uni Friends Hangout/DSCF0001.JPG
archive/26-08/26-08-23 Uni Friends Hangout/DSCF0001_1.JPG
archive/26-08/26-08-23 Uni Friends Hangout/DSCF0003.mp4
```

The month and the day come from the first shot of the event, so a night out that
runs past midnight stays together. The names from two folders that held the same
camera filenames (`103_FUJI` and `104_FUJI`, say) get a suffix instead of
overwriting each other. `.THM` thumbnails and other formats are ignored.

## Tests

```sh
.venv/bin/python -m pytest            # everything
.venv/bin/python -m pytest -m "not ui" # skip the headless interface tests
.venv/bin/ruff check src tests
```

The tests write real JPEGs and a real little AVI with ffmpeg into a temporary
folder, and fake `HandBrakeCLI` scripts stand in for the encoder, so nothing
outside `tmp_path` is touched and no encoder is needed. The `ui` marked tests
drive the Textual interface headless with a virtual keyboard, cursor and all.
