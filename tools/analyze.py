#!/usr/bin/env python3
"""Turn an arbitrary audio file into light cues.

The synths in synth.py report their own event times, so scenes built from
them get light that is locked to the sound by construction. A track you drop
in from outside has no such luxury — nobody tells us where its beats are. So
we listen for them.

The method is spectral flux onset detection, run separately in three bands:

    low   (< 200 Hz)     kick drums, heartbeats, organ pedal   -> the door
    mid   (200-2000 Hz)  voices, piano, most melodic material   -> the towers
    high  (> 2000 Hz)    cymbals, sibilance, bells, sparkle     -> accents

Splitting by band matters more than it might sound. A single onset track
gives every zone the same pulse and the castle blinks as one lamp; banded
onsets give the bass its own zone and let the towers answer the melody,
which is the difference between "the lights are flashing" and "the castle is
listening to the music".

Markers come back in exactly the shape synth.py produces — {name: [(t, vel)]}
— so custom tracks feed the same `pulse:` streams as everything else, and no
other part of the pipeline needs to know the difference.

    tools/analyze.py tracks/whatever.mp3          # print what it found
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy import signal

SR = 44100
HOP = 512                 # ~11.6 ms between analysis frames
WIN = 2048

# (name, low_hz, high_hz, min_gap_s) — min_gap is a refractory period, the
# shortest believable spacing between two separate hits in that band. Bass
# notes are further apart than hi-hats, and without this a single kick
# smeared across a few frames reads as a burst of four.
BANDS = [
    ("onset_low",  20,    200,   0.16),
    ("onset_mid",  200,   2000,  0.11),
    ("onset_high", 2000,  16000, 0.09),
]


def load_audio(path: Path, sr: int = SR) -> np.ndarray:
    """Decode anything ffmpeg understands to mono float32."""
    out = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-i", str(path),
         "-f", "f32le", "-ac", "1", "-ar", str(sr), "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(out, dtype="<f4").astype(np.float64)


def _flux(mag: np.ndarray) -> np.ndarray:
    """Positive spectral flux: how much energy APPEARED since last frame.

    Only rises count. Energy dying away is a note ending, which is not a
    thing to flash a light at.
    """
    d = np.diff(mag, axis=1, prepend=mag[:, :1])
    return np.maximum(d, 0.0).sum(axis=0)


def _pick_peaks(env: np.ndarray, times: np.ndarray, min_gap: float,
                sensitivity: float) -> list[tuple[float, float]]:
    """Adaptive-threshold peak picking.

    A fixed threshold fails on any real music: a quiet intro and a loud
    chorus need different bars. The threshold here is a running median plus a
    margin, so it tracks the material.
    """
    if env.max() <= 0:
        return []
    env = env / env.max()

    # Running median over ~0.5 s, as the local "normal" level.
    k = max(3, int(0.5 / (HOP / SR)) | 1)
    local = signal.medfilt(env, kernel_size=k)
    thresh = local + sensitivity * (env.std() + 1e-9)

    hits = []
    last = -1e9
    for i in range(1, len(env) - 1):
        if env[i] < thresh[i]:
            continue
        if not (env[i] >= env[i - 1] and env[i] >= env[i + 1]):
            continue                       # local maximum only
        t = float(times[i])
        if t - last < min_gap:
            # Too close to the previous hit: keep whichever is stronger.
            if hits and env[i] > hits[-1][1]:
                hits[-1] = (t, float(env[i]))
                last = t
            continue
        hits.append((max(0.0, t), float(env[i])))
        last = t

    if not hits:
        return []
    # Rescale velocities so the loudest hit in the band is 1.0 — the pulse
    # config sets absolute intensity, this only carries relative dynamics.
    peak = max(v for _, v in hits)
    return [(t, round(min(1.0, v / peak), 3)) for t, v in hits]


def analyze(x: np.ndarray, sr: int = SR, sensitivity: float = 1.1,
            bands=BANDS) -> dict[str, list[tuple[float, float]]]:
    """Band-split onset detection. Returns {band_name: [(seconds, velocity)]}."""
    if len(x) < WIN * 2:
        return {}
    # Pad the front with silence so a hit at t=0 has something to rise from.
    # Without it the first frame diffs against itself, and a track that opens
    # on its downbeat — which is most loops — loses that downbeat.
    pad = WIN
    x = np.concatenate([np.zeros(pad), x])
    f, t, Z = signal.stft(x, fs=sr, nperseg=WIN, noverlap=WIN - HOP)
    t = t - pad / sr
    mag = np.abs(Z)
    # Log-compress: onsets are perceived roughly logarithmically, and without
    # this a loud section's flux dwarfs everything else in the file.
    mag = np.log1p(mag * 100.0)

    out = {}
    for name, lo, hi, gap in bands:
        sel = (f >= lo) & (f < hi)
        if not sel.any():
            continue
        env = _flux(mag[sel])
        # Light smoothing kills frame-level jitter without moving the peaks.
        env = np.convolve(env, np.hanning(5) / np.hanning(5).sum(), mode="same")
        hits = _pick_peaks(env, t, gap, sensitivity)
        if hits:
            out[name] = hits
    return out


def analyze_file(path: Path, **kw) -> dict[str, list[tuple[float, float]]]:
    return analyze(load_audio(path), **kw)


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(f"usage: {sys.argv[0]} <audio-file> [sensitivity]")
    p = Path(sys.argv[1])
    sens = float(sys.argv[2]) if len(sys.argv) > 2 else 1.1
    x = load_audio(p)
    dur = len(x) / SR
    marks = analyze(x, sensitivity=sens)
    print(f"{p.name}  {dur:.1f}s  peak {np.abs(x).max():.2f}")
    print("-" * 52)
    for name, hits in marks.items():
        rate = len(hits) / dur * 60
        print(f"  {name:<11} {len(hits):>4} onsets  ({rate:>5.0f}/min)  "
              f"first: {', '.join(f'{t:.2f}s' for t, _ in hits[:5])}")
    if not marks:
        print("  no onsets found — try a lower sensitivity, e.g. 0.6")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
