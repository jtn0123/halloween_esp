"""Beats, bars and phrases from the onsets the importer already keeps.

The prepared show fires on every detected onset, which has no pulse a viewer
can follow. This finds the pulse: a tempo by autocorrelation, beats by the
usual dynamic programme over the onset envelope (Ellis 2007), the bar phase,
and an energy class per phrase from the loudness peaks. Pure Python on
~10 ms frames; no audio is read or played.

Which onsets it listens to depends on the split (tools/stems.py). A
four-stem split carries `drums`, `bass` and `other`: then the pulse comes
from the kit (with the backing underneath, so a drumless intro still has
beats), and the bar's "one" from the pattern a drummer plays — kick on 1 and
3, snare on the backbeat (2 and 4) — with a heavier kick on 1, the bass and
chords landing on the downbeat, and the song's section changes deciding 1
against 3, which the backbeat alone cannot. Without a kit (a two-stem split,
a drum stem of bleed) it falls back to the backing's onsets and takes "one"
to be where the low band hits hardest, which can sit a beat off.
`Grid.source`, `downbeat_cue` and `downbeat_margin` say which path ran,
which evidence decided, and by how much.
"""

from __future__ import annotations

import bisect
import itertools
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

FRAME_MS = 10
MIN_BPM, MAX_BPM = 84.0, 176.0
BEATS_PER_BAR = 4
BARS_PER_PHRASE = 4
SNAP_MS = 70
#: The drum stem's bands, weighed for the pulse: the kick double, the snare
#: half as much again, the hats halved — they tick the off-beats too, and at
#: full weight they pull the tempo towards double time.
DRUM_WEIGHTS = {"onset_low": 2.0, "onset_mid": 1.5, "onset_high": 0.5}
#: How much of the backing's envelope rides under the drum stem's.
BACKING_BLEND = 0.5
#: Fewer kick + snare hits a second than this and the drum stem is bleed
#: from the other instruments, not a kit: the grid falls back to the backing.
KIT_RATE = 0.5
#: How loud the drum stem must peak against the backing it is part of.
KIT_LEVEL = 0.1
#: How much each witness to "one" counts once its onsets are shares of the
#: bar. Section changes are the weakest (a fill blurs where a section turns,
#: a singer comes in on a pickup), so they count for less.
CUE_WEIGHTS = {
    "backbeat": 1.0,
    "kick": 1.0,
    "bass": 1.0,
    "harmony": 1.0,
    "sections": 0.5,
}

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
    source: str = "backing"  # the stem the pulse was found in
    downbeat_cue: str = "low band"  # the evidence that picked beat one
    downbeat_margin: float = 0.0  # 0..1: the winner's lead over the runner-up


def envelope(
    bands: Mapping[str, Onsets],
    duration_ms: int,
    weights: Mapping[str, float] | None = None,
) -> list[float]:
    """Onset strength per frame; by default the low band counts double — it
    is the kick and the bass that a listener taps to."""
    env = [0.0] * (duration_ms // FRAME_MS + 2)
    for name, hits in bands.items():
        if weights is None:
            weight = 2.0 if name.endswith("low") else 1.0
        else:
            weight = weights.get(name, 1.0)
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


def phase_weights(beats: Sequence[int], hits: Onsets) -> list[float]:
    """How hard `hits` land on each beat of the bar (0..3), counting beat 0
    of `beats` as the bar's first — onsets within SNAP_MS of a beat vote."""
    weight = [0.0] * BEATS_PER_BAR
    ordered = sorted((round(h[0] * 1000), h[1]) for h in hits)
    j = 0
    for index, beat in enumerate(beats):
        while j < len(ordered) and ordered[j][0] < beat - SNAP_MS:
            j += 1
        k = j
        while k < len(ordered) and ordered[k][0] <= beat + SNAP_MS:
            weight[index % BEATS_PER_BAR] += ordered[k][1]
            k += 1
    return weight


def bar_phase(beats: Sequence[int], low: Onsets) -> int:
    """Which beat (0..3) is 'one': the phase the low band hits hardest."""
    weight = phase_weights(beats, low)
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


def _merged(bands: Mapping[str, Onsets], *names: str) -> list[Sequence[float]]:
    return [h for n in names for h in bands.get(n, [])]


def _onsets(layers: Mapping[str, Any], name: str) -> Mapping[str, Onsets]:
    """A layer's mono onsets; empty when the split has no such layer."""
    found: Mapping[str, Onsets] = (
        (layers.get(name) or {}).get("both", {}).get("onsets", {})
    )
    return found


def drum_kit(
    layers: Mapping[str, Any], duration_ms: int
) -> Mapping[str, Onsets] | None:
    """The drum stem's onsets, when there is a kit in it worth following —
    None for a two-stem split and for a song whose drum stem is only what
    leaked from the other instruments."""
    drums = (layers.get("drums") or {}).get("both")
    if not drums:
        return None
    onsets: Mapping[str, Onsets] = drums.get("onsets", {})
    hits = len(_merged(onsets, "onset_low", "onset_mid"))
    if hits < KIT_RATE * duration_ms / 1000:
        return None
    backing = layers["backing"]["both"].get("level", 0.0)
    if drums.get("level", 0.0) < KIT_LEVEL * backing:
        return None
    return onsets


def snap_to(beats: Sequence[int], first: Onsets, then: Onsets) -> list[int]:
    """`snap`, preferring `first`: a beat moves onto the nearest `first`
    onset within SNAP_MS, and only when there is none onto a `then` one."""
    times = sorted(round(h[0] * 1000) for h in first)
    out = []
    for beat, fallback in zip(beats, snap(beats, then), strict=True):
        i = bisect.bisect_left(times, beat)
        near = [t for t in times[max(0, i - 1) : i + 1] if abs(t - beat) <= SNAP_MS]
        out.append(min(near, key=lambda t: abs(t - beat)) if near else fallback)
    return out


def _times(bands: Mapping[str, Onsets]) -> list[tuple[int, float]]:
    return sorted((round(h[0] * 1000), h[1]) for hits in bands.values() for h in hits)


def section_changes(
    beats: Sequence[int],
    backing: Mapping[str, Any],
    voice: Mapping[str, Any],
    duration_ms: int,
) -> list[tuple[float, float]]:
    """Beats where the song turns a corner — the band's onset strength over
    the next two bars jumping against the two before, or the singer coming
    in after a bar's rest — as (seconds, weight) hits. Sections start on a
    bar's "one", so these vote for it."""
    span = 2 * BEATS_PER_BAR
    if len(beats) < 2 * span + 1:
        return []
    every = _times(backing["onsets"])
    strength = []
    for a, b in itertools.pairwise([*beats, duration_ms]):
        lo = bisect.bisect_left(every, (a, -math.inf))
        hi = bisect.bisect_left(every, (b, -math.inf))
        strength.append(sum(v for _, v in every[lo:hi]))
    jump = [0.0] * len(beats)
    for i in range(span, len(beats) - span):
        jump[i] = sum(strength[i : i + span]) - sum(strength[i - span : i])
    top = max(jump) or 1.0
    out: list[tuple[float, float]] = []
    for i in range(span, len(beats) - span):
        near = jump[i - BEATS_PER_BAR : i + BEATS_PER_BAR + 1]
        if jump[i] > 0.25 * top and jump[i] == max(near):
            out.append((beats[i] / 1000, jump[i] / top))
    rest = BEATS_PER_BAR * (beats[-1] - beats[0]) / (len(beats) - 1)
    sung = [t for t, _ in _times(voice.get("onsets", {}))]
    for prev, at in zip([-math.inf, *sung], sung, strict=False):
        if at - prev >= rest:
            out.append((at / 1000, 0.5))
    return out


@dataclass(frozen=True)
class Vote:
    phase: int  # which beat of `beats` (0..3) is the bar's one
    cue: str  # the evidence that put it ahead of the runner-up
    margin: float  # the winner's lead in shares of the bar, 0..1


def phase_cues(
    beats: Sequence[int],
    kit: Mapping[str, Onsets],
    bass: Mapping[str, Onsets],
    changes: Onsets,
    harmony: Mapping[str, Onsets] | None = None,
) -> dict[str, list[float]]:
    """Each witness's weighted vote for every beat of the bar being "one".

    The backbeat — kick on 1 and 3, snare on 2 and 4 — tells the odd beats
    from the even ones and cannot tell 1 from 3; a stronger kick on 1, the
    bass and the chords (`harmony`, the `other` stem) landing on the
    downbeat, and the song's section changes do that. Each cue is a share of
    its own onsets per beat of the bar, so a busy stem cannot outvote a
    sparse one by volume alone; a hit counts by its velocity squared, so the
    hats and ghost notes that bleed into a band do not flatten the pattern
    the real hits make.
    """

    def share(hits: Onsets) -> list[float]:
        w = phase_weights(beats, [(h[0], h[1] * h[1]) for h in hits])
        total = sum(w)
        return [x / total for x in w] if total else [0.0] * BEATS_PER_BAR

    kick = share(kit.get("onset_low", []))
    snare = share(kit.get("onset_mid", []))
    shares = {
        "kick": kick,
        "bass": share(_merged(bass, "onset_low")),
        "harmony": share(_merged(harmony or {}, "onset_low", "onset_mid")),
        "sections": share(changes),
    }
    out: dict[str, list[float]] = {"backbeat": []}
    for p in range(BEATS_PER_BAR):
        on = (p, (p + 2) % BEATS_PER_BAR)
        off = ((p + 1) % BEATS_PER_BAR, (p + 3) % BEATS_PER_BAR)
        out["backbeat"].append(
            CUE_WEIGHTS["backbeat"]
            * sum(
                kick[i] + snare[j] - kick[j] - snare[i]
                for i, j in zip(on, off, strict=True)
            )
        )
    for name, w in shares.items():
        out[name] = [
            CUE_WEIGHTS[name] * (w[p] - w[(p + 2) % BEATS_PER_BAR])
            for p in range(BEATS_PER_BAR)
        ]
    return out


def drum_phase(
    beats: Sequence[int],
    kit: Mapping[str, Onsets],
    bass: Mapping[str, Onsets],
    changes: Onsets,
    harmony: Mapping[str, Onsets] | None = None,
) -> Vote:
    """The bar's "one" from the drummer's pattern (`phase_cues`): the beat
    with the most votes, the cue that did most to put it ahead of the
    runner-up, and by how much it led — in shares of a bar's onsets,
    clipped to 1."""
    cues = phase_cues(beats, kit, bass, changes, harmony)
    total = [sum(v[p] for v in cues.values()) for p in range(BEATS_PER_BAR)]
    phase, runner = sorted(range(BEATS_PER_BAR), key=lambda p: -total[p])[:2]
    lead = {k: v[phase] - v[runner] for k, v in cues.items()}
    cue = max(lead, key=lambda k: lead[k])
    return Vote(phase, cue, round(min(1.0, total[phase] - total[runner]), 3))


def analyse(layers: Mapping[str, Any], duration_ms: int) -> Grid:
    """`layers` is the stem analysis (`backing`, optional `vocals`, and from
    a four-stem split `drums` / `bass` / `other`); a song with no stems
    passes its whole-mix analysis as `backing`."""
    backing = layers["backing"]["both"]
    voice = (layers.get("vocals") or {}).get("both", {})
    kit = drum_kit(layers, duration_ms)
    if kit is None:
        env = envelope(backing["onsets"], duration_ms)
        source = "backing"
    else:
        # The band rides along underneath: where the kit rests (an intro, a
        # breakdown) the grid still has something to walk on.
        drummed = envelope(kit, duration_ms, DRUM_WEIGHTS)
        band = envelope(backing["onsets"], duration_ms)
        env = [d + BACKING_BLEND * b for d, b in zip(drummed, band, strict=True)]
        source = "drums"
    bpm = tempo(env)
    every = [h for hits in backing["onsets"].values() for h in hits]
    beats = track(env, bpm)
    if kit is not None:
        # Onto the kick or snare first; a beat the kit rests on still gets
        # whatever the rest of the band played there.
        beats = snap_to(beats, _merged(kit, "onset_low", "onset_mid"), every)
    else:
        beats = snap(beats, every)
    beats = [b for b in beats if b < duration_ms - 100]
    if kit is None:
        low = backing["onsets"].get("onset_low", [])
        downbeat, cue, margin = bar_phase(beats, low), "low band", 0.0
    else:
        changes = section_changes(beats, backing, voice, duration_ms)
        vote = drum_phase(
            beats, kit, _onsets(layers, "bass"), changes, _onsets(layers, "other")
        )
        downbeat, cue, margin = vote.phase, vote.cue, vote.margin
    found = phrases(
        beats, downbeat, duration_ms, backing["peaks"], voice.get("peaks", [])
    )
    return Grid(bpm, tuple(beats), downbeat, tuple(found), source, cue, margin)
