#!/usr/bin/env python3
"""Split a track into voice and background stems, and analyse every channel.

Why this exists: the onset detector hears a MONO downmix. A voice singing
over drums lands in the same mid band as the drums, and a spooky effect
panned hard left arrives at half volume — or cancelled outright, if the mix
plays phase tricks. The device never needs any of this unpicked (it keeps
playing the combined file); the LIGHT CUES are what benefit from knowing
which hits are sung and which side they live on.

Demucs (htdemucs, two-stem mode) does the unpicking: ~25 s per track on
Apple-silicon GPU. Results land next to the audio they came from —

    tracks/stems/<id>/vocals.mp3       the voice alone, stereo
    tracks/stems/<id>/backing.mp3      everything else, stereo
    tracks/stems/<id>/analysis.json    peaks + onsets, per layer, per channel

`tracks/*` is gitignored and sd_sync's glob is non-recursive, so stems never
reach GitHub or the SD card.

The analysis runs the SAME detector as the pipeline (tools/analyze.py), nine
ways: three layers (voice / backing / combined) x three channels (left /
right / both). "both" is the mono downmix — exactly what the pipeline hears
today — so the studio can put a channel's picture next to the pipeline's and
show precisely what the downmix loses.

    tools/stems.py <track-id> [--force]
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
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
# Same override the studio honours: a sandboxed test must not write stems
# into the real library.
TRACKS = Path(os.environ.get("CASTLE_TRACKS") or (ROOT / "tracks"))
STEMS = TRACKS / "stems"
AUDIO_EXT = ("mp3", "wav", "flac", "opus")

LAYERS = ("vocals", "backing", "combined")
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
    if layer not in ("vocals", "backing"):
        return None
    p = STEMS / Path(tid).name / f"{layer}.mp3"
    return p if p.exists() else None


def fresh(tid: str) -> bool:
    """Do the stems on disk still describe the current audio?

    A re-import (new trim, new normalize) rewrites the track; stems built
    from the old file would validate a split of audio that no longer exists.
    """
    src = track_file(tid)
    meta_p = STEMS / tid / "analysis.json"
    if src is None or not meta_p.exists():
        return False
    try:
        meta = json.loads(meta_p.read_text())
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
    p = STEMS / tid / "analysis.json"
    if not p.exists():
        return {"ok": False, "error": "not split yet"}
    try:
        out: dict = json.loads(p.read_text())
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


def _run_demucs(src: Path, out: Path, device: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "demucs.separate",
            "--two-stems",
            "vocals",
            "-n",
            "htdemucs",
            "-d",
            device,
            "-o",
            str(out),
            str(src),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=SEPARATE_TIMEOUT,
    )


def _encode(wav: Path, mp3: Path) -> None:
    """Stereo 160 kbps — a validation listen, not a flash-budget citizen."""
    r = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "quiet",
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
        check=False,
        timeout=300,
    )
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg could not encode {mp3.name}")


def separate(tid: str, force: bool = False) -> int:
    src = track_file(tid)
    if src is None:
        raise SystemExit(f"no such track: {tid}")
    if fresh(tid) and not force:
        print(f"stems for {tid} are current — --force to redo")
        return 0
    if importlib.util.find_spec("demucs") is None:
        raise SystemExit(
            "demucs is not installed — "
            ".venv/bin/pip install demucs (then re-pin: "
            ".venv/bin/pip install click==8.3.3)"
        )

    dest = STEMS / tid
    with tempfile.TemporaryDirectory(prefix="castle-stems-") as td:
        tmp = Path(td)
        print(f"separating {src.name} (htdemucs)…", flush=True)
        # Apple GPU when there is one; torch raising over MPS availability
        # must degrade to slow, not to broken.
        device = "mps" if platform.system() == "Darwin" else "cpu"
        try:
            r = _run_demucs(src, tmp, device)
            if r.returncode != 0 and device != "cpu":
                print("  GPU path failed — retrying on CPU", flush=True)
                r = _run_demucs(src, tmp, "cpu")
        except subprocess.TimeoutExpired:
            raise SystemExit(
                f"demucs stalled — gave up after {SEPARATE_TIMEOUT // 60} minutes"
            ) from None
        if r.returncode != 0:
            tail = [
                ln for ln in (r.stderr or r.stdout or "").splitlines() if ln.strip()
            ][-3:]
            raise SystemExit("demucs failed:\n" + "\n".join(tail))
        vocals = next(tmp.rglob("vocals.wav"), None)
        backing = next(tmp.rglob("no_vocals.wav"), None)
        if vocals is None or backing is None:
            raise SystemExit("demucs produced no stems")

        print("encoding stems…", flush=True)
        dest.mkdir(parents=True, exist_ok=True)
        _encode(vocals, dest / "vocals.mp3")
        _encode(backing, dest / "backing.mp3")

        print("analysing 3 layers x 3 channels…", flush=True)
        data = analyse_layers({"vocals": vocals, "backing": backing, "combined": src})

    st = src.stat()
    data.update(id=tid, src_bytes=st.st_size, src_mtime=int(st.st_mtime))
    # Atomic, like every other write that another process may be reading.
    tmp_json = dest / "analysis.json.tmp"
    tmp_json.write_text(json.dumps(data))
    os.replace(tmp_json, dest / "analysis.json")
    print(f"stems ready — tracks/stems/{tid}/", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("id", help="track id (tools/import_track.py --list)")
    ap.add_argument(
        "--force", action="store_true", help="re-split even if the stems look current"
    )
    args = ap.parse_args()
    return separate(args.id, force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
