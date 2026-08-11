#!/usr/bin/env python3
"""Bring an audio file into the project as a usable scene track.

Accepts a local file or a URL (fetched with yt-dlp). Normalises it to the
project's format, optionally trims it, drops it in tracks/, and prints the
scene block to paste into scenes/scenes.yaml — including a `pulse:` section
built from the onsets actually detected in the file.

    tools/import_track.py ~/Music/thing.wav --id organ_loop
    tools/import_track.py <url> --id chant --start 0:12 --take 20
    tools/import_track.py tracks/chant.mp3 --analyze-only

Flash is the hard constraint: everything the device plays lives in one 3.87 MB
app partition alongside ~1 MB of firmware. This script always tells you what a
track will cost before you commit to it.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import analyze as ana  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TRACKS = ROOT / "tracks"
BITRATE = 96          # matches hardware.audio.bitrate in scenes.yaml
BUDGET = 2.9 * 1024 * 1024


def secs(v: str) -> float:
    """Accept 12, 1:05 or 1:02:03."""
    parts = [float(p) for p in str(v).split(":")]
    out = 0.0
    for p in parts:
        out = out * 60 + p
    return out


def fetch_url(url: str, dest: Path) -> Path:
    if not shutil.which("yt-dlp"):
        raise SystemExit("yt-dlp not installed — `brew install yt-dlp`")
    print(f"fetching {url}")
    subprocess.run(
        ["yt-dlp", "-x", "--audio-format", "mp3", "--audio-quality", "0",
         "--no-playlist", "-o", str(dest / "%(title)s.%(ext)s"), url],
        check=True,
    )
    got = sorted(dest.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    if not got:
        raise SystemExit("yt-dlp produced no audio file")
    return got[-1]


def convert(src: Path, out: Path, start: float, take: float | None,
            bitrate: int = BITRATE) -> None:
    cmd = ["ffmpeg", "-v", "quiet", "-y"]
    if start:
        cmd += ["-ss", str(start)]
    cmd += ["-i", str(src)]
    if take:
        cmd += ["-t", str(take)]
    # Mono, 44.1 kHz. Mono is not a compromise here — there is one speaker.
    cmd += ["-ac", "1", "-ar", "44100", "-b:a", f"{bitrate}k", str(out)]
    subprocess.run(cmd, check=True)


def scene_block(tid: str, dur: float, marks: dict) -> str:
    """A ready-to-paste scene, wired to whatever the analyser actually found."""
    zones = {"onset_low": "door", "onset_mid": "towerL", "onset_high": "towerR"}
    colors = {
        "onset_low":  "[1.0, 0.12, 0.02, 0.0]",
        "onset_mid":  "[0.66, 0.10, 1.0, 0.05]",
        "onset_high": "[0.30, 1.0, 0.55, 0.0]",
    }
    decays = {"onset_low": 0.86, "onset_mid": 0.92, "onset_high": 0.94}
    lines = [
        f"  - id: {tid}",
        f"    name: {tid.replace('_', ' ').title()}",
        "    kind: custom",
        "    volume: 0.7",
        f"    duration_ms: {int(dur * 1000)}",
        "    loop: true",
        "    blurb: >",
        f"      Imported track {tid}. Light cues are onset-detected from the",
        "      audio itself, so they follow whatever the track actually does.",
        f"    audio_file: tracks/{tid}.mp3",
        "    base: {towerL: chill, towerR: chill, door: ember}",
        "    levels: {towerL: 0.4, towerR: 0.4, door: 0.5}",
        "    pulse:",
    ]
    for band, hits in marks.items():
        if not hits:
            continue
        z = zones.get(band, "door")
        lines.append(
            f"      - {{synth: {band}, zone: {z}, intensity: 0.55, "
            f"decay: {decays.get(band, 0.9)}, color: {colors.get(band, '[1,1,1,1]')}}}"
            f"   # {len(hits)} onsets"
        )
    lines.append("    cues: []")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", help="local audio file, or a URL for yt-dlp")
    ap.add_argument("--id", help="track id (default: sanitised filename)")
    ap.add_argument("--start", default="0", help="skip in, e.g. 0:12")
    ap.add_argument("--take", help="seconds to keep, e.g. 24")
    ap.add_argument("--sensitivity", type=float, default=1.1,
                    help="onset threshold; lower finds more (default 1.1)")
    ap.add_argument("--bitrate", type=int, default=BITRATE,
                    help=f"kbps, mono (default {BITRATE}, matching the flash "
                         "budget; raise it only if the audio is not going "
                         "into flash)")
    ap.add_argument("--analyze-only", action="store_true",
                    help="just report onsets, don't import")
    args = ap.parse_args()

    TRACKS.mkdir(exist_ok=True)

    if args.analyze_only:
        src = Path(args.source)
        marks = ana.analyze_file(src, sensitivity=args.sensitivity)
        dur = len(ana.load_audio(src)) / ana.SR
        for k, v in marks.items():
            print(f"  {k:<11} {len(v):>4} onsets")
        print(f"\n{scene_block(src.stem, dur, marks)}")
        return 0

    is_url = "://" in args.source
    tmp = TRACKS / "_incoming"
    tmp.mkdir(exist_ok=True)
    src = fetch_url(args.source, tmp) if is_url else Path(args.source)
    if not src.exists():
        raise SystemExit(f"no such file: {src}")

    tid = args.id or "".join(
        c if c.isalnum() else "_" for c in src.stem.lower()
    ).strip("_")[:32]
    out = TRACKS / f"{tid}.mp3"
    convert(src, out, secs(args.start),
            secs(args.take) if args.take else None, args.bitrate)
    if is_url:
        shutil.rmtree(tmp, ignore_errors=True)

    x = ana.load_audio(out)
    dur = len(x) / ana.SR
    size = out.stat().st_size
    marks = ana.analyze(x, sensitivity=args.sensitivity)

    print(f"\nimported  tracks/{tid}.mp3")
    print(f"  {dur:.1f}s   {size/1024:.0f} KB at {args.bitrate}kbps mono")
    print(f"  {size/BUDGET*100:.0f}% of the whole device audio budget "
          f"({BUDGET/1024/1024:.1f} MB for ALL scenes)")
    if size > BUDGET * 0.45:
        print("  ⚠ that is a big share — consider --take to trim it, or a "
              "shorter loop")
    print()
    for band, hits in marks.items():
        print(f"  {band:<11} {len(hits):>4} onsets ({len(hits)/dur*60:.0f}/min)")
    if not marks:
        print("  no onsets detected — try --sensitivity 0.6")

    print("\nPaste into scenes/scenes.yaml under `scenes:` —\n")
    print(scene_block(tid, dur, marks))
    print("\nthen:  make audio && make generate && make preview")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
