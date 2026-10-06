"""The ffmpeg half of an import: convert, probe, keep the source.

Split from import_track.py at the 500-line cap along the seam that was
already there: everything in this module shells out to ffmpeg/ffprobe or
files the source copy away — nothing here parses arguments, reads the
manifest or prints the summary. import_track re-exports these names, so
`it.convert(...)` in the tests and codec_compare still means what it did.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import exe_paths
import import_reason as ir
import portable_fs
from track_lib import SRC_DIR, TRACKS

ROOT = Path(__file__).resolve().parent.parent
# Same override as import_track/manifest: the sandbox env names the library.

#: The longest cut an import keeps. Not a format limit — the onset analysis
#: holds the whole song in memory, and measured on 2026-10-02 it peaks at
#: about 250 MB per minute of audio (castle-core analyze_track: 30 min →
#: 7.5 GB, 2 h → 24.6 GB). Fifteen minutes is ~3.8 GB, which an 8 GB
#: laptop survives; a 2-hour file is refused before anything is converted,
#: with a sentence, instead of swapping the machine to a halt. A longer
#: source still imports when a start and length pick the part to keep.
#: desktop/README.md ("What an import can take") says it for the owner.
MAX_IMPORT_SECONDS = 15 * 60


def longest() -> str:
    """The limit as a person says it: "15 minutes"."""
    return f"{MAX_IMPORT_SECONDS // 60} minutes"


def clock(seconds: float) -> str:
    """1:02:03 / 4:05 — a length as a player shows it."""
    s = round(seconds)
    h, m = divmod(s // 60, 60)
    return f"{h}:{m:02d}:{s % 60:02d}" if h else f"{m}:{s % 60:02d}"


def too_long(name: str, seconds: float) -> str:
    return (
        f"{name} is {clock(seconds)} long, and Castle Tools imports up to "
        f"{longest()} — set a start and length to import just part of it, "
        "or choose a shorter song."
    )


def not_audio(name: str) -> str:
    return (
        f"{name} does not look like playable audio — choose an MP3, WAV, "
        "FLAC, M4A or OGG file instead."
    )


#: ffmpeg's context tag, "[out#0/mp3 @ 0x7f…] ", which would otherwise read
#: as a progress line and hide the message after it from the reason scan.
_FFMPEG_TAG = re.compile(r"^\[[^\]]* @ 0x[0-9a-fA-F]+\][ \t]*", re.MULTILINE)


def detail(lines: list[str]) -> None:
    """A tool's own words, indented under the sentence (import_fetch.detail)."""
    for ln in lines:
        print("    " + ln.rstrip(), file=sys.stderr)


def _filters(o: dict[str, Any]) -> list[str]:
    """The -af chain: what the import options asked for, then the ceiling."""
    af = []
    if o["normalize"]:
        # EBU R128 to -16 LUFS. Scene `volume` still sets relative level; this
        # just stops one imported track being wildly louder than the rest.
        af.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    if o["gain_db"]:
        af.append(f"volume={o['gain_db']}dB")
    if o["fade_in"]:
        af.append(f"afade=t=in:st=0:d={o['fade_in']}")
    if o["fade_out"] and o["take"]:
        af.append(
            f"afade=t=out:st={max(0, o['take'] - o['fade_out'])}:d={o['fade_out']}"
        )

    # Final true-peak ceiling, always. Two things make this necessary:
    #
    #   - MP3 encoding overshoots its input. A source mastered near 0 dBFS
    #     decodes ABOVE full scale; measured +2.77 dBFS on a square wave and
    #     +0.92 on dense material. That eats the margin the scene mixer needs.
    #   - `loudnorm` alone does not save us. Single-pass, it is a dynamic
    #     normaliser, and its TP target is a goal rather than a guarantee — a
    #     real YouTube import still came back at +0.18 dBFS with it enabled.
    #
    # 0.89 matches TARGET_PEAK in render_score.py, so an imported track and a
    # synthesised scene arrive at the mixer with the same headroom.
    af.append("alimiter=limit=0.89:level=disabled")
    return af


def _codec(fmt: str, o: dict[str, Any]) -> list[str]:
    """Downmix, sample rate and codec for one container. WAV and FLAC have no
    bitrate to set — passing one makes ffmpeg complain rather than quietly
    ignore it."""
    rate = o["sample_rate"]
    if fmt == "opus" and rate not in (8000, 12000, 16000, 24000, 48000):
        # Opus only encodes at those rates; anything else fails outright
        # rather than resampling for you. 48k is the nearest sane landing
        # spot from 44.1k, and the device resamples on playback anyway.
        print(f"  note: opus cannot encode at {rate} Hz — using 48000")
        rate = 48000
    args = ["-ac", str(o["channels"]), "-ar", str(rate)]
    if fmt == "wav":
        return [*args, "-c:a", "pcm_s16le"]
    if fmt == "flac":
        # A fixed 4096-sample block, said out loud. Left alone, ffmpeg 9 takes
        # the block size from the first frame its decoder hands over, and an
        # MP3's first frame is what gapless trimming leaves of 1152 — 47
        # samples. A FLAC of 47-sample blocks is twice the size, and Safari's
        # decoder will not play it at all: the codec A/B sat "playing" there
        # at a frozen position, without a sound.
        return [*args, "-c:a", "flac", "-frame_size", "4096"]
    if fmt == "opus":
        return [*args, "-c:a", "libopus", "-b:a", f"{o['bitrate']}k"]
    return [*args, "-b:a", f"{o['bitrate']}k"]


def _encode(cmd: list[str], src: Path, out: Path, part: Path) -> None:
    """Run the pass, then move the part file over the destination — the last
    step, so a failure leaves whatever was already there untouched."""
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        part.unlink(missing_ok=True)
        raise SystemExit(
            f"Converting {src.name} took too long and was stopped — try a "
            "shorter song, or a different copy of this one."
        ) from None
    if r.returncode != 0 or not part.exists() or part.stat().st_size == 0:
        part.unlink(missing_ok=True)
        # ffmpeg's own words go in the log; the owner reads what they mean
        # — a full disk or a file it may not read is not "not audio".
        tail = [ln for ln in (r.stderr or "").splitlines() if ln.strip()]
        detail(tail[-6:] or [f"ffmpeg exited {r.returncode}"])
        said = ir.recognised(_FFMPEG_TAG.sub("", r.stderr or ""))
        raise SystemExit(said or not_audio(src.name))
    portable_fs.replace(part, out)


def convert(src: Path, out: Path, o: dict[str, Any]) -> None:
    """One ffmpeg pass: trim, filter, downmix, resample, encode."""
    # `error`, not `quiet`: when a conversion fails, ffmpeg's reason is the
    # detail the log keeps — and the full disk the owner is told about.
    cmd = [exe_paths.ffmpeg(), "-v", "error", "-y"]
    if o["start"]:
        cmd += ["-ss", str(o["start"])]
    cmd += ["-i", str(src)]
    if o["take"]:
        cmd += ["-t", str(o["take"])]
    cmd += ["-af", ",".join(_filters(o))]
    fmt = o.get("format", "mp3")
    cmd += _codec(fmt, o)

    # Encode BESIDE the destination, then rename: ffmpeg opens its output
    # before it knows the input is garbage, so a failed import used to leave
    # a 0-byte track that the desk then offered to send to the castle — and
    # a failed re-import truncated the good copy it was meant to replace.
    part = out.with_name(out.name + ".part")
    # ffmpeg picks the muxer from the extension, and ".part" is not one.
    cmd += [
        "-f",
        {"wav": "wav", "flac": "flac", "opus": "opus"}.get(fmt, "mp3"),
        str(part),
    ]
    _encode(cmd, src, out, part)


def probe_duration(src: Path) -> float | None:
    """The source's length in seconds by ffprobe, or None if it cannot say."""
    try:
        r = subprocess.run(
            [
                exe_paths.ffprobe(),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(src),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=60,
        )
        return float(r.stdout.strip()) if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _unlink(path: Path) -> None:
    """Delete a file even when it is read-only: Windows refuses that, and a
    copy kept by an older importer (copy2 carried the source's read-only
    bit across from a CD or a share) would otherwise block every re-import."""
    try:
        path.unlink()
    except PermissionError:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        path.unlink()


def keep_source(src: Path, tid: str) -> Path:
    """Copy a throwaway local source to tracks/_src/<tid><ext>, so a later
    --refresh has something to rebuild from. Already there: left alone.

    The bytes and nothing else: a song on a read-only share or a CD would
    hand its read-only bit to the copy, which Windows will then not let the
    library replace or delete."""
    kept_dir = TRACKS / SRC_DIR
    kept_dir.mkdir(parents=True, exist_ok=True)
    kept = kept_dir / f"{tid}{src.suffix.lower()}"
    if src.resolve() != kept.resolve():
        for old in kept_dir.glob(f"{tid}.*"):
            _unlink(old)
        shutil.copyfile(src, kept)
    return kept.resolve()


def _same_file(a: Path, b: Path) -> bool:
    """Do the two paths name one file on disk? (Absent paths never do.)"""
    try:
        return a.exists() and b.exists() and a.samefile(b)
    except OSError:
        return False
