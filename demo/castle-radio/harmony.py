"""Colour from harmony, for option 12 (Ensemble): the song's key, a chord per
bar, and what each chord MEANS in that key — which is what the colour
follows, not the chord's letter name.

A chord is read off the separated bass and "other" stems (the band without
drums or singer): a chroma — energy per pitch class — the bass weighted for
the root, matched against the 24 major and minor triads. The key is the
Krumhansl-Kessler profile that best fits the whole song's chroma. Measured
on the library: Halloween Theme reads F# minor and Thriller C# minor, both
right.

A bar's MOOD is the chord's place in the key:

- home   the key's own chord (either quality — a minor song's home is minor)
- away   another major chord of the key (IV and V; III, VI, VII in minor)
- shadow another minor chord of the key (ii, iii, vi; iv and v in minor)
- strange a chord from outside the key — the chromatic lurch horror music
  lives on.

Offline and lab-only: the stems are decoded with ffmpeg and the chroma is
cached as JSON in the lab's own directory. No device, no audio playback.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import exe_paths

RATE = 11025
WINDOW = 8192  # 0.74 s: a bass note's period many times over
HOP = 1024  # 93 ms
BASS_HZ, OTHER_HZ = (30.0, 300.0), (100.0, 1500.0)
KK_MAJOR = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
KK_MINOR = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)
MAJOR_SCALE, MINOR_SCALE = {0, 2, 4, 5, 7, 9, 11}, {0, 2, 3, 5, 7, 8, 10, 11}
QUIET = 0.05  # of the song's loudest frame: a bar this quiet has no chord
MOODS = ("home", "away", "shadow", "strange")


@dataclass(frozen=True)
class Chroma:
    """Per-frame pitch-class energy of the bass and of the rest of the band,
    each frame normalised to sum 1, with the frame's loudness (0..1)."""

    hop_ms: float
    bass: tuple[tuple[float, ...], ...]
    other: tuple[tuple[float, ...], ...]
    loud: tuple[float, ...]


@dataclass(frozen=True)
class Chord:
    root: int  # pitch class, 0 = C
    minor: bool


def decode(path: Path) -> np.ndarray:
    raw = subprocess.run(
        [exe_paths.ffmpeg(), "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1",
         "-ar", str(RATE), "-"],
        capture_output=True, check=True,
    ).stdout  # fmt: skip
    return np.frombuffer(raw, dtype=np.float32).astype(np.float64)


def chroma(samples: np.ndarray, band: tuple[float, float]) -> np.ndarray:
    """(frames, 12) energy per pitch class between `band` Hz, every HOP."""
    if len(samples) < WINDOW:
        return np.zeros((0, 12))
    frames = np.lib.stride_tricks.sliding_window_view(samples, WINDOW)[::HOP]
    power = np.abs(np.fft.rfft(frames * np.hanning(WINDOW), axis=1)) ** 2
    freqs = np.fft.rfftfreq(WINDOW, 1 / RATE)
    keep = (freqs >= band[0]) & (freqs <= band[1])
    pcs = np.round(12 * np.log2(freqs[keep] / 440.0) + 69).astype(int) % 12
    out = np.zeros((len(frames), 12))
    for pc in range(12):
        out[:, pc] = power[:, keep][:, pcs == pc].sum(axis=1)
    return out


def _unit(rows: np.ndarray) -> np.ndarray:
    out: np.ndarray = rows / np.maximum(rows.sum(axis=1, keepdims=True), 1e-12)
    return out


def measure(bass: np.ndarray, other: np.ndarray) -> Chroma:
    b, o = chroma(bass, BASS_HZ), chroma(other, OTHER_HZ)
    n = min(len(b), len(o))
    b, o = b[:n], o[:n]
    level = np.sqrt(b.sum(axis=1) + o.sum(axis=1))
    loud = level / max(float(level.max()) if n else 0.0, 1e-12)
    return Chroma(
        HOP * 1000 / RATE,
        tuple(tuple(round(float(v), 3) for v in row) for row in _unit(b)),
        tuple(tuple(round(float(v), 3) for v in row) for row in _unit(o)),
        tuple(round(float(v), 3) for v in loud),
    )


def track(stems: Path, cache: Path) -> Chroma | None:
    """The band's chroma from `stems`/bass.mp3 and other.mp3, from `cache`
    when it is newer than both; None when the stems are not there."""
    bass, other = stems / "bass.mp3", stems / "other.mp3"
    if not (bass.is_file() and other.is_file()):
        return None
    newest = max(bass.stat().st_mtime, other.stat().st_mtime)
    if cache.is_file() and cache.stat().st_mtime >= newest:
        doc = json.loads(cache.read_text(encoding="utf-8"))
        return Chroma(doc["hop_ms"], tuple(map(tuple, doc["bass"])),
                      tuple(map(tuple, doc["other"])), tuple(doc["loud"]))  # fmt: skip
    out = measure(decode(bass), decode(other))
    cache.write_text(json.dumps({"hop_ms": out.hop_ms, "bass": out.bass,
                                 "other": out.other, "loud": out.loud}), encoding="utf-8")  # fmt: skip
    return out


def _resolutions(chords: Sequence[Chord | None], tonic: int) -> int:
    """How often the song goes from the dominant (a fifth up) to `tonic`."""
    return sum(
        1 for a, b in pairwise(chords)
        if a and b and a.root == (tonic + 7) % 12 and b.root == tonic
    )  # fmt: skip


def key(c: Chroma, chords: Sequence[Chord | None] = ()) -> Chord:
    """The Krumhansl-Kessler key: its tonic and whether it is minor. The
    profiles cannot tell a key from its relative (G major from E minor share
    every note), so between the two `chords` (one a bar) decide: the tonic
    its dominant resolves to more often (D → G, not B → Em), and on a tie
    the one that opens more phrases (the first of every four bars)."""
    total = np.array(c.other).sum(axis=0) + np.array(c.bass).sum(axis=0)
    best = (-2.0, 0, False)
    for minor, profile in ((False, KK_MAJOR), (True, KK_MINOR)):
        for root in range(12):
            fit = float(np.corrcoef(np.roll(profile, root), total)[0, 1])
            if fit > best[0]:
                best = (fit, root, minor)
    found = Chord(best[1], best[2])
    other = Chord((found.root + (3 if found.minor else -3)) % 12, not found.minor)
    home, away = _resolutions(chords, found.root), _resolutions(chords, other.root)
    if home != away:
        return other if away > home else found
    roots = [ch.root for ch in chords[::4] if ch is not None]
    return other if roots.count(other.root) > roots.count(found.root) else found


TRIADS = np.zeros((24, 12))
for _r in range(12):
    TRIADS[_r, [_r, (_r + 4) % 12, (_r + 7) % 12]] = 1.0
    TRIADS[12 + _r, [_r, (_r + 3) % 12, (_r + 7) % 12]] = 1.0


def chord(c: Chroma, start: int, end: int) -> Chord | None:
    """The triad that best explains `start`..`end` ms, or None when it is quiet."""
    a = max(0, int(start / c.hop_ms))
    b = max(a + 1, min(len(c.loud), int(end / c.hop_ms)))
    if a >= len(c.loud) or max(c.loud[a:b]) < QUIET:
        return None
    bass = np.array(c.bass[a:b]).mean(axis=0)
    other = np.array(c.other[a:b]).mean(axis=0)
    score = TRIADS @ (other + 0.5 * bass) / 3 + 0.5 * np.concatenate([bass, bass])
    best = int(np.argmax(score))
    return Chord(best % 12, best >= 12)


def mood(ch: Chord, home: Chord) -> str:
    degree = (ch.root - home.root) % 12
    if degree == 0:
        return "home"
    scale = MINOR_SCALE if home.minor else MAJOR_SCALE
    third = (degree + (3 if ch.minor else 4)) % 12
    if degree not in scale or third not in scale:
        return "strange"
    return "shadow" if ch.minor else "away"


def bar_moods(c: Chroma, bars: Sequence[tuple[int, int]]) -> list[str | None]:
    """A mood per bar, a lone bar between two of one mood taken as theirs
    (a passing chord is not a change of colour), None where there is no chord."""
    chords = [chord(c, a, b) for a, b in bars]
    home = key(c, chords)  # bars run from the first downbeat, 4 a phrase
    raw = [mood(ch, home) if ch else None for ch in chords]
    out = list(raw)
    for i in range(1, len(raw) - 1):
        if raw[i - 1] == raw[i + 1] and raw[i] != raw[i - 1] and raw[i - 1]:
            out[i] = raw[i - 1]
    return out
