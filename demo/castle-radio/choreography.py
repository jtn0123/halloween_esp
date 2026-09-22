"""Opt-in, offline choreography experiments. Never replaces a prepared show.

The prepared show strikes on every onset over one unchanging look. These
candidates spend the same card format on STRUCTURE instead: a look (base
effects, levels, colours) that changes every phrase, a pattern locked to the
beat, a roll and a blackout before the song lifts, and the detected onsets
kept only as ornament — placed where they will not cut a beat hit short,
because the card reader REPLACES a zone's flash rather than adding to it.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from beat_grid import Grid, Phrase, analyse

ZONES = ("towerL", "towerR", "door")
ACROSS = ("towerL", "door", "towerR")  # as the castle stands, left to right
TICK_MS = 16
Cue = dict[str, Any]
Colour = list[float]

AMBER: Colour = [1.0, 0.35, 0.02, 0.0]
ORANGE: Colour = [1.0, 0.16, 0.01, 0.0]
RED: Colour = [1.0, 0.02, 0.02, 0.0]
VIOLET: Colour = [0.55, 0.04, 1.0, 0.0]
MAGENTA: Colour = [1.0, 0.05, 0.6, 0.0]
GREEN: Colour = [0.05, 1.0, 0.2, 0.0]
TOXIC: Colour = [0.6, 1.0, 0.05, 0.0]
ICE: Colour = [0.2, 0.55, 1.0, 0.15]
WHITE: Colour = [0.7, 0.7, 0.8, 1.0]


@dataclass(frozen=True)
class Look:
    name: str
    towers: str  # base effect
    door: str
    tower_level: float
    door_level: float
    a: Colour  # left / first colour
    b: Colour  # right / answer colour
    voice: Colour  # the door when it follows the singer


LOOKS = (
    Look("Graveyard", "chill", "ember", 0.30, 0.30, VIOLET, GREEN, AMBER),
    Look("Furnace", "furnace", "blood", 0.22, 1.0, ORANGE, RED, AMBER),
    Look("Séance", "seance", "spirit", 0.35, 0.30, MAGENTA, ICE, GREEN),
    Look("Toxic", "wisp", "ember", 0.30, 0.25, TOXIC, VIOLET, TOXIC),
    Look("Blood moon", "blood", "eyes", 1.0, 0.35, RED, WHITE, RED),
    Look("Mansion", "mansion", "candle", 0.35, 0.35, AMBER, ICE, MAGENTA),
)


def decay_for(ms: float, floor: float = 0.12) -> float:
    """The per-tick decay that brings a flash down to `floor` in `ms`."""
    ticks = max(1.0, ms / TICK_MS)
    return max(0.6, min(0.985, math.pow(floor, 1 / ticks)))


def strike(
    t: float,
    targets: Sequence[str],
    colour: Colour,
    intensity: float,
    fade_ms: float,
    pixels: str = "all",
    attack: int = 0,
) -> Cue:
    return {
        "t": round(t), "bus": "LED", "op": "strike", "targets": list(targets),
        "intensity": round(intensity, 3), "decay": round(decay_for(fade_ms), 4),
        "attack": attack, "pixels": pixels, "color": colour, "ms": 120,
    }  # fmt: skip


def look_cues(t: int, look: Look, dim: float = 1.0) -> list[Cue]:
    return [
        {
            "t": t,
            "bus": "LED",
            "op": "set",
            "zone": z,
            "eff": eff,
            "level": round(lvl * dim, 2),
        }
        for z, eff, lvl in (
            ("towerL", look.towers, look.tower_level),
            ("towerR", look.towers, look.tower_level),
            ("door", look.door, look.door_level),
        )
    ]


def _beats(phrase: Phrase) -> list[tuple[int, int, int, int]]:
    """(time, period, beat in bar 0..3, bar in phrase) for every beat."""
    out = []
    times = phrase.beats
    for j, t in enumerate(times):
        period = (times[j + 1] - t) if j + 1 < len(times) else (phrase.end - t)
        period = max(250, min(900, period))
        k = j - phrase.pickup
        out.append((t, period, k % 4, k // 4))
    return out


def pingpong(phrase: Phrase, look: Look) -> list[Cue]:
    """Left, right, left, right on the beat; the whole castle on every 'one'."""
    cues = []
    for t, period, beat, bar in _beats(phrase):
        if beat == 0:
            cues.append(
                strike(t, ZONES, look.a if bar % 2 == 0 else look.b, 1.0, period)
            )
        else:
            zone, colour = (("towerR", look.b), ("towerL", look.a))[beat % 2]
            cues.append(strike(t, [zone], colour, 0.85, period * 0.8))
    return cues


def chase(phrase: Phrase, look: Look) -> list[Cue]:
    """A sweep across the castle inside every beat; it turns round each bar."""
    cues = []
    for t, period, beat, bar in _beats(phrase):
        order = ACROSS if bar % 2 == 0 else ACROSS[::-1]
        colour = look.a if (bar + beat) % 2 == 0 else look.b
        for step, zone in enumerate(order):
            pixels = "ring" if zone == "door" else "all"
            cues.append(
                strike(
                    t + step * period / 3, [zone], colour, 0.9, period * 0.45, pixels
                )
            )
    return cues


def stomp(phrase: Phrase, look: Look) -> list[Cue]:
    """Everything on one and three; the towers answer on two and four."""
    cues = []
    for t, period, beat, _bar in _beats(phrase):
        if beat % 2 == 0:
            cues.append(strike(t, ZONES, look.a, 1.0, period * 0.9))
        else:
            cues.append(strike(t, ZONES[:2], look.b, 0.8, period * 0.6, "center"))
            cues.append(
                strike(t + period / 2, ["door"], look.b, 0.6, period * 0.4, "ring")
            )
    return cues


def breathe(phrase: Phrase, look: Look) -> list[Cue]:
    """One slow swell a bar, passed from tower to tower."""
    cues = []
    for t, period, beat, bar in _beats(phrase):
        if beat == 0:
            zone, colour = (("towerL", look.a), ("towerR", look.b))[bar % 2]
            cues.append(strike(t, [zone], colour, 0.75, period * 3, "all", int(period)))
    return cues


def heartbeat(phrase: Phrase, look: Look) -> list[Cue]:
    """Lub-dub on one and three, both towers together."""
    cues = []
    for t, period, beat, _bar in _beats(phrase):
        if beat % 2 == 0:
            cues.append(strike(t, ZONES[:2], look.a, 0.95, period * 0.35, "center"))
            cues.append(
                strike(t + period * 0.42, ZONES[:2], look.a, 0.6, period, "all")
            )
    return cues


Pattern = Callable[[Phrase, Look], list[Cue]]
POOLS: tuple[tuple[Pattern, ...], ...] = (
    (breathe, heartbeat, pingpong),
    (pingpong, chase, heartbeat),
    (stomp, chase, pingpong),
)


def drop(phrase: Phrase, following: Look) -> list[Cue]:
    """The last two beats before a lift: a sixteenth-note roll that climbs,
    half a beat of darkness, then the next phrase opens on a white slam."""
    beats = _beats(phrase)[-2:]
    if len(beats) < 2:
        return []
    start, period = beats[0][0], beats[0][1]
    step = period / 4
    cues = [
        strike(start + i * step, [ZONES[i % 2]], WHITE, 0.35 + 0.1 * i, step * 0.8)
        for i in range(6)
    ]
    dark = round(start + 6 * step)
    cues += look_cues(dark, following, 0.0)
    cues.append(strike(phrase.end, ZONES, WHITE, 1.0, period * 2.5))
    return cues


class Placer:
    """Keeps a hit from being cut short: a later, weaker strike on the same
    zone is refused while the earlier flash is still the brighter of the two."""

    def __init__(self) -> None:
        self.cues: list[Cue] = []
        self.live: dict[str, list[tuple[int, int, float, float]]] = {
            z: [] for z in ZONES
        }
        self.reserved: list[tuple[int, int]] = []  # a roll and its darkness

    def fixed(self, cues: Sequence[Cue]) -> None:
        for cue in cues:
            self.cues.append(cue)
            if cue["op"] == "strike":
                for zone in cue["targets"]:
                    self.live[zone].append(
                        (cue["t"], cue["attack"], cue["intensity"], cue["decay"])
                    )

    def settle(self) -> None:
        for hits in self.live.values():
            hits.sort()

    def level(self, zone: str, t: int) -> float:
        before = [h for h in self.live[zone] if h[0] <= t]
        if not before:
            return 0.0
        at, attack, intensity, decay = before[-1]
        return float(intensity * decay ** (max(0, t - at - attack) / TICK_MS))

    def next_fixed(self, zone: str, t: int) -> int:
        return min((h[0] for h in self.live[zone] if h[0] > t), default=1 << 30)

    def ornament(self, cue: Cue, clear_ms: int = 110) -> bool:
        zone, t = cue["targets"][0], cue["t"]
        if any(start <= t < end for start, end in self.reserved):
            return False
        if self.level(zone, t) > cue["intensity"] * 0.7:
            return False
        if self.next_fixed(zone, t) - t < clear_ms:
            return False
        self.cues.append(cue)
        return True


def finale(phrase: Phrase, look: Look, duration: int) -> list[Cue]:
    """The last bar holds one long white hit while the castle sinks to embers."""
    last = phrase.beats[-1]
    fade = max(1500, duration - last)
    return [strike(last, ZONES, WHITE, 1.0, fade), *look_cues(last + 1, look, 0.2)]


def spaced(hits: Sequence[Sequence[float]], gap_ms: int) -> list[tuple[int, float]]:
    """Strongest first, nothing closer than `gap_ms` to a hit already kept."""
    kept: list[tuple[int, float]] = []
    for at, strength in sorted(
        ((round(h[0] * 1000), h[1]) for h in hits), key=lambda h: -h[1]
    ):
        if all(abs(at - other) >= gap_ms for other, _ in kept):
            kept.append((at, strength))
    return sorted(kept)


def _merged(onsets: Mapping[str, Sequence[Sequence[float]]]) -> list[Sequence[float]]:
    return [h for hits in onsets.values() for h in hits]


@dataclass(frozen=True)
class Style:
    name: str
    patterns: tuple[tuple[Pattern, ...], ...]
    drops: bool
    ornaments: bool
    tower_overlay: str = "none"
    solo_door: bool = False  # the door is the singer's while there is singing


STYLES = {
    "beat": Style("Beat lock", ((pingpong,),) * 3, False, False),
    "motion": Style(
        "Sweeps", ((breathe, chase), (chase,), (chase, stomp)), False, True, "chase"
    ),
    "show": Style("Full show", POOLS, True, True, "chase"),
    "duet": Style("Singer's door", POOLS, True, True, "chase", True),
}


def singing(voice: Sequence[tuple[int, float]], t: int) -> bool:
    """Is there a sung onset from half a second before `t` to a second after?"""
    at = bisect_left(voice, (t - 500, 0.0))
    return at < len(voice) and voice[at][0] <= t + 1000


def without_door(cues: Sequence[Cue], voice: Sequence[tuple[int, float]]) -> list[Cue]:
    """The band's pattern, kept off the door wherever the singer needs it. In
    an instrumental passage the door is nobody's, so it rejoins the band."""
    out = []
    for cue in cues:
        keep = cue["targets"]
        if "door" in keep and singing(voice, cue["t"]):
            keep = [z for z in keep if z != "door"]
        if keep:
            out.append({**cue, "targets": keep})
    return out


def plan(grid: Grid, style: Style) -> list[tuple[Phrase, Look, Pattern]]:
    """A look and a pattern per phrase. Neither repeats back to back, and a
    rank's pool is walked in order so the same kind of passage comes back
    looking like itself two visits later, not at random."""
    out: list[tuple[Phrase, Look, Pattern]] = []
    turn = [0, 0, 0]
    for index, phrase in enumerate(grid.phrases):
        pool = style.patterns[phrase.rank]
        pattern = pool[turn[phrase.rank] % len(pool)]
        turn[phrase.rank] += 1
        if out and pattern is out[-1][2] and len(pool) > 1:
            pattern = pool[turn[phrase.rank] % len(pool)]
            turn[phrase.rank] += 1
        out.append((phrase, LOOKS[index % len(LOOKS)], pattern))
    return out


def choreograph(
    source: Mapping[str, Any], layers: Mapping[str, Any], style: Style
) -> dict[str, Any]:
    """A candidate show in the prepared-preview shape, ready for
    pulse_clarity.encode_preview. `source` supplies identity and duration."""
    duration = int(source["dur"])
    grid = analyse(layers, duration)
    planned = plan(grid, style)
    placer = Placer()
    sung = (layers.get("vocals") or {}).get("both", {}).get("onsets", {})
    voice = [h for h in spaced(_merged(sung), 180) if h[0] < duration - 100]
    sections = []
    slam = -1000
    for index, (phrase, look, pattern) in enumerate(planned):
        placer.fixed(look_cues(phrase.start, look))
        # A slam already owns this downbeat; a second strike would replace it.
        cues = [c for c in pattern(phrase, look) if abs(c["t"] - slam) > 120]
        if style.solo_door:
            cues = without_door(cues, voice)
        following = planned[index + 1] if index + 1 < len(planned) else None
        lifts = following is not None and (
            following[0].rank > phrase.rank
            or following[0].energy - phrase.energy > 0.08
        )
        if style.drops and lifts and following is not None and len(phrase.beats) >= 8:
            cut = _beats(phrase)[-2][0]
            cues = [c for c in cues if c["t"] < cut] + drop(phrase, following[1])
            slam = phrase.end
            placer.reserved.append((cut, phrase.end))
        if following is None and style.drops:
            cues = [c for c in cues if c["t"] < phrase.beats[-1]]
            cues += finale(phrase, look, duration)
        placer.fixed([c for c in cues if c["t"] < duration - 50])
        sections.append(
            {"t": phrase.start, "end": phrase.end, "look": look.name,
             "pattern": pattern.__name__, "rank": phrase.rank,
             "drop": bool(style.drops and lifts)}
        )  # fmt: skip
    placer.settle()
    looks = {id(p): look for p, look, _ in planned}

    def look_at(t: int) -> Look:
        for phrase in grid.phrases:
            if phrase.start <= t < phrase.end:
                return looks[id(phrase)]
        return LOOKS[0]

    for at, strength in voice:
        colour = look_at(at).voice
        if style.solo_door:  # nothing competes, so the whole ring can sing
            hit = strike(at, ["door"], colour, 0.6 + 0.4 * strength, 420)
        else:
            hit = strike(at, ["door"], colour, 0.45 + 0.45 * strength, 320, "scatter")
        placer.ornament(hit)
    if style.ornaments:
        for side, zone in (("left", "towerL"), ("right", "towerR")):
            fills = layers["backing"].get(side, {}).get("onsets", {})
            for at, strength in spaced(_merged(fills), 140):
                if strength >= 0.45 and at < duration - 100:
                    colour = look_at(at).b if zone == "towerL" else look_at(at).a
                    hit = strike(
                        at, [zone], colour, 0.2 + 0.3 * strength, 180, "scatter"
                    )
                    placer.ornament(hit)
    first = planned[0][1] if planned else LOOKS[0]
    zones = {z: dict(source.get("zones", {}).get(z, {})) for z in ZONES}
    for tower in ZONES[:2]:
        zones[tower]["overlay"] = style.tower_overlay
    return {
        **{k: source[k] for k in ("id", "name", "dur") if k in source},
        "base": {"towerL": first.towers, "towerR": first.towers, "door": first.door},
        "levels": {"towerL": first.tower_level, "towerR": first.tower_level,
                   "door": first.door_level},
        "zones": zones,
        "cues": sorted(placer.cues, key=lambda c: c["t"]),
        "sections": sections,
        "bpm": round(grid.bpm, 1),
        "beats": list(grid.beats),
    }  # fmt: skip
