"""Option 13: Colour. Option 12 (Ensemble) with more colour and no more
chaos — for firmware v5.71, opt-in and offline like every lab candidate.

Measured on option 12: each song sat on one colour family (Day-O 66% red
and orange by brightness, Spell 65% green, Monster Mash 49% violet and
blue), four looks answered one warm colour with another the LEDs barely
tell apart (Furnace orange/red, Heartbeat magenta/red, Watchers
amber/orange, Blood moon red/white), and white was a third of Thriller's
light. So, on the same sections, drums, harmony, voices and bookends:

1. Every look answers across the wheel: its two colours are at least
   MIN_HUE degrees apart, and each look has a third, ACCENT, colour from a
   family neither of the two is in.
2. White is kept for the big moments — a drop, a stop, the finale and the
   first hit of each chorus. Anywhere else a white hit takes the look's
   own first colour.
3. The race hands the colour over: each part of the castle it passes keeps
   a glow of the new section's colour, and its chase changes palette as
   the light goes by, so a new section wipes across the castle instead of
   cutting in.
4. A section that comes back is recognisable but not identical: its second
   and later visits lead with the answering colour, and the chorus's last
   visit answers in the accent.
5. The singer's notes are shaded by pitch: high notes lighter and cooler,
   low ones deeper, round the door's own colour.
6. No colour family holds more than FAMILY_CAP of a song's light: past it,
   the family's hits in alternate phrases take the look's accent.
"""

from __future__ import annotations

import colorsys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

import porch
from ensemble_show import Parts, _period, blend, ensemble
from harmony import Chroma
from looks import BY_NAME, WHITE, ZONES, Colour, Cue, strike
from sections_show import Draft, Planned, preview
from sections_v2 import FIRMWARE, PALETTE
from spin_show import race
from voice_kinds import VoiceTrack
from voice_pitch import Pitch

MIN_HUE = 80.0  # degrees between a look's two colours
FAMILY_CAP = 0.45  # of a song's coloured light (white aside)
TEAL: Colour = [0.0, 0.8, 0.6, 0.0]
DEEP_BLUE: Colour = [0.05, 0.1, 1.0, 0.0]
HIGH_NOTE: Colour = [0.5, 0.65, 1.0, 0.6]  # a high note leans pale and cool
LOW_NOTE: Colour = [0.35, 0.0, 0.6, 0.0]  # a low one deep violet
GLOW = 0.28  # the colour the race leaves behind
TOWERS = list(ZONES[:2])
_C = BY_NAME
#: The answering colour of the four looks that answered with a neighbour.
ANSWER = {"Furnace": DEEP_BLUE, "Blood moon": _C["Mansion"].b,
          "Watchers": _C["Graveyard"].b, "Heartbeat": TEAL}  # fmt: skip
ACCENT = {
    "Graveyard": _C["Mansion"].a, "Furnace": _C["Séance"].a,
    "Séance": _C["Graveyard"].b, "Toxic": _C["Mansion"].a,
    "Blood moon": _C["Séance"].a, "Mansion": _C["Séance"].a,
    "Watchers": _C["Graveyard"].a, "Haunt": _C["Séance"].a,
    "Heartbeat": _C["Mansion"].a,
}  # fmt: skip
COLOURS = {name: replace(look, b=ANSWER.get(name, look.b))
           for name, look in BY_NAME.items()}  # fmt: skip


def hue(c: Colour) -> float:
    return colorsys.rgb_to_hsv(c[0], c[1], c[2])[0] * 360


def family(c: Colour) -> str:
    """The colour's family on the castle: white where the white LED leads."""
    if c[3] > 0.5:
        return "white"
    h = hue(c)
    if h < 45 or h >= 345:
        return "warm"
    return "green" if h < 160 else "blue" if h < 290 else "magenta"


def chorus(plans: Sequence[Planned]) -> int:
    """The chorus's label: the loudest kind of passage that comes back (an
    instrumental's one-off loud opening is not a chorus), else the loudest."""
    energy: dict[int, list[float]] = {}
    for sec in {id(p.section): p.section for p in plans}.values():
        energy.setdefault(sec.label, []).append(sec.energy)
    back = [k for k in energy if len(energy[k]) > 1] or list(energy)
    return max(back, key=lambda k: (sum(energy[k]) / len(energy[k]), -k))


def restyle(plans: list[Planned]) -> list[Planned]:
    """Ideas 1 and 4: the retuned looks, varied on a section's return."""
    if not plans:
        return plans
    loudest = chorus(plans)
    out = []
    for p in plans:
        look = COLOURS.get(p.look.name, p.look)
        sec = p.section
        if sec.label == loudest and sec.last_visit and sec.visit > 0:
            look = replace(look, b=ACCENT.get(look.name, look.b))
        elif sec.visit > 0:
            look = replace(look, a=look.b, b=look.a)
        out.append(replace(p, look=look))
    return out


def _windows(d: Draft, end_at: int) -> list[tuple[int, int]]:
    """Where white stays: drops and stops with two beats either side, and
    the finale."""
    out = [(end_at, 1 << 30)]
    for a, b in [*d.drops, *d.stops]:
        out.append((a - 2 * _period(d, a), b + 2 * _period(d, b)))
    return out


def chorus_ones(d: Draft) -> set[int]:
    """The first downbeat of every visit of the chorus."""
    if not d.plans:
        return set()
    loudest = chorus(d.plans)
    return {p.section.start for p in d.plans if p.section.label == loudest}


def budget_white(cues: Sequence[Cue], d: Draft, end_at: int) -> list[Cue]:
    """Idea 2."""
    keep = _windows(d, end_at)
    ones = chorus_ones(d)
    out = []
    for c in cues:
        if c["op"] != "strike" or c.get("layer"):
            out.append(c)
            continue
        whole = c["pixels"] == "all" and set(TOWERS) <= set(c["targets"])
        if whole and any(abs(c["t"] - t) <= 60 for t in ones):
            out.append({**c, "color": WHITE})
        elif c["color"] == WHITE and not any(a <= c["t"] < b for a, b in keep):
            out.append({**c, "color": d.look_at(c["t"]).a})
        else:
            out.append(c)
    return out


Window = tuple[str, int, int]  # a zone and the ms its race glow holds it


def handoff(cues: Sequence[Cue], d: Draft) -> tuple[list[Cue], list[Window]]:
    """Idea 3: the colour and chase palette each zone keeps as the race
    passes it, for the race hits that made it into the show."""
    shown = {(c["t"], tuple(c["targets"]), c.get("pixels")) for c in cues
             if c["op"] == "strike"}  # fmt: skip
    out: list[Cue] = []
    held: list[Window] = []
    for p, nxt in zip(d.plans, d.plans[1:], strict=False):
        if nxt.section is p.section:
            continue
        hits, _cut = race(p, nxt.look)
        hits = [h for h in hits
                if (h["t"], tuple(h["targets"]), h["pixels"]) in shown]  # fmt: skip
        until = nxt.section.start + 2 * _period(d, nxt.section.start)
        last = {h["targets"][0]: h for h in hits}  # the door's second pass
        for zone, h in last.items():
            glow = strike(h["t"] + 1, [zone], h["color"], GLOW, until - h["t"])
            out.append({**glow, "layer": 1})
            held.append((zone, h["t"], until))
            if zone in TOWERS:
                out.append({"t": h["t"], "bus": "LED", "op": "look", "targets": [zone],
                            "palette": PALETTE.get(nxt.look.name, "haunt")})  # fmt: skip
    return out, held


def _hat_in_glow(c: Cue, held: Sequence[Window]) -> bool:
    """A hi-hat sparkle inside a race glow on its tower: it would cut the
    glow short, as a zone's flash is replaced layer by layer."""
    return (
        c["op"] == "strike" and c.get("layer") == 1 and c["pixels"] == "scatter"
        and any([z] == c["targets"] and a < c["t"] < b for z, a, b in held)
    )  # fmt: skip


def shade(cues: Sequence[Cue], d: Draft, pitch: Pitch | None) -> list[Cue]:
    """Idea 5: each sung arc lighter the higher it is sung, deeper the lower."""
    if pitch is None:
        return list(cues)
    notes = {at: pitch.at(at + 30, at + min(room, 300)) for at, _s, room in d.notes}
    known = sorted(n for n in notes.values() if n is not None)
    if len(known) < 5:
        return list(cues)
    count = len(known)
    mid = known[count // 2]
    spread = known[9 * count // 10] - known[count // 10]
    out = []
    for c in cues:
        n = notes.get(c["t"]) if c["op"] == "strike" and c.get("layer") == 1 else None
        if n is None or c["targets"] != ["door"] or not c["pixels"].startswith("arc"):
            out.append(c)
            continue
        x = max(-1.0, min(1.0, 2 * (n - mid) / max(spread, 2.0)))
        if x > 0:
            out.append({**c, "color": blend(c["color"], HIGH_NOTE, 0.35 * x)})
        else:
            out.append({**c, "color": blend(c["color"], LOW_NOTE, 0.3 * -x),
                        "intensity": round(c["intensity"] * (1 + 0.15 * x), 3)})  # fmt: skip
    return out


def shares(cues: Sequence[Cue]) -> dict[str, float]:
    """Each family's share of the show's coloured strike light."""
    light: dict[str, float] = {}
    for c in cues:
        if c["op"] == "strike" and family(c["color"]) != "white":
            f = family(c["color"])
            light[f] = light.get(f, 0.0) + c["intensity"]
    total = sum(light.values()) or 1.0
    return {f: round(v / total, 3) for f, v in sorted(light.items())}


def capped(cues: list[Cue], d: Draft) -> list[Cue]:
    """Idea 6: past FAMILY_CAP, a family's hits in alternate phrases (the
    second and fourth of a section first) take their look's accent."""
    s = shares(cues)
    top = max(s, key=lambda f: s[f]) if s else None
    if top is None or s[top] <= FAMILY_CAP:
        return cues
    order = sorted(range(len(d.plans)), key=lambda i: (_nth(d, i) % 2 == 0, i))
    out = list(cues)
    for i in order:
        p = d.plans[i]
        accent = ACCENT.get(p.look.name)
        if accent is None or family(accent) == top:
            continue
        a, b = p.phrase.start, p.phrase.end
        out = [{**c, "color": accent} if c["op"] == "strike" and a <= c["t"] < b
               and family(c["color"]) == top else c for c in out]  # fmt: skip
        if shares(out).get(top, 0.0) <= FAMILY_CAP:
            break
    return out


def _nth(d: Draft, i: int) -> int:
    """Which phrase of its section plan `i` is, from 0."""
    sec = d.plans[i].section
    return sum(1 for p in d.plans[:i] if p.section is sec)


def dress(p: Parts, pitch: Pitch | None) -> list[Cue]:
    cues = budget_white(p.shown, p.d, p.end.at)
    glows, held = handoff(cues, p.d)
    cues = [c for c in cues if not _hat_in_glow(c, held)]
    cues = shade([*cues, *glows], p.d, pitch)
    return capped(cues, p.d)


def choreograph_colour(
    source: Mapping[str, Any], layers: Mapping[str, Any], pitch: Pitch | None = None,
    voice: VoiceTrack | None = None, chroma: Chroma | None = None,
    lead_ms: int = 0,
) -> dict[str, Any]:  # fmt: skip
    """Option 13, in the prepared-preview shape (pulse_clarity.encode_preview)."""
    p = ensemble(source, layers, pitch, voice, chroma, restyle=restyle)
    return card(source, p, dress(p, pitch), p.zones, lead_ms)


def card(
    source: Mapping[str, Any], p: Parts, cues: list[Cue],
    zones: Mapping[str, Mapping[str, Any]], lead_ms: int,
) -> dict[str, Any]:  # fmt: skip
    """The finished show, its story telling how much white and which colour
    families the hits spend."""
    whites = sum(c["intensity"] for c in cues
                 if c["op"] == "strike" and family(c["color"]) == "white")  # fmt: skip
    light = sum(c["intensity"] for c in cues if c["op"] == "strike") or 1.0
    story = {**p.story, "white": round(whites / light, 3), "families": shares(cues)}
    shown = porch.lead(cues, lead_ms)
    return {**preview(source, p.d, shown, zones), "firmware": FIRMWARE,
            "story": story}  # fmt: skip
