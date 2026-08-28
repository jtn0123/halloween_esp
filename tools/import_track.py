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
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
import core_bins
import manifest as mf
from import_convert import _same_file as _same_file
from import_convert import convert as convert
from import_convert import keep_source as keep_source
from import_convert import probe_duration as probe_duration
from import_fetch import fetch_url as fetch_url
from import_fetch import is_web_url as is_web_url
from import_scene import FRAME as FRAME
from import_scene import fit_to_density as fit_to_density
from import_scene import scene_block as scene_block
from studio_tracks import AUDIO_EXT

ROOT = Path(__file__).resolve().parent.parent
# CASTLE_TRACKS is the whole sandbox story (see playwright.config.ts): the
# studio honored it but this subprocess wrote to the real tracks/ anyway —
# an e2e import quietly landed files in (or over!) the user's library.
TRACKS = Path(os.environ.get("CASTLE_TRACKS") or (ROOT / "tracks"))
BITRATE = 96  # matches hardware.audio.bitrate in scenes.yaml
BUDGET = 2.9 * 1024 * 1024
SR = 44100  # the analysis rate — analyze.SR, which the crate fixes too


def crate_analysis(
    path: Path, sensitivity: float | dict[str, float], stereo: bool
) -> tuple[int, dict[str, list[list[float]]]]:
    """analyze_full through castle-core's analyze_track bin: the mono
    decode's sample count and the onset bands (with pans when `stereo`).
    The crate is the importer's ears now; analyze.py remains only as the
    parity reference — tests/test_analyze_track_rust.py holds the two
    value-for-value. Failures raise ValueError so the caller keeps its
    own sentences."""
    req = {"path": str(path), "sensitivity": sensitivity, "stereo": stereo}
    run = subprocess.run(
        [str(core_bins.core_bin("analyze_track"))],
        input=json.dumps(req).encode(),
        capture_output=True,
        check=False,
    )
    if run.returncode != 0:
        raise ValueError(
            run.stderr.decode().strip() or f"analyze_track failed on {path.name}"
        )
    out = json.loads(run.stdout)
    return int(out["samples"]), out["bands"]


def secs(v: str) -> float:
    """Accept 12, 1:05 or 1:02:03."""
    parts = [float(p) for p in str(v).split(":")]
    out = 0.0
    for p in parts:
        out = out * 60 + p
    return out


_NUM = re.compile(r"^\d+(?:\.\d+)?$")


def time_arg(raw: str) -> str:
    """`12`, `1:05` or `1:02:03` — what `secs()` reads. Anything else (a
    flag-shaped "-x", a word, 1:99) is refused before it reaches ffmpeg."""
    parts = raw.strip().split(":")
    ok = (
        1 <= len(parts) <= 3
        and all(_NUM.match(p) for p in parts)
        and all(float(p) < 60 for p in parts[1:])
    )
    if not ok:
        raise argparse.ArgumentTypeError(
            f"not a time: {raw!r} — use seconds (24) or m:ss (0:12)"
        )
    return raw.strip()


def text_arg(raw: str) -> str:
    """Free text that must not look like an option (the studio passes it as
    `--notes=<v>`; a value starting with '-' is refused even so)."""
    if raw.startswith("-"):
        raise argparse.ArgumentTypeError(f"{raw!r} looks like an option, not text")
    return raw


def sensitivity_arg(raw: str) -> float | dict[str, float]:
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
    out: dict[str, float] = {}
    for part in raw.split(","):
        k, _, v = part.partition("=")
        k = k.strip().replace("onset_", "")
        if k not in ("low", "mid", "high"):
            raise argparse.ArgumentTypeError(
                f"unknown band {k!r} — expected low, mid or high"
            )
        try:
            out[f"onset_{k}"] = float(v)
        except ValueError:
            raise argparse.ArgumentTypeError(f"not a number for {k}: {v!r}") from None
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Import audio into tracks/, remembering where it came from."
    )
    ap.add_argument(
        "source",
        nargs="?",
        help="local audio file, or a URL for yt-dlp. Omit with "
        "--refresh to reuse the remembered source.",
    )
    ap.add_argument("--id", help="track id (default: sanitised filename)")
    ap.add_argument(
        "--refresh",
        metavar="ID",
        help="rebuild an existing track from its remembered "
        "source; any option you pass overrides what was "
        "used last time",
    )
    ap.add_argument("--list", action="store_true", help="show imported tracks")
    ap.add_argument(
        "--notes", default="", type=text_arg, help="free-text note on the track"
    )
    ap.add_argument(
        "--keep-source",
        action="store_true",
        help="copy a local source file into tracks/_src/ and "
        "remember THAT as the source — for a file that is "
        "about to be deleted (the studio's upload staging)",
    )

    g = ap.add_argument_group("trim")
    g.add_argument("--start", type=time_arg, help="skip in, e.g. 0:12")
    g.add_argument("--take", type=time_arg, help="seconds to keep, e.g. 24")
    g.add_argument("--fade-in", type=float, help="seconds of fade at the head")
    g.add_argument("--fade-out", type=float, help="seconds of fade at the tail")

    g = ap.add_argument_group("format")
    g.add_argument(
        "--bitrate",
        type=int,
        help=f"kbps (default {BITRATE}, matching the flash budget)",
    )
    g.add_argument(
        "--format",
        choices=("mp3", "wav", "flac", "opus"),
        help="container. mp3 is the default and what the firmware "
        "decodes today. wav costs the device NO decode CPU at "
        "all — it is a memcpy into the I2S buffer — which is "
        "the cheapest fix if MP3 ever stutters on the "
        "single-core S2, at roughly 9x the size. flac and "
        "opus need the matching decoder enabled in the "
        "pipeline (see `format:` in castle.yaml)",
    )
    g.add_argument(
        "--channels",
        type=int,
        choices=(1, 2),
        help="2 = stereo (default; the show runs two speaker "
        "chains). 1 = mono, half the size, for effects that "
        "have no side to be on",
    )
    g.add_argument(
        "--sample-rate",
        type=int,
        help="Hz (default 44100). 22050 halves the data rate and "
        "is plenty for atmospheres",
    )
    g.add_argument(
        "--normalize",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="EBU R128 loudness match, so imports sit level with "
        "the generated scenes and each other (default on; "
        "--no-normalize keeps the source's own level)",
    )
    g.add_argument("--gain-db", type=float, help="flat gain adjustment in dB")

    g = ap.add_argument_group("analysis")
    g.add_argument(
        "--sensitivity",
        type=sensitivity_arg,
        help="onset threshold; lower finds more (default 1.1). "
        "Per band: low=0.8,mid=1.1,high=1.6",
    )

    ap.add_argument(
        "--analyze-only", action="store_true", help="just report onsets, don't import"
    )
    args = ap.parse_args()

    TRACKS.mkdir(exist_ok=True)

    if args.list:
        data = mf.load()
        if not data:
            print("no tracks imported yet")
            return 0
        for tid, e in sorted(data.items()):
            a = e.get("audio", {})
            print(
                f"{tid:<20} {a.get('duration', 0):>6.1f}s "
                f"{a.get('bytes', 0) / 1024:>7.0f}K  {e.get('source', '')[:60]}"
            )
        return 0

    if args.analyze_only:
        src = Path(args.source)
        samples, marks = crate_analysis(
            src,
            args.sensitivity if args.sensitivity is not None else 1.1,
            stereo=False,
        )
        dur = samples / SR
        for band, hits in marks.items():
            print(f"  {band:<11} {len(hits):>4} onsets")
        print(f"\n{scene_block(src.stem, dur, marks, src.suffix.lstrip('.') or 'mp3')}")
        return 0

    # Options: remembered defaults, overridden by whatever was passed now.
    prev = mf.get(args.refresh) if args.refresh else None
    if args.refresh and prev is None:
        raise SystemExit(
            f"no remembered track {args.refresh!r} (tools/import_track.py --list)"
        )
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
    is_url = is_web_url(source)
    # A source that wanted to be a URL and is not one never becomes a path:
    # "ftp://…" or "--config-location=http://…" would otherwise be opened as a
    # local file name, which is a confusing way to fail at best.
    if not is_url and "://" in source:
        raise SystemExit(f"not a link this can fetch: {source!r} — http(s) only")

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


def _import(
    args: argparse.Namespace,
    o: dict[str, Any],
    source: str,
    is_url: bool,
    tmp: Path,
    prev: mf.Entry | None,
) -> int:
    title = (prev or {}).get("title", "")
    if is_url:
        src, title = fetch_url(source, tmp)
    else:
        src = Path(source)
    if not src.exists():
        # The basename, not the path: an operator can act on "drop it
        # again", not on /private/tmp/…/_upload/x.wav (JB1-3).
        raise SystemExit(
            f"no such file: {src.name} — the remembered source "
            "is gone; import it again from the original"
        )

    # Truncate BEFORE stripping, and cut at the last word boundary inside
    # the limit — "the_citizens_of_halloween___this" (cut mid-title, dangling
    # separators kept) is what the other order produces, on the desk and on
    # the card.
    slug = "".join(c if c.isalnum() else "_" for c in src.stem.lower())[:32]
    if "_" in slug[1:] and len(slug) == 32:
        slug = slug[: slug.rindex("_")]
    tid = args.refresh or args.id or slug.strip("_")
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
        raise SystemExit(
            f"start {o['start']} is past the end of {src.name} ({src_dur:.0f}s long)"
        )
    convert(src, out, conv)

    try:
        # stereo= so import-time markers carry pan, same as the studio's
        # live analysis — otherwise the pasteable scene block and the desk
        # disagree.
        samples, marks = crate_analysis(out, o["sensitivity"], stereo=True)
        if samples < SR // 10:
            raise ValueError("the cut came out (nearly) empty")
    except ValueError as e:
        # Never leave a broken row behind: the desk would offer to send it
        # to the castle. One line, no traceback.
        out.unlink(missing_ok=True)
        raise SystemExit(
            f"{src.name}: {e} — check start/length against the source"
        ) from None
    dur = samples / SR
    size = out.stat().st_size
    # A refresh that changed the container leaves the old one behind, and
    # track_path() would keep finding it first. One file per id — but never
    # the source itself: `import_track.py tracks/foo.wav --id foo` used to
    # convert the original and then delete it (judge B, JB2-2).
    for other in AUDIO_EXT:
        stale = TRACKS / f"{tid}.{other}"
        if other != o["format"] and not _same_file(stale, src):
            stale.unlink(missing_ok=True)
    if args.keep_source and not is_url:
        source = f"file:{keep_source(src, tid)}"

    mf.record(
        tid,
        source=source
        if is_url or source.startswith("file:")
        else f"file:{Path(source).resolve()}",
        title=title,
        opts=o,
        notes=args.notes,
        audio={
            "duration": round(dur, 2),
            "bytes": size,
            "format": o["format"],
            "channels": o["channels"],
            "sample_rate": o["sample_rate"],
            "bitrate": o["bitrate"],
        },
        onsets={k: len(v) for k, v in marks.items()},
    )

    ch = "mono" if o["channels"] == 1 else "stereo"
    print(f"\nimported  tracks/{tid}.mp3")
    lossy = o["format"] in ("mp3", "opus")
    rate_txt = f"{o['bitrate']}kbps " if lossy else ""
    print(
        f"  {dur:.1f}s   {size / 1024:.0f} KB   {o['format']} "
        f"{rate_txt}{ch} {o['sample_rate']}Hz"
    )
    if o["format"] == "wav":
        print(
            "  wav costs the device no decode CPU at all — worth it if MP3 "
            "ever stutters"
        )
    print(
        f"  source remembered — rebuild any time with: "
        f"tools/import_track.py --refresh {tid}"
    )
    print(
        f"  {size / BUDGET * 100:.0f}% of the flash audio budget "
        f"({BUDGET / 1024 / 1024:.1f} MB for ALL scenes)"
    )
    if size > BUDGET * 0.45:
        print(
            "  ⚠ that is a big share — trim it with --take, or drop "
            "--bitrate / --sample-rate"
        )
    print()
    for band, hits in marks.items():
        print(f"  {band:<11} {len(hits):>4} onsets ({len(hits) / dur * 60:.0f}/min)")
    if not marks:
        print("  no onsets detected — try --sensitivity 0.6")

    print("\nPaste into scenes/scenes.yaml under `scenes:` —\n")
    print(scene_block(tid, dur, marks, o["format"]))
    print("\nthen:  make audio && make generate && make preview")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
