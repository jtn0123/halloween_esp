"""Beats, bars and phrases from the onsets the importer already keeps.

The prepared show fires on every detected onset, which has no pulse a viewer
can follow. This finds the pulse: a tempo by autocorrelation, beats by the
usual dynamic programme over the onset envelope (Ellis 2007), the bar phase
from where the low band lands, and an energy class per phrase from the
loudness peaks. Pure Python on ~10 ms frames; no audio is read or played.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

FRAME_MS = 10
MIN_BPM, MAX_BPM = 84.0, 176.0
BEATS_PER_BAR = 4
BARS_PER_PHRASE = 4
SNAP_MS = 70

Onsets = Sequence[Sequence[float]]


@dataclass(frozen=True)
class Phrase:
    start: int  # ms
    end: int  # ms
    beats: tuple[int, ...]  # ms; beats[pickup] is the first downbeat
    pickup: int  # beats before that downbeat — only the first phrase has any
    energy: float  # 0..1, against the loudest phrase of the song
    rank: int  # 0 quiet, 1 middle, 2 loud — thirds of this song
    vocal: bool


@dataclass(frozen=True)
class Grid:
    bpm: float
    beats: tuple[int, ...]  # ms
    downbeat: int  # index into beats of the first bar's beat one
    phrases: tuple[Phrase, ...]


def envelope(bands: Mapping[str, Onsets], duration_ms: int) -> list[float]:
    """Onset strength per frame; the low band counts double — it is the kick
    and the bass that a listener taps to."""
    env = [0.0] * (duration_ms // FRAME_MS + 2)
    for name, hits in bands.items():
        weight = 2.0 if name.endswith("low") else 1.0
        for hit in hits:
            at = round(hit[0] * 1000 / FRAME_MS)
            for offset, share in ((-1, 0.5), (0, 1.0), (1, 0.5)):
                if 0 <= at + offset < len(env):
                    env[at + offset] += weight * share * hit[1]
    return env


def tempo(env: Sequence[float]) -> float:
    """Beats per minute: the autocorrelation peak inside MIN..MAX_BPM, leaning
    gently towards 120 so a half/double tie resolves the walkable way."""
    best, best_score = 120.0, -1.0
    low = round(60000 / MAX_BPM / FRAME_MS)
    high = round(60000 / MIN_BPM / FRAME_MS)
    for lag in range(low, high + 1):
        total = sum(env[i] * env[i + lag] for i in range(len(env) - lag))
        # Twice the lag supports the same pulse; count it so a lone strong
        # off-beat cannot outvote the bar.
        total += 0.5 * sum(env[i] * env[i + 2 * lag] for i in range(len(env) - 2 * lag))
        bpm = 60000 / (lag * FRAME_MS)
        score = total * math.exp(-0.5 * (math.log2(bpm / 120) / 0.9) ** 2)
        if score > best_score:
            best, best_score = bpm, score
    return best


def track(env: Sequence[float], bpm: float, tightness: float = 6.0) -> list[int]:
    """Beat times in ms. Each frame's score is its own onset strength plus the
    best predecessor about one period back, so the grid bends with a human
    drummer instead of drifting off one."""
    period = 60000 / bpm / FRAME_MS
    peak = max(env) or 1.0
    score = [v / peak for v in env]
    back = [-1] * len(env)
    near, far = round(period / 2), round(period * 2)
    for t in range(len(env)):
        best, arg = 0.0, -1
        for prev in range(max(0, t - far), t - near + 1):
            value = score[prev] - tightness * math.log((t - prev) / period) ** 2
            if value > best:
                best, arg = value, prev
        if arg >= 0:
            score[t] += best
            back[t] = arg
    tail = max(0, len(env) - round(period))
    at = max(range(tail, len(env)), key=lambda i: score[i])
    beats = []
    while at >= 0:
        beats.append(at * FRAME_MS)
        at = back[at]
    return beats[::-1]


def snap(beats: Sequence[int], hits: Onsets) -> list[int]:
    """Move each beat onto the real onset beside it, when there is one — the
    light should land on the drum, not on the arithmetic."""
    times = sorted(round(h[0] * 1000) for h in hits)
    out, i = [], 0
    for beat in beats:
        while i + 1 < len(times) and abs(times[i + 1] - beat) <= abs(times[i] - beat):
            i += 1
        near = times[i] if times else beat
        out.append(near if abs(near - beat) <= SNAP_MS else beat)
    return out


def bar_phase(beats: Sequence[int], low: Onsets) -> int:
    """Which beat (0..3) is 'one': the phase the low band hits hardest."""
    weight = [0.0] * BEATS_PER_BAR
    hits = sorted((round(h[0] * 1000), h[1]) for h in low)
    j = 0
    for index, beat in enumerate(beats):
        while j < len(hits) and hits[j][0] < beat - SNAP_MS:
            j += 1
        k = j
        while k < len(hits) and hits[k][0] <= beat + SNAP_MS:
            weight[index % BEATS_PER_BAR] += hits[k][1]
            k += 1
    return max(range(BEATS_PER_BAR), key=lambda p: weight[p])


def _mean(peaks: Sequence[float], start: int, end: int, duration_ms: int) -> float:
    if not peaks:
        return 0.0
    a = max(0, min(len(peaks) - 1, start * len(peaks) // duration_ms))
    b = max(a + 1, min(len(peaks), end * len(peaks) // duration_ms))
    return sum(peaks[a:b]) / (b - a)


def phrases(
    beats: Sequence[int],
    downbeat: int,
    duration_ms: int,
    loud: Sequence[float],
    voice: Sequence[float],
) -> list[Phrase]:
    span = BEATS_PER_BAR * BARS_PER_PHRASE
    blocks = [list(beats[i : i + span]) for i in range(downbeat, len(beats), span)]
    blocks = [b for b in blocks if b]
    if downbeat and blocks:
        blocks[0] = list(beats[:downbeat]) + blocks[0]
    raw = []
    for i, block in enumerate(blocks):
        start = 0 if i == 0 else block[0]
        end = blocks[i + 1][0] if i + 1 < len(blocks) else duration_ms
        raw.append((start, end, block, _mean(loud, start, end, duration_ms)))
    top = max((r[3] for r in raw), default=0.0) or 1.0
    ordered = sorted(r[3] for r in raw)
    cuts = (ordered[len(ordered) // 3], ordered[2 * len(ordered) // 3]) if raw else ()
    sung = max(voice, default=0.0) * 0.18
    return [
        Phrase(
            start,
            end,
            tuple(block),
            downbeat if start == 0 else 0,
            energy / top,
            sum(energy > c for c in cuts),
            _mean(voice, start, end, duration_ms) > sung,
        )
        for start, end, block, energy in raw
    ]


def analyse(layers: Mapping[str, Any], duration_ms: int) -> Grid:
    """`layers` is the stem analysis (`backing`, optional `vocals`); a song
    with no stems passes its whole-mix analysis as `backing`."""
    backing = layers["backing"]["both"]
    env = envelope(backing["onsets"], duration_ms)
    bpm = tempo(env)
    low = backing["onsets"].get("onset_low", [])
    every = [h for hits in backing["onsets"].values() for h in hits]
    beats = snap(track(env, bpm), every)
    beats = [b for b in beats if b < duration_ms - 100]
    downbeat = bar_phase(beats, low)
    voice = (layers.get("vocals") or {}).get("both", {}).get("peaks", [])
    found = phrases(beats, downbeat, duration_ms, backing["peaks"], voice)
    return Grid(bpm, tuple(beats), downbeat, tuple(found))
