"""Option 14: Palette. Option 13 (Colour) with the castle's resting light
in colour too — for firmware v5.71, opt-in and offline like every lab
candidate.

Measured on option 13 by rendering every frame (the page's own renderer):
the hits were varied, but a hit is gone in a fraction of a second and what
the eye holds is the base effect under it. Eight of the nine looks gave the
door a fixed red or amber effect (blood, ember, eyes, candle), so the door
was red 42-70% of every song; Blood moon's towers sat on "blood", the
dimmest red there is, and each tower's centre pixel was an ember whatever
the look. So, with every hit where option 13 put it:

1. Each look's base effects are ones the zone's palette colours (seance,
   mansion, wisp, throb) wherever a fixed red or amber stood, at levels that
   keep the hits on top. A spoken line still turns the door to eyes, and the
   candle intros and finales are untouched.
2. The door wears its own palette, one of two that stand apart from its
   towers' (blue or violet under orange towers, embers or green under blue
   and violet ones), whichever the song has worn least so far, so no song's
   door settles on one colour. Every chase and shimmer the singer puts on the
   door wears it too: the door is one colour story and the towers another.
3. The towers' centre pixels follow their effect instead of an ember, and
   Heartbeat's towers throb violet: three of the four looks the loud
   passages wear were red towers.
4. A long section turns: in its second and fourth phrase the towers wear a
   second palette (Blood moon's go violet, Graveyard's blue), so a chorus
   that holds for a minute is not a minute of one colour. A chord's mood
   still takes the palette over bar by bar, as in option 12.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

import colour_show
from ensemble_show import ensemble
from harmony import Chroma
from looks import Cue, Look
from sections_show import Draft, Planned
from sections_v2 import PALETTE
from voice_kinds import VoiceTrack
from voice_pitch import Pitch

#: Per look: the towers' effect and level, and the door's.
DRESS: dict[str, tuple[str, float, str, float]] = {
    "Graveyard": ("mansion", 0.30, "seance", 0.35),
    "Furnace": ("furnace", 0.22, "seance", 0.40),
    "Séance": ("seance", 0.35, "seance", 0.35),
    "Toxic": ("wisp", 0.30, "seance", 0.35),
    "Blood moon": ("seance", 0.40, "seance", 0.40),
    "Mansion": ("mansion", 0.35, "seance", 0.35),
    "Watchers": ("candle", 0.35, "spirit", 0.45),
    "Haunt": ("spirit", 0.30, "seance", 0.35),
    "Heartbeat": ("throb", 0.22, "seance", 0.40),
}
#: Per look, two door palettes that both stand apart from its towers' own
#: palette (NEAR counts as the same).
DOOR: dict[str, tuple[str, str]] = {
    "Graveyard": ("ember", "toxic"), "Furnace": ("moonlight", "haunt"),
    "Séance": ("ember", "toxic"), "Toxic": ("haunt", "moonlight"),
    "Blood moon": ("moonlight", "haunt"), "Mansion": ("ember", "toxic"),
    "Watchers": ("moonlight", "haunt"), "Haunt": ("ember", "toxic"),
    "Heartbeat": ("ember", "toxic"),
}  # fmt: skip
#: Palettes the LEDs barely tell apart: violet (haunt) and blue (moonlight).
NEAR = {"haunt": "moonlight", "moonlight": "haunt"}
#: Three of the four hot looks were red towers; Heartbeat's throb goes violet.
TOWER_PALETTE = {"Heartbeat": "haunt"}
#: The palette a look's towers turn to in the second and fourth phrase of a
#: section (a look with a fixed-colour tower effect has none).
TOWER_ALT = {
    "Graveyard": "moonlight", "Séance": "haunt", "Mansion": "ember",
    "Blood moon": "haunt", "Heartbeat": "ember", "Toxic": "haunt",
}  # fmt: skip
Door = list[tuple[int, str]]  # (ms, palette) from each section's start


def dressed(look: Look) -> Look:
    towers, tl, door, dl = DRESS.get(
        look.name, (look.towers, look.tower_level, look.door, look.door_level)
    )
    return replace(look, towers=towers, tower_level=tl, door=door, door_level=dl)


def restyle(plans: list[Planned]) -> list[Planned]:
    """Option 13's colours, then idea 1's resting effects."""
    return [replace(p, look=dressed(p.look)) for p in colour_show.restyle(plans)]


def doors(d: Draft) -> Door:
    """Idea 2: each section's door palette — whichever of its look's two
    the song has worn least so far, so no song's door settles on one."""
    worn: dict[str, int] = {}
    out: Door = []
    first: dict[int, Planned] = {}
    for p in d.plans:
        first.setdefault(id(p.section), p)
    for p in first.values():
        sec = p.section
        a, b = DOOR.get(p.look.name, ("moonlight", "haunt"))
        pick = b if worn.get(b, 0) < worn.get(a, 0) else a
        worn[pick] = worn.get(pick, 0) + sec.end - sec.start
        out.append((sec.start, pick))
    return sorted(out)


def door_at(plan: Door, t: int) -> str:
    i = bisect_right([at for at, _p in plan], t) - 1
    return plan[max(i, 0)][1] if plan else "moonlight"


def palettes(cues: Sequence[Cue], d: Draft, plan: Door) -> list[Cue]:
    """Every palette the door is given is its section's, and each section
    starts by giving it; the towers' own palette is TOWER_PALETTE's where it
    has one (a mood's palette stays the mood's)."""
    out: list[Cue] = []
    for c in cues:
        if c["op"] != "look" or "palette" not in c:
            out.append(c)
            continue
        look = d.look_at(c["t"])
        towers = [z for z in c["targets"] if z != "door"]
        if towers:
            own = c["palette"] == PALETTE.get(look.name)
            pal = tower_at(d, c["t"]) if own else c["palette"]
            out.append({**c, "targets": towers, "palette": pal})
        if "door" in c["targets"]:
            out.append({**c, "targets": ["door"], "palette": door_at(plan, c["t"])})
    for t, pal in plan:
        out.append({"t": t, "bus": "LED", "op": "look", "targets": ["door"],
                    "palette": pal})  # fmt: skip
    return sorted([*out, *turns(d)], key=lambda c: c["t"])


def _tower(d: Draft, i: int) -> str:
    """Plan `i`'s tower palette: the look's own, its TOWER_ALT on alternate
    phrases."""
    name = d.plans[i].look.name
    alt = TOWER_ALT.get(name) if colour_show._nth(d, i) % 2 else None
    return alt or TOWER_PALETTE.get(name, PALETTE.get(name, "haunt"))


def tower_at(d: Draft, t: int) -> str:
    starts = [p.phrase.start for p in d.plans]
    return _tower(d, max(bisect_right(starts, t) - 1, 0))


def turns(d: Draft) -> list[Cue]:
    """Idea 4: a long section's towers turn palette phrase by phrase."""
    out: list[Cue] = []
    showing = None
    for i, p in enumerate(d.plans):
        want = _tower(d, i)
        if want != showing:
            out.append({"t": p.phrase.start, "bus": "LED", "op": "look",
                        "targets": ["towerL", "towerR"], "palette": want})  # fmt: skip
            showing = want
    return out


def zones_for(zones: Mapping[str, Mapping[str, Any]], plan: Door) -> dict[str, Any]:
    """Idea 3, and the door's first palette."""
    out = {z: dict(v) for z, v in zones.items()}
    for tower in ("towerL", "towerR"):
        out.setdefault(tower, {}).pop("center", None)
    if plan:
        out.setdefault("door", {})["palette"] = plan[0][1]
    return out


def choreograph_palette(
    source: Mapping[str, Any], layers: Mapping[str, Any], pitch: Pitch | None = None,
    voice: VoiceTrack | None = None, chroma: Chroma | None = None,
    lead_ms: int = 0,
) -> dict[str, Any]:  # fmt: skip
    """Option 14, in the prepared-preview shape (pulse_clarity.encode_preview)."""
    p = ensemble(source, layers, pitch, voice, chroma, restyle=restyle)
    plan = doors(p.d)
    cues = palettes(colour_show.dress(p, pitch), p.d, plan)
    return colour_show.card(source, p, cues, zones_for(p.zones, plan), lead_ms)
