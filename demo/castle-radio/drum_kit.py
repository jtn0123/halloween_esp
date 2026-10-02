"""Per-drum lights, for option 12 (Ensemble): the drum stem's three bands
played as three instruments on the towers, instead of one pattern that only
knows where the beat is.

- Kick (the stem's low band): the towers' BOTTOM halves thump, in the look's
  first colour — low in the sound, low on the castle.
- Snare (mid): an arc of both towers cracks where the tempo-locked chase is
  (spin_show.Heads), in the answering colour, so the backbeat still turns.
- Kick and snare together: the whole tower.
- Hi-hat (high): a faint sparkle of a few pixels on the ornament layer,
  left tower then right, so it glitters over the drums without cutting them.

The bar's "one" stays the whole castle's unison hit (spin_show), and the
drums take over only where they are really playing — a passage without a
kit keeps option 11's pattern. Transitions, drops and stops are left alone.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from choreography import beat_table, spaced
from looks import WHITE, ZONES, Colour, Cue, strike
from sections_show import Draft
from spin_show import Heads, arc, downbeats

TOWERS = list(ZONES[:2])
FLOOR = 0.35  # of a band's strong hits (90th percentile): weaker is bleed
ONE_MS = 60  # a hit this close to a bar's "one" is the unison hit's
TOGETHER_MS = 30  # a kick and a snare this close are one hit
HAT_GAP = 160  # ms between sparkles, whichever tower
KIT_SHARE = 0.2  # the drum stem against the band: under this there is no kit
Hits = tuple[tuple[int, float], ...]


@dataclass(frozen=True)
class Kit:
    kick: Hits
    snare: Hits
    hat: Hits


def _band(onsets: Sequence[Sequence[float]], gap: int) -> Hits:
    if not onsets:
        return ()
    strong = sorted(h[1] for h in onsets)[int(0.9 * (len(onsets) - 1))] or 1.0
    kept = [(h[0], min(1.0, h[1] / strong)) for h in onsets if h[1] >= FLOOR * strong]
    return tuple(spaced(kept, gap))


def kit(layers: Mapping[str, Any]) -> Kit | None:
    """The drum stem's hits, band by band, or None when there is no kit."""
    drums = (layers.get("drums") or {}).get("both")
    backing = (layers.get("backing") or {}).get("both") or {}
    if not drums or float(drums.get("level") or 0.0) < KIT_SHARE * float(
        backing.get("level") or 1.0
    ):
        return None
    onsets = drums.get("onsets") or {}
    return Kit(
        _band(onsets.get("onset_low", []), 90),
        _band(onsets.get("onset_mid", []), 90),
        _band(onsets.get("onset_high", []), HAT_GAP),
    )


def free_windows(d: Draft) -> list[tuple[int, int]]:
    """Where a phrase's own pattern plays: up to its transition's cut (the
    last two beats before a new section, sections_show._phrase_cues), up to
    the last beat of the song, and never inside a stop or a drop."""
    out = []
    for i, plan in enumerate(d.plans):
        end = plan.phrase.end
        nxt = d.plans[i + 1] if i + 1 < len(d.plans) else None
        table = beat_table(plan.phrase)
        if nxt is None:
            end = plan.phrase.beats[-1]
        elif nxt.section is not plan.section and len(table) >= 2:
            end = table[-2][0]
        out.append((plan.phrase.start, end))
    blocked = [*d.stops, *d.drops]
    return [(a, b) for a, b in out if not any(s < b and a < e for s, e in blocked)]


def playing(k: Kit, start: int, end: int) -> bool:
    """At least a kick or a snare a bar (two seconds, give or take)."""
    hits = sum(1 for hits in (k.kick, k.snare) for t, _s in hits if start <= t < end)
    return hits * 2000 >= end - start


def _near(t: int, times: Sequence[int], ms: int) -> bool:
    return any(abs(t - other) <= ms for other in times)


def _pattern_hit(cue: Cue, ones: Sequence[int]) -> bool:
    """A tower hit of the phrase's pattern — not the unison "one", a white
    slam, a swell or anything on the ornament layer."""
    return (
        cue["op"] == "strike" and any(z in TOWERS for z in cue["targets"])
        and not cue["attack"] and cue["color"] != WHITE and not cue.get("layer")
        and not _near(cue["t"], ones, ONE_MS)
    )  # fmt: skip


def sparkle(colour: Colour) -> Colour:
    return [round(c + (w - c) * 0.5, 3) for c, w in zip(colour, WHITE, strict=True)]


def hits_in(k: Kit, d: Draft, heads: Heads, start: int, end: int) -> list[Cue]:
    """The kit's cues between `start` and `end`."""
    ones = downbeats(d.grid)
    kicks = [
        (t, s) for t, s in k.kick if start <= t < end and not _near(t, ones, ONE_MS)
    ]
    snares = [
        (t, s) for t, s in k.snare if start <= t < end and not _near(t, ones, ONE_MS)
    ]
    out: list[Cue] = []
    for t, s in snares:
        look = d.look_at(t)
        if _near(t, [at for at, _ in kicks], TOGETHER_MS):
            out.append(strike(t, TOWERS, look.a, 0.6 + 0.4 * s, 240))
        else:
            k8 = round(heads.at(t) * 8)
            out.append(strike(t, TOWERS, look.b, 0.5 + 0.45 * s, 260, arc(k8)))
    for t, s in kicks:
        if not _near(t, [at for at, _ in snares], TOGETHER_MS):
            out.append(
                strike(t, TOWERS, d.look_at(t).a, 0.45 + 0.45 * s, 200, "bottom")
            )
    hats = [(t, s) for t, s in k.hat if start <= t < end]
    for i, (t, s) in enumerate(hats):
        colour = sparkle(d.look_at(t).b)
        cue = strike(t, [TOWERS[i % 2]], colour, 0.18 + 0.22 * s, 90, "scatter")
        out.append({**cue, "layer": 1})
    return out


def drummed(band: Sequence[Cue], k: Kit | None, d: Draft, heads: Heads) -> list[Cue]:
    """`band` (option 11's) with the pattern's tower hits replaced by the kit
    wherever the kit is playing."""
    if k is None:
        return list(band)
    ones = downbeats(d.grid)
    windows = [(a, b) for a, b in free_windows(d) if playing(k, a, b)]
    out: list[Cue] = []
    for cue in band:
        if not any(a <= cue["t"] < b for a, b in windows) or not _pattern_hit(
            cue, ones
        ):
            out.append(cue)
            continue
        rest = [z for z in cue["targets"] if z not in TOWERS]
        if rest:
            out.append({**cue, "targets": rest})
    for a, b in windows:
        out += hits_in(k, d, heads, a, b)
    return out
