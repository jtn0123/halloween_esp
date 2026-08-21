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
from import_scene import FRAME as FRAME
from import_scene import fit_to_density as fit_to_density
from import_scene import scene_block as scene_block
from studio_tracks import AUDIO_EXT, SRC_DIR

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


def probe_duration(src: Path) -> float | None:
    """The source's length in seconds by ffprobe, or None if it cannot say."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(src)],
            capture_output=True, text=True, check=False, timeout=60)
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
    ap.add_argument("--keep-source", action="store_true",
                    help="copy a local source file into tracks/_src/ and "
                         "remember THAT as the source — for a file that is "
                         "about to be deleted (the studio's upload staging)")

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
        # `is not`, not `not in (None, False)`: 0.0 == False, and an explicit
        # --fade-in 0 is how a remembered fade gets cleared on a refresh.
        if v is not None and v is not False:
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
        # The basename, not the path: an operator can act on "drop it
        # again", not on /private/tmp/…/_upload/x.wav (JB1-3).
        raise SystemExit(f"no such file: {src.name} — the remembered source "
                         "is gone; import it again from the original")

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
    # A start past the end is the commonest way to get a 358-byte "track":
    # ffmpeg happily writes a header and nothing else. Refuse up front, in
    # the operator's own units (JB1-1).
    src_dur = probe_duration(src)
    if src_dur is not None and conv["start"] >= src_dur:
        raise SystemExit(f"start {o['start']} is past the end of {src.name} "
                         f"({src_dur:.0f}s long)")
    convert(src, out, conv)

    try:
        x = ana.load_audio(out)
        if len(x) < ana.SR // 10:
            raise ValueError("the cut came out (nearly) empty")
    except Exception as e:
        # Never leave a broken row behind: the desk would offer to send it
        # to the castle. One line, no traceback.
        out.unlink(missing_ok=True)
        raise SystemExit(f"{src.name}: {e} — check start/length against "
                         f"the source") from None
    dur = len(x) / ana.SR
    size = out.stat().st_size
    # A refresh that changed the container leaves the old one behind, and
    # track_path() would keep finding it first. One file per id.
    for other in AUDIO_EXT:
        if other != o["format"]:
            (TRACKS / f"{tid}.{other}").unlink(missing_ok=True)
    if args.keep_source and not is_url:
        source = f"file:{keep_source(src, tid)}"
    # stereo= so import-time markers carry pan, same as the studio's live
    # analysis — otherwise the pasteable scene block and the desk disagree.
    marks = ana.analyze_full(x, sensitivity=o["sensitivity"],
                             stereo=ana.load_stereo(out))

    mf.record(
        tid,
        source=source if is_url or source.startswith("file:")
               else f"file:{Path(source).resolve()}",
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
