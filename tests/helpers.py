"""Shared fixtures for the track tests.

A click track with beats at known times is the backbone of every analysis
test here: onset detection is only meaningful when there is a right answer
to compare against.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import analyze as ana          # noqa: E402
import import_track as it      # noqa: E402
import manifest as mf          # noqa: E402
import render_audio as ra      # noqa: E402
import yaml                    # noqa: E402




def make_click_track(path: Path, *, seconds: float = 6.0, bpm: float = 120.0,
                     hats: bool = True) -> list[float]:
    """A file with beats at known times, so onset detection has a right answer."""
    sr = ana.SR
    x = np.zeros(int(seconds * sr))
    period = 60.0 / bpm
    beats = []

    def place(sig: np.ndarray, at: float) -> None:
        i = int(at * sr)
        k = min(len(sig), len(x) - i)
        if k > 0:
            x[i:i + k] += sig[:k]

    t = 0.0
    rng = np.random.default_rng(7)
    while t < seconds - 0.3:
        n = int(0.18 * sr)
        tt = np.arange(n) / sr
        place(np.sin(2 * np.pi * 55 * tt) * np.exp(-tt * 22) * 0.9, t)   # kick
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
