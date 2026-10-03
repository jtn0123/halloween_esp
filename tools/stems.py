#!/usr/bin/env python3
"""Split a track into its instrument stems, and analyse every channel.

Why this exists: the onset detector hears a MONO downmix. A voice singing
over drums lands in the same mid band as the drums, and a spooky effect
panned hard left arrives at half volume — or cancelled outright, if the mix
plays phase tricks. The device never needs any of this unpicked (it keeps
playing the combined file); the LIGHT CUES are what benefit from knowing
which hits are sung, which are drummed, and which side they live on.

Demucs (htdemucs, all four of its sources in one pass) does the unpicking:
~25 s per track on Apple-silicon GPU. Results land next to the audio they
came from —

    tracks/stems/<id>/vocals.mp3       the voice alone, stereo
    tracks/stems/<id>/backing.mp3      everything else (drums+bass+other)
    tracks/stems/<id>/drums.mp3        the kit — the beat grid's best witness
    tracks/stems/<id>/bass.mp3         the bass line — it lands on the "one"
    tracks/stems/<id>/other.mp3        guitars, keys, strings, effects
    tracks/stems/<id>/analysis.json    peaks + onsets, per layer, per channel

`backing` is what the old two-stem split called `no_vocals`, and is built the
same way demucs built it: the three instrument sources summed before any
clipping guard, then demucs's own `rescale` rule. Every consumer that
predates the four-stem split reads it, so it stays; a split made before
drums/bass/other existed has only the first two and stays valid — nothing
here forces a re-split.

`tracks/*` is gitignored and sd_sync's glob is non-recursive, so stems never
reach GitHub or the SD card.

The analysis runs the SAME detector as the pipeline (tools/analyze.py) on
every layer (voice / backing / combined, then drums / bass / other) x three
channels (left / right / both). "both" is the mono downmix — exactly what the
pipeline hears today — so the studio can put a channel's picture next to the
pipeline's and show precisely what the downmix loses.

    tools/stems.py <track-id> [--force] [--out DIR] [--fast-cpu]

`--out DIR` writes to DIR/<id>/ instead of the library — a lab splitting
into a scratch directory reads the track and never touches tracks/stems/.
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import analyze as ana
import exe_paths
import import_reason as ir
import numpy as np
import portable_fs

ROOT = Path(__file__).resolve().parent.parent
# Same override the studio honours: a sandboxed test must not write stems
# into the real library.
TRACKS = Path(os.environ.get("CASTLE_TRACKS") or (ROOT / "tracks"))
STEMS = TRACKS / "stems"
#: The per-track cache file: peaks and onsets for all nine layer/channel
#: pairs, plus the source's size and mtime so `fresh` can tell whether a
#: re-import has invalidated it. Named because three paths point at it.
ANALYSIS_JSON = "analysis.json"
AUDIO_EXT = ("mp3", "wav", "flac", "opus")


def _fail(said: str, *detail: str) -> SystemExit:
    """The owner's sentence (tools/import_reason.py) as the exit message,
    with the tool's own words printed above it, indented: the studio and
    Castle Radio show the sentence and keep the rest for Details."""
    for line in detail:
        print("    " + line.rstrip(), file=sys.stderr, flush=True)
    return SystemExit(said)


#: What htdemucs separates, in the order it names them.
SOURCES = ("drums", "bass", "other", "vocals")
#: The instrument sources `backing` is the sum of.
INSTRUMENTS = ("drums", "bass", "other")
#: Every layer analysis.json can hold. The first three are all a two-stem
#: split ever wrote; a reader must treat the rest as optional.
LAYERS = ("vocals", "backing", "combined", *INSTRUMENTS)
#: The stems the studio serves (`stem_file`) — the two the stems panel plays.
SERVED = ("vocals", "backing")
CHANNELS = ("left", "right", "both")
# Waveform resolution for the stems strips. Less than the clip editor's 1000:
# three strips share the panel, and 700 is still finer than any canvas width.
PEAKS = 700
# Demucs is minutes of GPU work at worst, not unbounded; anything past this
# is a hang, and a hung child must not wedge the studio's job slot.
SEPARATE_TIMEOUT = 600


def track_file(tid: str) -> Path | None:
    """The audio behind a bare id, whichever container it landed in."""
    tid = Path(tid).name  # no traversal, ever
    for e in AUDIO_EXT:
        p = TRACKS / f"{tid}.{e}"
        if p.exists():
            return p
    return None


def stem_file(tid: str, layer: str) -> Path | None:
    """A stem's mp3 for serving, or None. `combined` is not a file here —
    that is the original track, and /api/track already streams it."""
    if layer not in SERVED:
        return None
    p = STEMS / Path(tid).name / f"{layer}.mp3"
    return p if p.exists() else None


def fresh(tid: str, root: Path | None = None) -> bool:
    """Do the stems on disk (under `root`, the library's by default) still
    describe the current audio?

    A re-import (new trim, new normalize) rewrites the track; stems built
    from the old file would validate a split of audio that no longer exists.
    """
    src = track_file(tid)
    meta_p = (STEMS if root is None else root) / tid / ANALYSIS_JSON
    if src is None or not meta_p.exists():
        return False
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    st = src.stat()
    return bool(
        meta.get("src_bytes") == st.st_size
        and meta.get("src_mtime") == int(st.st_mtime)
    )


def analysis(tid: str) -> dict:
    """The cached nine-way analysis, or a reason there is none.

    Served straight from disk: the split job wrote it, and re-deriving nine
    STFTs inside an HTTP GET would stall the panel for tens of seconds.
    """
    tid = Path(tid).name
    if track_file(tid) is None:
        return {"ok": False, "error": "no such track"}
    p = STEMS / tid / ANALYSIS_JSON
    if not p.exists():
        return {"ok": False, "error": "not split yet"}
    try:
        out: dict = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return {"ok": False, "error": f"stems analysis unreadable: {e}"}
    out["ok"] = True
    out["stale"] = not fresh(tid)
    return out


def _channels_of(path: Path) -> dict[str, np.ndarray]:
    """left / right / both, where `both` is the pipeline's own mono downmix
    (ffmpeg -ac 1) rather than a hand-rolled average — the whole point is to
    show what THAT path hears, so it must be byte-for-byte the same decode."""
    left, right = ana.load_stereo(path)
    return {"left": left, "right": right, "both": ana.load_audio(path)}


def _peaks_of(x: np.ndarray, buckets: int = PEAKS) -> list[float]:
    if len(x) == 0:
        return []
    edges = np.linspace(0, len(x), buckets + 1).astype(int)
    out = [
        float(np.abs(x[a:b]).max()) if b > a else 0.0
        for a, b in itertools.pairwise(edges)
    ]
    top = max(out) or 1.0
    return [round(p / top, 3) for p in out]


def analyse_layers(files: dict[str, Path], sensitivity: float = 1.1) -> dict:
    """Peaks + onsets for every (layer, channel) pair, one decode per pair.

    Peaks are normalised per channel — the strips show each channel's own
    shape. What they must NOT hide is a channel being genuinely quieter than
    its twin, so the raw peak level rides along as `level`.
    """
    layers: dict[str, dict] = {}
    dur = 0.0
    for name, path in files.items():
        chans = _channels_of(path)
        dur = max(dur, len(chans["both"]) / ana.SR)
        layers[name] = {}
        for ch, x in chans.items():
            marks = ana.analyze(x, sensitivity=sensitivity)
            layers[name][ch] = {
                "peaks": _peaks_of(x),
                "level": round(float(np.abs(x).max()), 4) if len(x) else 0.0,
                "onsets": {
                    k: [[round(t, 3), v] for t, v in hits] for k, hits in marks.items()
                },
            }
            counts = " ".join(
                f"{k.replace('onset_', '')}:{len(v)}" for k, v in marks.items()
            )
            print(f"  {name:<9} {ch:<5} {counts or 'no onsets'}", flush=True)
    return {"duration": round(dur, 3), "layers": layers}


#: Opt-in CPU shortcut (CASTLE_DEMUCS_FAST=1 or --fast-cpu): segment overlap
#: 0.1 instead of demucs's 0.25, one worker per core. OFF by default because
#: it was measured and the stems DO change: on a 30 s clip (Apple M-series,
#: CPU, 2026-09-30) two default runs agreed at 24-25 dB SDR per stem — the
#: random-shift noise floor — while the fast run sat at 21-22 dB for vocals
#: and drums, 8.8 dB for `other` and 0.7 dB for bass, for roughly half the
#: wall time (13 s against 27-40 s). The bass line is the "one" the beat grid
#: leans on, so it stays a choice for a slow Windows CPU, not a default.
FAST_ENV = "CASTLE_DEMUCS_FAST"
FAST_OVERLAP = "0.1"


def fast_cpu() -> bool:
    """Whether the environment opted into the fast CPU split."""
    return os.environ.get(FAST_ENV, "") == "1"


def demucs_argv(src: Path, out: Path, device: str, fast: bool = False) -> list[str]:
    """The demucs command line. `fast` only ever changes a CPU run: the GPU
    is fast already, and its result should not move with a CPU knob."""
    # All four sources as float wavs with no clip guard: `backing` is summed
    # from three of them afterwards, and demucs's per-file `rescale` would
    # otherwise shrink a loud drum stem on its own before the sum saw it.
    # (A float wav is still clamped at full scale, which one source alone
    # rarely reaches — the sum is where the overshoot lives.)
    argv = [
        sys.executable,
        "-m",
        "demucs.separate",
        "-n",
        "htdemucs",
        "--float32",
        "--clip-mode",
        "none",
        "-d",
        device,
        "-o",
        str(out),
        str(src),
    ]
    if fast and device == "cpu":
        argv[-1:-1] = ["--overlap", FAST_OVERLAP, "-j", str(os.cpu_count() or 1)]
    return argv


def _run_demucs(
    src: Path, out: Path, device: str, fast: bool = False
) -> subprocess.CompletedProcess:
    argv = demucs_argv(src, out, device, fast)
    if os.environ.get("CASTLE_PROGRESS_STREAM") == "1":
        from progress_process import run_progress

        return run_progress(argv, SEPARATE_TIMEOUT)
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=SEPARATE_TIMEOUT,
    )


def _rescale(x: np.ndarray) -> np.ndarray:
    """demucs's own `rescale` clip guard (demucs.audio.prevent_clip): scaled
    down only when it would clip, otherwise untouched."""
    peak = float(np.abs(x).max()) if x.size else 0.0
    return (x / max(1.01 * peak, 1.0)).astype(np.float32)


def mix_stems(separated: Path, dest: Path) -> dict[str, Path]:
    """The four demucs sources found under `separated`, plus `backing`, as
    float wavs in `dest` — each through the clip guard demucs would have
    applied had it written them itself.

    `backing` = drums + bass + other, summed raw and then guarded: how
    two-stem mode builds `no_vocals`, so a four-stem backing is the two-stem
    backing of the same separation (bar a source that alone passed full
    scale, which arrives clamped), not a re-derivation of it.
    """
    from scipy.io import wavfile

    raw: dict[str, np.ndarray] = {}
    rate = 44100
    for name in SOURCES:
        found = next(separated.rglob(f"{name}.wav"), None)
        if found is None:
            raise _fail(ir.GENERIC, f"demucs produced no {name} stem")
        rate, data = wavfile.read(found)
        raw[name] = np.asarray(data, dtype=np.float32)
    raw["backing"] = raw["drums"] + raw["bass"] + raw["other"]
    dest.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for name in ("vocals", "backing", *INSTRUMENTS):
        out[name] = dest / f"{name}.wav"
        wavfile.write(out[name], rate, _rescale(raw.pop(name)))
    return out


def _encode(wav: Path, mp3: Path) -> None:
    """Stereo 160 kbps — a validation listen, not a flash-budget citizen."""
    r = subprocess.run(
        [
            exe_paths.ffmpeg(),
            "-v",
            "error",
            "-y",
            "-i",
            str(wav),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "160k",
            str(mp3),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=300,
    )
    if r.returncode != 0:
        said = ir.recognised(r.stderr or "") or ir.TOOL_FAILED.format(prog="ffmpeg")
        tail = (r.stderr or "").strip().splitlines()[-4:]
        raise _fail(said, f"ffmpeg could not encode {mp3.name}", *tail)


def separate(
    tid: str, force: bool = False, out: Path | None = None, fast: bool | None = None
) -> int:
    """Split `tid` into DIR/<id>/, DIR being the library's stems directory
    unless `out` names another — the track itself is only ever read. `fast`
    (default: CASTLE_DEMUCS_FAST) takes the CPU shortcut FAST_ENV describes."""
    fast = fast_cpu() if fast is None else fast
    src = track_file(tid)
    if src is None:
        raise SystemExit(f"no such track: {tid}")
    root = STEMS if out is None else out
    if fresh(tid, root) and not force:
        print(f"stems for {tid} are current — --force to redo")
        return 0
    if importlib.util.find_spec("demucs") is None:
        raise _fail(
            ir.DEMUCS_MISSING,
            "demucs is not installed: python -m pip install demucs (then re-pin: "
            "python -m pip install click==8.3.3), with this repo's python",
        )

    dest = root / Path(tid).name
    with tempfile.TemporaryDirectory(prefix="castle-stems-") as td:
        tmp = Path(td)
        print(f"separating {src.name} (htdemucs, four stems)…", flush=True)
        # Apple GPU when there is one; torch raising over MPS availability
        # must degrade to slow, not to broken.
        device = "mps" if platform.system() == "Darwin" else "cpu"
        try:
            r = _run_demucs(src, tmp / "sep", device, fast)
            if r.returncode != 0 and device != "cpu":
                print("  GPU path failed — retrying on CPU", flush=True)
                r = _run_demucs(src, tmp / "sep", "cpu", fast)
        except subprocess.TimeoutExpired:
            raise _fail(
                ir.STALLED,
                f"demucs stalled: gave up after {SEPARATE_TIMEOUT // 60} minutes",
            ) from None
        if r.returncode != 0:
            tail = [
                ln for ln in (r.stderr or r.stdout or "").splitlines() if ln.strip()
            ][-3:]
            said = ir.recognised(r.stderr or r.stdout or "") or ir.GENERIC
            raise _fail(said, "demucs failed:", *tail)
        wavs = mix_stems(tmp / "sep", tmp / "mix")

        print("encoding stems…", flush=True)
        dest.mkdir(parents=True, exist_ok=True)
        for name, wav in wavs.items():
            _encode(wav, dest / f"{name}.mp3")

        print(f"analysing {len(LAYERS)} layers x {len(CHANNELS)} channels…", flush=True)
        # The two-stem layers first, so a reader watching progress sees the
        # layers every consumer uses finish before the instrument ones.
        data = analyse_layers(
            {"vocals": wavs["vocals"], "backing": wavs["backing"], "combined": src}
            | {name: wavs[name] for name in INSTRUMENTS}
        )

    st = src.stat()
    data.update(id=tid, src_bytes=st.st_size, src_mtime=int(st.st_mtime))
    # Atomic, like every other write that another process may be reading.
    tmp_json = dest / f"{ANALYSIS_JSON}.tmp"
    tmp_json.write_text(json.dumps(data), encoding="utf-8")
    portable_fs.replace(tmp_json, dest / ANALYSIS_JSON)
    print(f"stems ready — {dest}/", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("id", help="track id (tools/import_track.py --list)")
    ap.add_argument(
        "--force", action="store_true", help="re-split even if the stems look current"
    )
    ap.add_argument(
        "--out",
        type=Path,
        help="write DIR/<id>/ instead of the library's stems directory",
    )
    ap.add_argument(
        "--fast-cpu",
        action="store_true",
        default=None,
        help=f"on CPU, overlap {FAST_OVERLAP} and one job per core: about twice "
        f"as fast, measurably different stems (also {FAST_ENV}=1)",
    )
    args = ap.parse_args()
    return separate(args.id, force=args.force, out=args.out, fast=args.fast_cpu)


if __name__ == "__main__":
    raise SystemExit(main())
