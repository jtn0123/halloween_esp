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



sys.path.insert(0, str(Path(__file__).parent))
import analyze as ana  # noqa: E402
import manifest as mf  # noqa: E402

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


def fetch_url(url: str, dest: Path) -> tuple[Path, str]:
    """Download audio only. Returns (file, title as the source named it)."""
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
    return got[-1], got[-1].stem


def convert(src: Path, out: Path, o: dict) -> None:
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
        af.append(f"afade=t=out:st={max(0, o['take'] - o['fade_out'])}:d={o['fade_out']}")

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

    cmd += ["-ac", str(o["channels"]), "-ar", str(o["sample_rate"]),
            "-b:a", f"{o['bitrate']}k", str(out)]
    subprocess.run(cmd, check=True)


FRAME = 0.016          # the light engine's tick, matching the firmware


def fit_to_density(hits: list, fallback: float) -> tuple[float, float]:
    """Choose a decay and an intensity scale that suit how busy a band is.

    The built-in scenes' decay constants were tuned against Crypt's 48 bpm
    heartbeat — gaps of 1.25 s. Reuse them on a track that hits every 0.24 s
    and the flash is still at ~29% when the next one lands: the zone saturates
    and reads as a continuous smear rather than as pulses. Which is precisely
    the way an imported track stops "making sense".

    So the decay is solved from the material: fall to ~10% by the time the
    next hit is due. And dense bands get their intensity pulled down, because
    a lot of overlapping pulses sum to a floor that never returns to dark.

    Returns (decay_per_frame, intensity_scale).
    """
    if len(hits) < 3:
        return fallback, 1.0

    gaps = sorted(hits[i + 1][0] - hits[i][0] for i in range(len(hits) - 1))
    median_gap = gaps[len(gaps) // 2]
    if median_gap <= 0:
        return fallback, 1.0

    # d^frames = 0.1  ->  d = 0.1 ** (1/frames)
    frames = max(1.0, median_gap / FRAME)
    decay = 0.1 ** (1.0 / frames)
    # Floor at 0.78: faster than that is a single-frame blink nobody sees.
    # Ceiling at the scene's own value, so sparse material keeps its bloom.
    decay = max(0.78, min(fallback, decay))

    # Below ~0.5 s between hits, back the level off so they stay distinct.
    scale = 1.0 if median_gap >= 0.5 else max(0.45, median_gap / 0.5)
    return round(decay, 3), round(scale, 2)


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
        decay, scale = fit_to_density(hits, decays.get(band, 0.9))
        intensity = round(0.55 * scale, 3)
        rate = len(hits) / dur * 60 if dur else 0
        note = f"{len(hits)} onsets, {rate:.0f}/min"
        if scale < 1.0:
            note += " — dense, so eased back to stay distinct"
        lines.append(
            f"      - {{synth: {band}, zone: {z}, intensity: {intensity}, "
            f"decay: {decay}, color: {colors.get(band, '[1,1,1,1]')}}}"
            f"   # {note}"
        )
    lines.append("    cues: []")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Import audio into tracks/, remembering where it came from.")
    ap.add_argument("source", nargs="?",
                    help="local audio file, or a URL for yt-dlp. Omit with "
                         "--refresh to reuse the remembered source.")
    ap.add_argument("--id", help="track id (default: sanitised filename)")
    ap.add_argument("--refresh", metavar="ID",
                    help="rebuild an existing track from its remembered "
                         "source; any option you pass overrides what was "
                         "used last time")
    ap.add_argument("--list", action="store_true", help="show imported tracks")
    ap.add_argument("--notes", default="", help="free-text note on the track")

    g = ap.add_argument_group("trim")
    g.add_argument("--start", help="skip in, e.g. 0:12")
    g.add_argument("--take", help="seconds to keep, e.g. 24")
    g.add_argument("--fade-in", type=float, help="seconds of fade at the head")
    g.add_argument("--fade-out", type=float, help="seconds of fade at the tail")

    g = ap.add_argument_group("format")
    g.add_argument("--bitrate", type=int,
                   help=f"kbps (default {BITRATE}, matching the flash budget)")
    g.add_argument("--channels", type=int, choices=(1, 2),
                   help="1 = mono (default). 2 needs a second amp on the "
                        "hardware; see PROJECT_NOTES §12.10")
    g.add_argument("--sample-rate", type=int,
                   help="Hz (default 44100). 22050 halves the data rate and "
                        "is plenty for atmospheres")
    g.add_argument("--normalize", action="store_true",
                   help="EBU R128 loudness match, so imports sit level with "
                        "the generated scenes")
    g.add_argument("--gain-db", type=float, help="flat gain adjustment in dB")

    g = ap.add_argument_group("analysis")
    g.add_argument("--sensitivity", type=float,
                   help="onset threshold; lower finds more (default 1.1)")

    ap.add_argument("--analyze-only", action="store_true",
                    help="just report onsets, don't import")
    args = ap.parse_args()

    TRACKS.mkdir(exist_ok=True)

    if args.list:
        data = mf.load()
        if not data:
            print("no tracks imported yet")
            return 0
        for tid, e in sorted(data.items()):
            a = e.get("audio", {})
            print(f"{tid:<20} {a.get('duration', 0):>6.1f}s "
                  f"{a.get('bytes', 0)/1024:>7.0f}K  {e.get('source', '')[:60]}")
        return 0

    if args.analyze_only:
        src = Path(args.source)
        marks = ana.analyze_file(src, sensitivity=args.sensitivity or 1.1)
        dur = len(ana.load_audio(src)) / ana.SR
        for k, v in marks.items():
            print(f"  {k:<11} {len(v):>4} onsets")
        print(f"\n{scene_block(src.stem, dur, marks)}")
        return 0

    # Options: remembered defaults, overridden by whatever was passed now.
    prev = mf.get(args.refresh) if args.refresh else None
    if args.refresh and prev is None:
        raise SystemExit(f"no remembered track {args.refresh!r} "
                         f"(tools/import_track.py --list)")
    base = dict(prev.get("opts", {})) if prev else {}
    o = {
        "start": base.get("start", "0"),
        "take": base.get("take"),
        "fade_in": base.get("fade_in"),
        "fade_out": base.get("fade_out"),
        "bitrate": base.get("bitrate", BITRATE),
        "channels": base.get("channels", 1),
        "sample_rate": base.get("sample_rate", 44100),
        "normalize": base.get("normalize", False),
        "gain_db": base.get("gain_db"),
        "sensitivity": base.get("sensitivity", 1.1),
    }
    for k in list(o):
        v = getattr(args, k, None)
        if v not in (None, False):
            o[k] = v

    source = args.source or (prev or {}).get("source", "")
    if not source:
        raise SystemExit("need a source (file or URL)")
    if source.startswith("file:"):
        source = source[5:]
    is_url = "://" in source

    tmp = TRACKS / "_incoming"
    tmp.mkdir(exist_ok=True)
    title = (prev or {}).get("title", "")
    if is_url:
        src, title = fetch_url(source, tmp)
    else:
        src = Path(source)
    if not src.exists():
        raise SystemExit(f"no such file: {src}")

    tid = args.refresh or args.id or "".join(
        c if c.isalnum() else "_" for c in src.stem.lower()
    ).strip("_")[:32]

    out = TRACKS / f"{tid}.mp3"
    conv = dict(o)
    conv["start"] = secs(o["start"]) if o["start"] else 0
    conv["take"] = secs(o["take"]) if o["take"] else None
    convert(src, out, conv)
    shutil.rmtree(tmp, ignore_errors=True)

    x = ana.load_audio(out)
    dur = len(x) / ana.SR
    size = out.stat().st_size
    marks = ana.analyze(x, sensitivity=float(o["sensitivity"]))

    mf.record(
        tid,
        source=source if is_url else f"file:{Path(source).resolve()}",
        title=title, opts=o, notes=args.notes,
        audio={"duration": round(dur, 2), "bytes": size,
               "channels": o["channels"], "sample_rate": o["sample_rate"],
               "bitrate": o["bitrate"]},
        onsets={k: len(v) for k, v in marks.items()},
    )

    ch = "mono" if o["channels"] == 1 else "stereo"
    print(f"\nimported  tracks/{tid}.mp3")
    print(f"  {dur:.1f}s   {size/1024:.0f} KB at {o['bitrate']}kbps {ch} "
          f"{o['sample_rate']}Hz")
    print(f"  source remembered — rebuild any time with: "
          f"tools/import_track.py --refresh {tid}")
    print(f"  {size/BUDGET*100:.0f}% of the flash audio budget "
          f"({BUDGET/1024/1024:.1f} MB for ALL scenes)")
    if size > BUDGET * 0.45:
        print("  ⚠ that is a big share — trim it with --take, or drop "
              "--bitrate / --sample-rate")
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
