"""Shared fixtures for the track tests.

A click track with beats at known times is the backbone of every analysis
test here: onset detection is only meaningful when there is a right answer
to compare against. The synth section at the bottom is the equivalent for the
offline renderer: one way to render a voice and a handful of ways to measure
what came back, shared by test_synth_voices.py and test_synth_pieces.py.
"""

from __future__ import annotations

import os
import sys
import wave
from pathlib import Path

# Hermetic suite, by construction. The emulator workflow (CLAUDE.md) has you
# export CASTLE_HOST / CASTLE_TRACKS in the very shell you then run `make
# test` from, and six tests used to go red on that alone. The sandbox knobs
# are cleared HERE, before any tools module reads them at import time
# (studio_tracks.TRACKS is bound at import), and every case that needs one
# sets it explicitly. unittest discovery loads test_analysis.py — which
# imports this — before any other module, so the whole run sees a clean env.
SANDBOX_ENV = ("CASTLE_HOST", "CASTLE_TRACKS", "CASTLE_SCENES", "CASTLE_BUILD")
for _k in SANDBOX_ENV:
    os.environ.pop(_k, None)

from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import analyze as ana
import synth

# Long enough that every fade knee in wind/drone (they reference dur - 1.5 and
# dur - 3.0) is still in order, short enough that the whole registry renders in
# under a second.
SYNTH_DUR = 6.0

# The voices that report their own event times for the lights.
MARKER_SYNTHS = ("heartbeat", "whispers", "toll", "organ", "waltz")

# Voices built from oscillators rather than noise: their sample-to-sample slew
# is bounded by the highest partial, so a discontinuity means something there.
TONAL_SYNTHS = ("drone", "toll", "organ", "descent", "waltz", "musicbox", "heartbeat")


def make_click_track(
    path: Path, *, seconds: float = 6.0, bpm: float = 120.0, hats: bool = True
) -> list[float]:
    """A file with beats at known times, so onset detection has a right answer."""
    sr = ana.SR
    x = np.zeros(int(seconds * sr))
    period = 60.0 / bpm
    beats = []

    def place(sig: np.ndarray, at: float) -> None:
        i = int(at * sr)
        k = min(len(sig), len(x) - i)
        if k > 0:
            x[i : i + k] += sig[:k]

    t = 0.0
    rng = np.random.default_rng(7)
    while t < seconds - 0.3:
        n = int(0.18 * sr)
        tt = np.arange(n) / sr
        place(np.sin(2 * np.pi * 55 * tt) * np.exp(-tt * 22) * 0.9, t)  # kick
        beats.append(t)
        if hats:
            hn = int(0.05 * sr)
            ht = np.arange(hn) / sr
            place(rng.standard_normal(hn) * np.exp(-ht * 90) * 0.25, t + period / 2)
        t += period

    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return beats


def render_synth(
    name: str, dur: float = SYNTH_DUR, seed: int = 1234
) -> tuple[np.ndarray, Any]:
    """Render one registry entry the way render_audio.py calls it.

    Always returns (buf, marks) — marks is None for the voices that report
    nothing — so callers do not each repeat the tuple check.
    """
    out = synth.SYNTHS[name](np.random.default_rng(seed), dur=dur)
    return out if isinstance(out, tuple) else (out, None)


def peak(x: np.ndarray) -> float:
    return float(np.max(np.abs(x))) if len(x) else 0.0


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x))))


def window_peaks(x: np.ndarray, ms: float = 20.0) -> np.ndarray:
    """Peak amplitude per short window — a cheap amplitude envelope."""
    w = max(1, int(ms * 1e-3 * synth.SR))
    return np.array(
        [np.max(np.abs(x[i * w : (i + 1) * w])) for i in range(len(x) // w)]
    )
