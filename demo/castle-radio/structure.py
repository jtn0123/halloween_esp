"""Song structure for the section-aware show, from the stem analysis alone.

beat_grid finds the pulse and cuts the song into 4-bar phrases. This decides
what those phrases ARE to a viewer: which ones are the same music coming back
(so a chorus can return looking like itself), where the band stops dead, how
hard each beat is actually hit, and whether anyone is really singing — a
near-silent vocal stem still yields hundreds of "onsets", which are bleed.

Nothing here reads audio: every input is the analysis.json the importer
already wrote (peaks normalised per channel, with the raw `level` beside them,
and three onset bands). When the four-stem split is present (drums, bass,
other) the rhythm fingerprint listens to those; otherwise to the backing.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from beat_grid import Grid, Phrase

#: A vocal stem this much quieter than the band is bleed or silence, not a singer.
VOICE_FLOOR = 0.05
#: At most this many kinds of passage; more would stop reading as repetition.
MAX_LABELS = 4
SLOTS = 16  # sixteenth notes in a 4/4 bar
Onsets = Sequence[Sequence[float]]


@dataclass(frozen=True)
class Section:
    start: int  # ms
    end: int  # ms
    phrases: tuple[int, ...]  # indices into grid.phrases
    label: int  # 0 is the first kind of passage heard, 1 the second, …
    energy: float  # mean phrase energy, 0..1
    visit: int  # 0 the first time this label is heard, 1 the second, …
    last_visit: bool  # no later section shares the label


def absolute(channel: Mapping[str, Any]) -> list[float]:
    """The channel's envelope on one scale with its twin stems: peaks are
    normalised per channel, so multiply their raw level back in."""
    level = float(channel.get("level") or 0.0)
    return [p * level for p in channel.get("peaks") or []]


def has_singer(layers: Mapping[str, Any]) -> bool:
    vocals = (layers.get("vocals") or {}).get("both") or {}
    backing = (layers.get("backing") or {}).get("both") or {}
    band = float(backing.get("level") or 0.0)
    return float(vocals.get("level") or 0.0) >= VOICE_FLOOR * max(band, 1e-6)


def rhythm_bands(layers: Mapping[str, Any]) -> dict[str, Onsets]:
    """What the fingerprint and the beat strengths listen to."""
    if "drums" in layers:
        drums = layers["drums"]["both"]["onsets"]
        bands = {
            "kick": drums.get("onset_low", []),
            "snare": drums.get("onset_mid", []),
            "hat": drums.get("onset_high", []),
        }
        for name, key in (("bass", "bass"), ("keys", "other")):
            if key in layers:
                bands[name] = layers[key]["both"]["onsets"].get("onset_mid", []) or (
                    layers[key]["both"]["onsets"].get("onset_low", [])
                )
        return bands
    onsets = layers["backing"]["both"]["onsets"]
    return {k: onsets.get(k, []) for k in ("onset_low", "onset_mid", "onset_high")}


def _window(env: Sequence[float], start: int, end: int, duration: int) -> float:
    if not env or duration <= 0:
        return 0.0
    a = max(0, min(len(env) - 1, start * len(env) // duration))
    b = max(a + 1, min(len(env), end * len(env) // duration))
    return sum(env[a:b]) / (b - a)


def _periods(beats: Sequence[int], end: int) -> list[int]:
    out = []
    for j, t in enumerate(beats):
        nxt = beats[j + 1] if j + 1 < len(beats) else end
        out.append(max(250, min(900, nxt - t)))
    return out


def beat_strengths(
    grid: Grid, bands: Mapping[str, Onsets], snap_ms: int = 70
) -> list[float]:
    """How hard each grid beat is hit, 0..1 against the loud beats nearby
    (the 90th percentile of the surrounding 32 beats), so a quiet verse still
    has accents and a loud chorus is not all 1.0."""
    hits = sorted((round(h[0] * 1000), h[1]) for b in bands.values() for h in b)
    times = [h[0] for h in hits]
    raw = []
    for beat in grid.beats:
        lo = bisect_left(times, beat - snap_ms)
        hi = bisect_left(times, beat + snap_ms + 1)
        raw.append(sum(h[1] for h in hits[lo:hi]))
    out = []
    for i, value in enumerate(raw):
        near = sorted(raw[max(0, i - 16) : i + 16])
        top = near[int(0.9 * (len(near) - 1))] if near else 0.0
        out.append(min(1.0, value / top) if top > 0 else 0.0)
    return out


def fingerprint(phrase: Phrase, bands: Mapping[str, Onsets]) -> list[float]:
    """Where in the bar each band hits, summed over the phrase: a 16-slot
    histogram per band, per bar — the groove, not the loudness."""
    beats = phrase.beats
    periods = _periods(beats, phrase.end)
    bars = max(1, (len(beats) - phrase.pickup) // 4)
    vec: list[float] = []
    for hits in bands.values():
        hist = [0.0] * SLOTS
        times = sorted((round(h[0] * 1000), h[1]) for h in hits)
        stamps = [t for t, _ in times]
        for j in range(phrase.pickup, len(beats)):
            k = j - phrase.pickup
            lo = bisect_left(stamps, beats[j])
            hi = bisect_left(stamps, beats[j] + periods[j])
            for at, strength in times[lo:hi]:
                sub = min(3, (at - beats[j]) * 4 // periods[j])
                hist[(k % 4) * 4 + sub] += strength
        vec.extend(v / bars for v in hist)
    return vec


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else (1.0 if na == nb else 0.0)


def similarity(
    a: tuple[list[float], float, float], b: tuple[list[float], float, float]
) -> float:
    """Groove first, then loudness and singing: (fingerprint, energy, voice)."""
    groove = _cosine(a[0], b[0])
    loud = 1.0 - min(1.0, 2.0 * abs(a[1] - b[1]))
    voice = 1.0 - min(1.0, abs(a[2] - b[2]))
    return 0.6 * groove + 0.25 * loud + 0.15 * voice


def _assign(feats: Sequence[tuple[list[float], float, float]], tau: float) -> list[int]:
    labels: list[int] = []
    members: list[list[int]] = []
    for i, f in enumerate(feats):
        scores = [
            sum(similarity(f, feats[m]) for m in group) / len(group)
            for group in members
        ]
        best = max(range(len(scores)), key=scores.__getitem__, default=-1)
        if best >= 0 and scores[best] >= tau:
            labels.append(best)
            members[best].append(i)
        else:
            labels.append(len(members))
            members.append([i])
    return labels


def label_phrases(grid: Grid, layers: Mapping[str, Any], duration: int) -> list[int]:
    """One label per phrase; equal labels are the same music. The threshold is
    the strictest that leaves at most MAX_LABELS kinds of passage that come
    back, plus two that are heard once (an intro, a bridge, an ending)."""
    bands = rhythm_bands(layers)
    singer = has_singer(layers)
    voice_env = absolute((layers.get("vocals") or {}).get("both") or {})
    band_env = absolute(layers["backing"]["both"])
    feats = []
    for p in grid.phrases:
        v = _window(voice_env, p.start, p.end, duration)
        b = _window(band_env, p.start, p.end, duration)
        sung = min(1.0, v / b) if singer and b > 0 else 0.0
        feats.append((fingerprint(p, bands), p.energy, sung))
    for step in range(36):
        labels = _assign(feats, 0.95 - step * 0.01)
        recurring = {x for x in labels if labels.count(x) > 1}
        if len(recurring) <= MAX_LABELS and len(set(labels)) <= MAX_LABELS + 2:
            return labels
    return labels


def sections(grid: Grid, labels: Sequence[int], max_phrases: int = 4) -> list[Section]:
    """Neighbouring phrases with one label become one section, but none runs
    longer than `max_phrases` (about half a minute): a song that never changes
    groove must still change look now and then."""
    runs: list[list[int]] = []
    for i, label in enumerate(labels):
        if runs and labels[runs[-1][-1]] == label and len(runs[-1]) < max_phrases:
            runs[-1].append(i)
        else:
            runs.append([i])
    seen: dict[int, int] = {}
    out = []
    for run in runs:
        label = labels[run[0]]
        visit = seen.get(label, 0)
        seen[label] = visit + 1
        energy = sum(grid.phrases[i].energy for i in run) / len(run)
        out.append((run, label, energy, visit))
    return [
        Section(
            grid.phrases[run[0]].start,
            grid.phrases[run[-1]].end,
            tuple(run),
            label,
            energy,
            visit,
            visit == seen[label] - 1,
        )
        for run, label, energy, visit in out
    ]


def breaks(
    grid: Grid, bands: Mapping[str, Onsets], layers: Mapping[str, Any], duration: int
) -> list[tuple[int, int]]:
    """Where the band stops dead: two or more beats, inside the song, with
    almost no band onsets AND a band envelope far under the song's own, after
    two bars of the band playing. A fade-out or a sparse intro is not a
    break — the band must be going, stop, then come back."""
    beats = list(grid.beats)
    if len(beats) < 12:
        return []
    hits = sorted((round(h[0] * 1000), h[1]) for b in bands.values() for h in b)
    times = [h[0] for h in hits]
    env = absolute(layers["backing"]["both"])
    typical = sorted(env)[len(env) // 2] if env else 0.0
    periods = _periods(beats, duration)
    busy = []
    for j, beat in enumerate(beats):
        lo, hi = (
            bisect_left(times, beat - 35),
            bisect_left(times, beat + periods[j] - 35),
        )
        busy.append(sum(h[1] for h in hits[lo:hi]))
    usual = sorted(busy)[len(busy) // 2] or 1.0
    level = [
        _window(env, beats[j], beats[j] + periods[j], duration) / (typical or 1.0)
        for j in range(len(beats))
    ]
    quiet = [busy[j] < 0.12 * usual and level[j] < 0.4 for j in range(len(beats))]
    # Once stopped, a stray note or a breath does not end the stop: it lasts
    # while the band stays under half its usual loudness and hits less than
    # it usually does (hysteresis). A singer's bleed into the other stems
    # is a few faint onsets; a band coming back is loud.
    still = [busy[j] < usual and level[j] < 0.5 for j in range(len(beats))]
    out = []
    j = 8  # the first two bars may be a count-in
    while j < len(beats) - 8:
        if not quiet[j]:
            j += 1
            continue
        k = j
        while k < len(beats) - 8 and still[k]:
            k += 1
        # A stop needs a band playing before it — a sparse intro is not one —
        # but a band may thin out for a bar first: any of the four bars
        # before will do.
        start = max(0, j - 16)
        before = max(
            sum(busy[b : b + 4]) / 4 for b in range(start, max(start + 1, j - 3))
        )
        if k - j >= 2 and before >= 0.6 * usual and not all(quiet[k : k + 4]):
            out.append((beats[j], beats[k]))
        j = k + 1
    return out
