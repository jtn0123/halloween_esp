"""The room and the master bus: reverb, and the limiter before the WAV.

Split from synth.py at the 500-line cap along the seam that was already
drawn there ("room and master"): the voices compose the music, this file
makes it sit in a stone hall and fit the DAC. castle-core's Rust port
(typesafe plan B3, core/src/master.rs) matches `limit` bit for bit;
`apply_reverb`'s fftconvolve is the one deliberately unported piece —
matching pocketfft's rounding is its own project.
"""

from __future__ import annotations

import numpy as np
from scipy import signal

SR = 44100  # synth.py's SR, restated to keep this import-cycle-free


def reverb_ir(secs: float, decay: float, rng: np.random.Generator) -> np.ndarray:
    n = int(secs * SR)
    return rng.uniform(-1.0, 1.0, n) * (1.0 - np.arange(n) / n) ** decay


def apply_reverb(x: np.ndarray, wet: float, rng: np.random.Generator) -> np.ndarray:
    """The stone hall. Without it the organ is just a synth patch."""
    if wet <= 0:
        return x
    ir = reverb_ir(3.4, 2.4, rng)
    tail = signal.fftconvolve(x, ir)[: len(x)]
    peak = np.max(np.abs(tail))
    if peak > 0:
        tail /= peak
    return np.asarray(x + wet * tail * np.max(np.abs(x) + 1e-9))


def _avg_same(x: np.ndarray, win: int) -> np.ndarray:
    """Moving average with np.convolve's "same" window placement, written
    as cumulative-sum differences. Deliberate: np.convolve hands each
    window to the BLAS dot under numpy, and BLAS summation order is a
    vendor choice — the same render carried different low bits under
    Accelerate and OpenBLAS. cumsum is defined-order, so the limiter's
    gain ride is now reproducible across machines, and castle-core's Rust
    port (typesafe plan B3) is held to it bit for bit."""
    n = len(x)
    cs = np.zeros(n + 1)
    np.cumsum(x, out=cs[1:])
    idx = np.arange(n) + ((win - 1) // 2 + 1)
    hi = np.minimum(idx, n)
    lo = np.maximum(idx - win, 0)
    return np.asarray((cs[hi] - cs[lo]) * (1.0 / win))


def limit(x: np.ndarray, ceiling: float = 0.89) -> np.ndarray:
    """Lookahead limiter. Two overlapping organ chords clip without it.

    Smoothed peak envelope -> per-sample gain -> smoothed again so the gain
    ride is inaudible. Hard clip at the end is a backstop, not the mechanism.
    """
    if np.max(np.abs(x)) <= 0:
        return x
    win = max(1, int(0.005 * SR))
    env = _avg_same(np.abs(x), win)
    gain = np.ones_like(env)
    over = env > ceiling
    gain[over] = ceiling / env[over]
    smooth = max(1, int(0.02 * SR))
    # Pad with the edge value rather than letting the average assume zeros
    # past the ends. Without it the first and last ~10 ms came back at
    # roughly half gain — inaudible as a de-click on a one-shot, but wind and
    # drone loop, so the seam dipped on every pass.
    pad = smooth // 2
    padded = np.pad(gain, pad, mode="edge")
    gain = _avg_same(padded, smooth)[pad : pad + len(env)]
    return np.asarray(np.clip(x * gain, -1.0, 1.0))
