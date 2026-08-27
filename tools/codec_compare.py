#!/usr/bin/env python3
"""Encode one clip several ways and say how much each one lost.

Picking a codec for this project is a real decision with a real cost: the app
partition holds about 2.9 MB for every scene the show has, and the S2 has one
core to decode with. MP3 at 96k is four times smaller than WAV and the WAV
costs no decode CPU at all. Which of those matters more is not something a
table of formats can answer — you have to hear it.

So this produces the thing you can hear, plus one number you can compare.

The number is a spectral distance from the uncompressed original, in decibels:
the mean absolute difference between the two log-magnitude spectrograms. It is
not PESQ or ViSQOL — those model an ear, and this does not pretend to. What it
does do is rank encodes of the *same clip* honestly and reproducibly, which is
the question actually being asked ("is 96k enough here, or do I need 128?").
Lower is closer to the source. Zero is the source itself.

Read it as a tiebreaker for your ears, never as a substitute for them.

Calibration, measured on an 8 s clip of wicked_winds and reproducible across
runs: FLAC scores 0.06 dB, MP3 at 96k scores 1.29, Opus at 96k scores 1.23.
FLAC is lossless, so 0.06 is the floor of the measurement — the encode path's
own rounding — not audible loss. Anything near it is transparent by this
metric; the gap to ~1.3 is what a lossy encoder at 96k actually costs.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import analyze as ana
import import_track as it

# The lossless reference every other encode is measured against. Making it
# first, once, also means the codecs are all fed identical audio — trimming
# and normalising separately per codec would fold those differences into the
# score and call them codec loss.
REFERENCE = "wav"

CODECS = ("wav", "mp3", "opus", "flac")


def spectral_db(ref: np.ndarray, got: np.ndarray, sr: int = ana.SR) -> float:
    """Mean absolute log-spectral difference, in dB. Lower is closer.

    Compared in the spectral domain rather than sample by sample because every
    lossy codec introduces a delay and a phase shift that a waveform diff would
    report as enormous error while sounding identical. What survives encoding
    is the magnitude spectrum, so that is what gets compared.
    """
    from scipy import signal

    n = min(len(ref), len(got))
    if n < ana.WIN * 2:
        return 0.0
    ref, got = ref[:n], got[:n]

    def spec(x: np.ndarray) -> np.ndarray:
        _f, _t, z = signal.stft(x, fs=sr, nperseg=ana.WIN, noverlap=ana.WIN - ana.HOP)
        # Floor at -100 dB so silence does not turn into a division by zero
        # and dominate the mean with meaningless numbers.
        return np.asarray(20 * np.log10(np.abs(z) + 1e-5))

    a, b = spec(ref), spec(got)
    m = min(a.shape[1], b.shape[1])
    return float(np.abs(a[:, :m] - b[:, :m]).mean())


def encode_set(src: Path, dest: Path, opts: dict, codecs=CODECS) -> list[dict]:
    """Encode `src` once per codec into `dest`, and score each one.

    Returns a row per codec: name, file, bytes, and `db` — the spectral
    distance from the WAV reference. The reference itself scores 0.0, which is
    true rather than flattering: it is the thing being compared against.
    """
    dest.mkdir(parents=True, exist_ok=True)
    order = [REFERENCE] + [c for c in codecs if c != REFERENCE]

    ref_audio: np.ndarray | None = None
    rows: list[dict] = []
    for codec in order:
        out = dest / f"{codec}.{codec}"
        o = dict(opts, format=codec)
        it.convert(src, out, o)
        x = ana.load_audio(out)
        if codec == REFERENCE:
            ref_audio = x
        rows.append(
            {
                "codec": codec,
                "file": out.name,
                "bytes": out.stat().st_size,
                "db": 0.0
                if codec == REFERENCE or ref_audio is None
                else round(spectral_db(ref_audio, x), 2),
            }
        )

    # Report in the order asked for, not the order encoded.
    by_name = {r["codec"]: r for r in rows}
    return [by_name[c] for c in codecs if c in by_name]


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("src", type=Path)
    ap.add_argument("--out", type=Path, default=Path("/tmp/codec-compare"))
    ap.add_argument("--start", default=0)
    ap.add_argument("--take", type=float)
    ap.add_argument("--bitrate", type=int, default=96)
    ap.add_argument("--channels", type=int, default=1)
    ap.add_argument("--sample-rate", type=int, default=44100)
    a = ap.parse_args()

    if not a.src.exists():
        raise SystemExit(f"no such file: {a.src}")
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg not installed")

    opts = {
        "start": it.secs(str(a.start)) if a.start else 0,
        "take": a.take,
        "fade_in": None,
        "fade_out": None,
        "normalize": False,
        "gain_db": None,
        "bitrate": a.bitrate,
        "channels": a.channels,
        "sample_rate": a.sample_rate,
    }
    rows = encode_set(a.src, a.out, opts)

    print(f"{'codec':6} {'size':>9}  {'vs wav':>8}")
    print("-" * 27)
    for r in rows:
        db = "reference" if r["codec"] == REFERENCE else f"{r['db']:.2f} dB"
        print(f"{r['codec']:6} {r['bytes'] / 1024:8.0f}K  {db:>8}")
    print(f"\nfiles in {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
