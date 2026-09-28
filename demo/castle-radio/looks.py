"""The castle's looks: colours, base effects and levels a passage wears, and
the strike and look-change cue shapes every choreography candidate emits.

A look is a base effect for the towers and one for the door, their levels
(kept low so the hits have somewhere to go), and three colours: the first
and the answering colour of a pattern, and the singer's colour on the door.
The first six are the lab's originals (options 5-8 cycle through them); the
three after them wake the effects nothing else used — a candle-lit window
with watching eyes in the door, drifting spirits, a throbbing heart.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

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


def level_cue(t: int, zone: str, effect: str, level: float) -> Cue:
    """One zone's base effect and level from `t` on."""
    return {
        "t": t,
        "bus": "LED",
        "op": "set",
        "zone": zone,
        "eff": effect,
        "level": level,
    }


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


EXTRA_LOOKS = (
    Look("Watchers", "candle", "eyes", 0.35, 0.55, AMBER, ORANGE, RED),
    Look("Haunt", "spirit", "eyes", 0.30, 0.45, GREEN, ICE, GREEN),
    Look("Heartbeat", "throb", "blood", 0.22, 0.8, MAGENTA, RED, AMBER),
)
BY_NAME = {look.name: look for look in LOOKS + EXTRA_LOOKS}
#: Quiet and middle passages wear these; the loudest kind of passage gets HOT.
CALM = ("Graveyard", "Séance", "Watchers", "Mansion", "Haunt")
HOT = ("Furnace", "Blood moon", "Toxic", "Heartbeat")
#: A kind of passage at least this loud (against the loudest phrase) is hot.
HOT_FROM = 0.62


def _turn(names: Sequence[str], by: int) -> list[Look]:
    return [BY_NAME[names[(i + by) % len(names)]] for i in range(len(names))]


def casting(
    energy: Mapping[int, float], share: Mapping[int, float], seed: int
) -> dict[int, tuple[Look, Look]]:
    """A (look, alternate) pair per kind of passage.

    A kind of passage is hot or calm by its own loudness against the loudest
    phrase of the song (`energy`, 0..1), and each kind gets its own look from
    its family, so a chorus is recognisable every time it comes back. The
    alternate is worn when one kind runs on longer than a section may; the
    kind that is most of the song (`share` of its phrases, 0..1) alternates
    between the two families, so a song that is one groove from end to end
    still breathes between calm and hot. `seed` turns the tables so different
    songs are dressed differently."""
    calm, hot = _turn(CALM, seed), _turn(HOT, seed)
    used = {"calm": 0, "hot": 0}
    out: dict[int, tuple[Look, Look]] = {}
    for label in sorted(energy, key=lambda k: (-energy[k], k)):
        family = "hot" if energy[label] >= HOT_FROM else "calm"
        own, other = (hot, calm) if family == "hot" else (calm, hot)
        first = own[used[family] % len(own)]
        used[family] += 1
        if share.get(label, 0.0) >= 0.5:
            out[label] = (first, other[0])
        else:
            out[label] = (first, own[used[family] % len(own)])
    return out
