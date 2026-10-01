"""The singer's pitch, for the lab: a YIN pitch track of the separated vocal
stem, so a light can follow the melody around the door instead of only
flashing on each syllable.

Offline and lab-only: it decodes `vocals.mp3` with ffmpeg and caches the
track as JSON in the lab's own output directory — never beside the library's
files. No device, no audio playback.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import exe_paths

RATE = 16000
HOP = 160  # 10 ms
WINDOW = 512  # 32 ms of comparison
LOW_HZ, HIGH_HZ = 75.0, 1000.0
THRESHOLD = 0.15  # YIN's aperiodicity cut: under this the frame is voiced
QUIET = 0.02  # a frame this far under the stem's loudest is silence


def decode(path: Path) -> np.ndarray:
    """Mono float32 samples at RATE."""
    raw = subprocess.run(
        [exe_paths.ffmpeg(), "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1",
         "-ar", str(RATE), "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64)


def cmnd_blocks(
    samples: np.ndarray, chunk: int = 2048
) -> Iterator[tuple[np.ndarray, np.ndarray, float]]:
    """YIN's cumulative-mean-normalised difference for every HOP, a block of
    frames at a time: (cmnd rows, each frame's RMS, the silence floor)."""
    hi = int(RATE / LOW_HZ) + 1
    span = WINDOW + hi
    if len(samples) < span:
        return
    frames = np.lib.stride_tricks.sliding_window_view(samples, span)[::HOP]
    energy = np.sqrt((frames[:, :WINDOW] ** 2).mean(axis=1))
    floor = QUIET * (energy.max() if energy.size else 0.0)
    size = 1 << math.ceil(math.log2(2 * span))
    taus = np.arange(1, hi)
    for at in range(0, len(frames), chunk):
        block = frames[at : at + chunk]
        head = block[:, :WINDOW]
        # r(tau) = sum_j x[j] x[j + tau] over the first WINDOW samples.
        spec = np.fft.rfft(block, size) * np.conj(np.fft.rfft(head, size))
        ac = np.fft.irfft(spec, size)[:, :hi]
        sq = np.cumsum(np.pad(block**2, ((0, 0), (1, 0))), axis=1)
        e0 = sq[:, WINDOW][:, None]
        et = sq[:, WINDOW : WINDOW + hi] - sq[:, :hi]
        diff = np.maximum(e0 + et - 2 * ac, 0.0)
        running = np.cumsum(diff[:, 1:], axis=1)
        cmnd = np.ones_like(diff)
        cmnd[:, 1:] = diff[:, 1:] * taus / np.maximum(running, 1e-12)
        yield cmnd, energy[at : at + chunk], floor


def yin(samples: np.ndarray, chunk: int = 2048) -> list[float | None]:
    """f0 in Hz every HOP samples, None where unvoiced (de Cheveigné & Kawahara
    2002: cumulative-mean-normalised difference, absolute threshold)."""
    lo, hi = int(RATE / HIGH_HZ), int(RATE / LOW_HZ) + 1
    out: list[float | None] = []
    for cmnd, energy, floor in cmnd_blocks(samples, chunk):
        for row, level in zip(cmnd, energy, strict=True):
            out.append(_pick(row, lo, hi) if level > floor else None)
    return out


def _pick(row: np.ndarray, lo: int, hi: int) -> float | None:
    under = np.nonzero(row[lo:hi] < THRESHOLD)[0]
    if under.size == 0:
        return None
    tau = lo + int(under[0])
    while tau + 1 < hi and row[tau + 1] < row[tau]:
        tau += 1
    if 0 < tau < hi - 1:  # parabolic refinement between the neighbours
        a, b, c = row[tau - 1], row[tau], row[tau + 1]
        bend = a - 2 * b + c
        shift = 0.5 * (a - c) / bend if bend else 0.0
        return RATE / (tau + shift)
    return RATE / tau


@dataclass(frozen=True)
class Pitch:
    """A pitch track: MIDI note numbers (fractional) every `hop_ms`."""

    hop_ms: int
    notes: tuple[float | None, ...]

    def at(self, start: int, end: int) -> float | None:
        """The median note sung between `start` and `end` ms, if voiced for
        at least a third of it."""
        a = max(0, start // self.hop_ms)
        b = max(a + 1, min(len(self.notes), end // self.hop_ms))
        voiced = [n for n in self.notes[a:b] if n is not None]
        if not voiced or len(voiced) * 3 < b - a:
            return None
        return float(np.median(voiced))


def midi(hz: float | None) -> float | None:
    return None if not hz else round(69 + 12 * math.log2(hz / 440.0), 2)


def track(vocals: Path, cache: Path) -> Pitch:
    """The stem's pitch track, from `cache` when it is newer than the stem."""
    if cache.is_file() and cache.stat().st_mtime >= vocals.stat().st_mtime:
        doc = json.loads(cache.read_text(encoding="utf-8"))
        return Pitch(doc["hop_ms"], tuple(doc["notes"]))
    notes = tuple(midi(f) for f in yin(decode(vocals)))
    cache.write_text(
        json.dumps({"hop_ms": HOP * 1000 // RATE, "notes": notes}), encoding="utf-8"
    )
    return Pitch(HOP * 1000 // RATE, notes)
