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
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
import analyze as ana
import manifest as mf

ROOT = Path(__file__).resolve().parent.parent
# CASTLE_TRACKS is the whole sandbox story (see playwright.config.ts): the
# studio honored it but this subprocess wrote to the real tracks/ anyway —
# an e2e import quietly landed files in (or over!) the user's library.
TRACKS = Path(os.environ.get("CASTLE_TRACKS") or (ROOT / "tracks"))
BITRATE = 96          # matches hardware.audio.bitrate in scenes.yaml
BUDGET = 2.9 * 1024 * 1024


def secs(v: str) -> float:
    """Accept 12, 1:05 or 1:02:03."""
    parts = [float(p) for p in str(v).split(":")]
    out = 0.0
    for p in parts:
        out = out * 60 + p
    return out


def _ytdlp() -> str:
    """The venv's yt-dlp when present, else the system one.

    YouTube deliberately breaks stale clients (403s, SABR-only sessions), and
    Homebrew's formula trails releases by weeks — pip does not. So the venv
    copy, updated with `pip install -U yt-dlp`, wins when it exists.
    """
    local = Path(sys.executable).with_name("yt-dlp")
    if local.exists():
        return str(local)
    if not shutil.which("yt-dlp"):
        raise SystemExit("yt-dlp not installed — `brew install yt-dlp`")
    return "yt-dlp"


def fetch_url(url: str, dest: Path) -> tuple[Path, str]:
    """Download audio only. Returns (file, title as the source named it)."""
    print(f"fetching {url}")
    try:
        r = subprocess.run(
            [_ytdlp(), "-x", "--audio-format", "mp3", "--audio-quality", "0",
             "--no-playlist", "-o", str(dest / "%(title)s.%(ext)s"), url],
            capture_output=True, text=True, check=False,  # handled below
            timeout=900,   # a hung download must not wedge the studio's lock
        )
    except subprocess.TimeoutExpired:
        raise SystemExit("gave up after 15 minutes — the download stalled. "
                         "Try the link again, or a different source.") from None
    if r.returncode != 0:
        # yt-dlp's own last lines say WHY ("Video unavailable", a bot check…).
        # check=True here dumped a raw CalledProcessError traceback into the
        # studio's red banner, which no one can act on (round-3 user test).
        tail = [ln for ln in (r.stderr or r.stdout or "").splitlines()
                if ln.strip()][-3:]
        raise SystemExit("could not fetch that link:\n" + "\n".join(tail))
    got = sorted(dest.glob("*.mp3"), key=lambda p: p.stat().st_mtime)
    if not got:
        raise SystemExit("yt-dlp produced no audio file")
    return got[-1], got[-1].stem


def sensitivity_arg(raw: str):
    """`1.1`, or `low=0.8,mid=1.1,high=1.6`.

    One number for all three bands is usually the wrong answer — a crisp kick
    and a wash of cymbals want different thresholds — but it is the right
    default, so both spellings are accepted and a bare number still means
    "the same everywhere".
    """
    if "=" not in raw:
        try:
            return float(raw)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a number: {raw!r}") from None
    out = {}
    for part in raw.split(","):
        k, _, v = part.partition("=")
        k = k.strip().replace("onset_", "")
        if k not in ("low", "mid", "high"):
            raise argparse.ArgumentTypeError(
                f"unknown band {k!r} — expected low, mid or high")
        try:
            out[f"onset_{k}"] = float(v)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"not a number for {k}: {v!r}") from None
    return out


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
    cmd += ["-f", {"wav": "wav", "flac": "flac", "opus": "opus"}.get(fmt, "mp3"),
            str(part)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, check=False,
                           timeout=300)
    except subprocess.TimeoutExpired:
        part.unlink(missing_ok=True)
        raise SystemExit(f"ffmpeg stalled encoding {out.name} — "
                         "gave up after 5 minutes") from None
    if r.returncode != 0 or not part.exists() or part.stat().st_size == 0:
        part.unlink(missing_ok=True)
        # ffmpeg's own last line names the actual problem; a traceback does not.
        tail = [ln for ln in (r.stderr or "").splitlines() if ln.strip()]
        raise SystemExit(f"{src.name} doesn't look like playable audio — "
                         f"ffmpeg could not convert it "
                         f"({tail[-1] if tail else f'exit {r.returncode}'})")
    os.replace(part, out)


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


def scene_block(tid: str, dur: float, marks: dict, ext: str = "mp3") -> str:
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
        f"    audio_file: tracks/{tid}.{ext}",
        "    base: {towerL: chill, towerR: chill, door: ember}",
        "    levels: {towerL: 0.4, towerR: 0.4, door: 0.5}",
        "    pulse:",
    ]
    for band, hits in marks.items():
        if not hits:
            continue
        base = band.replace("level_", "onset_")
        z = zones.get(base, "door")
        col = colors.get(base, "[1,1,1,1]")

        if band.startswith("level_"):
            # An envelope wants to GLIDE, not pulse. Its samples arrive at a
            # steady 6 Hz, so a decay that empties between them would chop a
            # smooth swell into a stutter. 0.90 leaves roughly half the level
            # standing when the next sample lands, which reads as breathing.
            lines.append(
                f"      - {{synth: {band}, zone: {z}, intensity: 0.5, "
                f"decay: 0.90, color: {col}}}"
                f"   # {len(hits)} level samples — no beat here, so the zone "
                f"follows loudness instead"
            )
            continue

        decay, scale = fit_to_density(hits, decays.get(band, 0.9))
        intensity = round(0.55 * scale, 3)
        rate = len(hits) / dur * 60 if dur else 0
        note = f"{len(hits)} onsets, {rate:.0f}/min"
        if scale < 1.0:
            note += " — dense, so eased back to stay distinct"
        lines.append(
            f"      - {{synth: {band}, zone: {z}, intensity: {intensity}, "
            f"decay: {decay}, color: {col}}}"
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
    g.add_argument("--format", choices=("mp3", "wav", "flac", "opus"),
                   help="container. mp3 is the default and what the firmware "
                        "decodes today. wav costs the device NO decode CPU at "
                        "all — it is a memcpy into the I2S buffer — which is "
                        "the cheapest fix if MP3 ever stutters on the "
                        "single-core S2, at roughly 9x the size. flac and "
                        "opus need the matching decoder enabled in the "
                        "pipeline (see `format:` in castle.yaml)")
    g.add_argument("--channels", type=int, choices=(1, 2),
                   help="2 = stereo (default; the show runs two speaker "
                        "chains). 1 = mono, half the size, for effects that "
                        "have no side to be on")
    g.add_argument("--sample-rate", type=int,
                   help="Hz (default 44100). 22050 halves the data rate and "
                        "is plenty for atmospheres")
    g.add_argument("--normalize", action=argparse.BooleanOptionalAction,
                   default=None,
                   help="EBU R128 loudness match, so imports sit level with "
                        "the generated scenes and each other (default on; "
                        "--no-normalize keeps the source's own level)")
    g.add_argument("--gain-db", type=float, help="flat gain adjustment in dB")

    g = ap.add_argument_group("analysis")
    g.add_argument("--sensitivity", type=sensitivity_arg,
                   help="onset threshold; lower finds more (default 1.1). "
                        "Per band: low=0.8,mid=1.1,high=1.6")

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
        marks = ana.analyze_full(ana.load_audio(src),
                                 sensitivity=args.sensitivity
                                 if args.sensitivity is not None else 1.1)
        dur = len(ana.load_audio(src)) / ana.SR
        for band, hits in marks.items():
            print(f"  {band:<11} {len(hits):>4} onsets")
        print(f"\n{scene_block(src.stem, dur, marks, src.suffix.lstrip('.') or 'mp3')}")
        return 0

    # Options: remembered defaults, overridden by whatever was passed now.
    prev = mf.get(args.refresh) if args.refresh else None
    if args.refresh and prev is None:
        raise SystemExit(f"no remembered track {args.refresh!r} "
                         f"(tools/import_track.py --list)")
    base = dict(prev.get("opts", {})) if prev else {}
    o: dict[str, Any] = {
        "start": base.get("start", "0"),
        "take": base.get("take"),
        "fade_in": base.get("fade_in"),
        "fade_out": base.get("fade_out"),
        "bitrate": base.get("bitrate", BITRATE),
        "channels": base.get("channels", 2),
        "sample_rate": base.get("sample_rate", 44100),
        "normalize": base.get("normalize", True),
        "gain_db": base.get("gain_db"),
        "sensitivity": base.get("sensitivity", 1.1),
        "format": base.get("format", "mp3"),
    }
    for k in list(o):
        v = getattr(args, k, None)
        if v not in (None, False):
            o[k] = v
    # normalize is tri-state (None = keep the remembered/default value), and
    # an explicit --no-normalize is a False the loop above would drop.
    if args.normalize is not None:
        o["normalize"] = args.normalize

    source = args.source or (prev or {}).get("source", "")
    if not source:
        raise SystemExit("need a source (file or URL)")
    source = source.removeprefix("file:")
    is_url = "://" in source

    # Per-run scratch dir. A shared one races: two imports at once, and the
    # first to finish deletes the other's half-downloaded file out from under
    # it. Seen exactly that, as a job that failed for no visible reason.
    tmp = TRACKS / f"_incoming_{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        return _import(args, o, source, is_url, tmp, prev)
    finally:
        # Whatever happened, the scratch dir goes — a failed import used to
        # leave _incoming_<pid> behind next to the library.
        shutil.rmtree(tmp, ignore_errors=True)


def _import(args: argparse.Namespace, o: dict, source: str, is_url: bool,
            tmp: Path, prev: dict | None) -> int:
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
    # The derived branch above is sanitised by construction; an EXPLICIT id
    # was not, and the studio forwards the browser's id verbatim — so
    # "../../audio/01_vigil" used to walk out of tracks/ and overwrite show
    # audio. Same alphabet for every spelling, no exceptions.
    if not tid or not all(c.isalnum() or c == "_" for c in tid):
        raise SystemExit(f"track id {tid!r} — letters, digits and _ only")

    out = TRACKS / f"{tid}.{o['format']}"
    conv = dict(o)
    conv["start"] = secs(o["start"]) if o["start"] else 0
    conv["take"] = secs(o["take"]) if o["take"] else None
    convert(src, out, conv)

    x = ana.load_audio(out)
    dur = len(x) / ana.SR
    size = out.stat().st_size
    # stereo= so import-time markers carry pan, same as the studio's live
    # analysis — otherwise the pasteable scene block and the desk disagree.
    marks = ana.analyze_full(x, sensitivity=o["sensitivity"],
                             stereo=ana.load_stereo(out))

    mf.record(
        tid,
        source=source if is_url else f"file:{Path(source).resolve()}",
        title=title, opts=o, notes=args.notes,
        audio={"duration": round(dur, 2), "bytes": size, "format": o["format"],
               "channels": o["channels"], "sample_rate": o["sample_rate"],
               "bitrate": o["bitrate"]},
        onsets={k: len(v) for k, v in marks.items()},
    )

    ch = "mono" if o["channels"] == 1 else "stereo"
    print(f"\nimported  tracks/{tid}.mp3")
    lossy = o["format"] in ("mp3", "opus")
    rate_txt = f"{o['bitrate']}kbps " if lossy else ""
    print(f"  {dur:.1f}s   {size/1024:.0f} KB   {o['format']} "
          f"{rate_txt}{ch} {o['sample_rate']}Hz")
    if o["format"] == "wav":
        print("  wav costs the device no decode CPU at all — worth it if MP3 "
              "ever stutters")
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
    print(scene_block(tid, dur, marks, o["format"]))
    print("\nthen:  make audio && make generate && make preview")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
