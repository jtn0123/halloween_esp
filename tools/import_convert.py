"""The ffmpeg half of an import: convert, probe, keep the source.

Split from import_track.py at the 500-line cap along the seam that was
already there: everything in this module shells out to ffmpeg/ffprobe or
files the source copy away — nothing here parses arguments, reads the
manifest or prints the summary. import_track re-exports these names, so
`it.convert(...)` in the tests and codec_compare still means what it did.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from studio_tracks import SRC_DIR

ROOT = Path(__file__).resolve().parent.parent
# Same override as import_track/manifest: the sandbox env names the library.
TRACKS = Path(os.environ.get("CASTLE_TRACKS") or (ROOT / "tracks"))


def convert(src: Path, out: Path, o: dict[str, Any]) -> None:
    """One ffmpeg pass: trim, filter, downmix, resample, encode."""
    cmd = ["ffmpeg", "-v", "quiet", "-y"]
    if o["start"]:
        cmd += ["-ss", str(o["start"])]
    cmd += ["-i", str(src)]
    if o["take"]:
        cmd += ["-t", str(o["take"])]

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
    # 0.89 matches TARGET_PEAK in render_audio.py, so an imported track and a
    # synthesised scene arrive at the mixer with the same headroom.
    af.append("alimiter=limit=0.89:level=disabled")

    cmd += ["-af", ",".join(af)]

    fmt = o.get("format", "mp3")
    rate = o["sample_rate"]
    if fmt == "opus" and rate not in (8000, 12000, 16000, 24000, 48000):
        # Opus only encodes at those rates; anything else fails outright
        # rather than resampling for you. 48k is the nearest sane landing
        # spot from 44.1k, and the device resamples on playback anyway.
        print(f"  note: opus cannot encode at {rate} Hz — using 48000")
        rate = 48000
    cmd += ["-ac", str(o["channels"]), "-ar", str(rate)]

    # Codec per container. WAV and FLAC have no bitrate to set — passing one
    # makes ffmpeg complain rather than quietly ignore it.
    if fmt == "wav":
        cmd += ["-c:a", "pcm_s16le"]
    elif fmt == "flac":
        cmd += ["-c:a", "flac"]
    elif fmt == "opus":
        cmd += ["-c:a", "libopus", "-b:a", f"{o['bitrate']}k"]
    else:
        cmd += ["-b:a", f"{o['bitrate']}k"]

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
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=300
        )
    except subprocess.TimeoutExpired:
        part.unlink(missing_ok=True)
        raise SystemExit(
            f"ffmpeg stalled encoding {out.name} — gave up after 5 minutes"
        ) from None
    if r.returncode != 0 or not part.exists() or part.stat().st_size == 0:
        part.unlink(missing_ok=True)
        # ffmpeg's own last line names the actual problem; a traceback does not.
        tail = [ln for ln in (r.stderr or "").splitlines() if ln.strip()]
        raise SystemExit(
            f"{src.name} doesn't look like playable audio — "
            f"ffmpeg could not convert it "
            f"({tail[-1] if tail else f'exit {r.returncode}'})"
        )
    os.replace(part, out)


def probe_duration(src: Path) -> float | None:
    """The source's length in seconds by ffprobe, or None if it cannot say."""
    try:
        r = subprocess.run(
            [
                "ffprobe",
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
            check=False,
            timeout=60,
        )
        return float(r.stdout.strip()) if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def keep_source(src: Path, tid: str) -> Path:
    """Copy a throwaway local source to tracks/_src/<tid><ext>, so a later
    --refresh has something to rebuild from. Already there: left alone."""
    kept_dir = TRACKS / SRC_DIR
    kept_dir.mkdir(parents=True, exist_ok=True)
    kept = kept_dir / f"{tid}{src.suffix.lower()}"
    if src.resolve() != kept.resolve():
        for old in kept_dir.glob(f"{tid}.*"):
            old.unlink()
        shutil.copy2(src, kept)
    return kept.resolve()


def _same_file(a: Path, b: Path) -> bool:
    """Do the two paths name one file on disk? (Absent paths never do.)"""
    try:
        return a.exists() and b.exists() and a.samefile(b)
    except OSError:
        return False
