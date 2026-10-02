"""Option 15: Spectrum. Option 14 (Palette) with its colours drawn from the
whole wheel instead of nine looks' ten named colours — for firmware v5.71,
opt-in and offline like every lab candidate.

A strike carries its own 8-bit RGBW colour, so the card could always say
any of 16 million; the looks only ever asked for about ten. So, on the same
sections, drums, harmony, voices, races and resting effects:

1. Every kind of passage gets its own place on the wheel: the song's own
   starting hue (from its id) plus the golden angle (137.5°) per kind, so
   no two neighbouring kinds sit near each other and every song starts
   somewhere different. A chorus is the same colours every time it comes
   back, a little turned (SHIFT) on each return.
2. A quiet passage is themed in neighbouring hues (its colour and one
   ANALOGOUS degrees round); a loud one in opposing ones (its colour and the
   far side of the wheel, split a little), with the singer on a third hue,
   paler, so the voice stands out from the band.
3. Every coloured hit is turned a few degrees (JITTER) by its own moment,
   so a section is a spread of shades of its theme, not two flat colours.
4. The resting glow can only be one of the firmware's four palettes, so
   each section's towers wear the palette nearest its first colour and the
   door the one nearest its second.
"""

from __future__ import annotations

import colorsys
import zlib
from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

import colour_show
import palette_show
from ensemble_show import Parts, ensemble
from harmony import Chroma
from looks import HOT_FROM, Colour, Cue
from sections_show import Draft, Planned
from sections_v2 import PALETTE
from voice_kinds import VoiceTrack
from voice_pitch import Pitch

GOLDEN = 137.5  # degrees between one kind of passage and the next
SHIFT = 14.0  # degrees a returning section turns, per return
ANALOGOUS = 40.0
SPLIT = 25.0  # a loud passage's answer: opposite, split this far
JITTER = 9.0  # the most a single hit turns from its theme
#: Where the firmware's four palettes sit on the wheel (their first colour).
PALETTES = (("ember", 10.0), ("toxic", 95.0), ("moonlight", 220.0), ("haunt", 280.0))


def rgb(hue: float, sat: float = 1.0, white: float = 0.0) -> Colour:
    r, g, b = colorsys.hsv_to_rgb((hue % 360) / 360, sat, 1.0)
    return [round(r, 3), round(g, 3), round(b, 3), white]


def hue_of(c: Colour) -> float:
    return colorsys.rgb_to_hsv(c[0], c[1], c[2])[0] * 360


def theme(hue: float, loud: bool) -> tuple[Colour, Colour, Colour]:
    """A passage's first colour, answer and singer's colour."""
    if loud:
        return rgb(hue), rgb(hue + 180 - SPLIT), rgb(hue + 120, 0.55)
    return rgb(hue), rgb(hue + ANALOGOUS, 0.9), rgb(hue - 60, 0.5)


def restyler(seed: int) -> Callable[[list[Planned]], list[Planned]]:
    """Ideas 1 and 2 for one song, over option 14's resting effects."""
    start = seed % 360

    def restyle(plans: list[Planned]) -> list[Planned]:
        out = []
        for p in plans:
            sec = p.section
            hue = start + GOLDEN * sec.label + SHIFT * sec.visit
            a, b, voice = theme(hue, sec.energy >= HOT_FROM)
            look = palette_show.dressed(p.look)
            out.append(replace(p, look=replace(look, a=a, b=b, voice=voice)))
        return out

    return restyle


def nearest(hue: float, not_: str = "") -> str:
    def gap(at: float) -> float:
        d = abs(hue - at) % 360
        return min(d, 360 - d)

    return min((gap(at), name) for name, at in PALETTES if name != not_)[1]


def glow(d: Draft) -> tuple[list[Cue], list[tuple[int, str, str]]]:
    """Idea 4: each section's tower and door palettes, and when they start."""
    plan: list[tuple[int, str, str]] = []
    for p in d.plans:
        if plan and plan[-1][0] == p.section.start:
            continue
        towers = nearest(hue_of(p.look.a))
        plan.append((p.section.start, towers, nearest(hue_of(p.look.b), towers)))
    cues: list[Cue] = []
    for t, towers, door in plan:
        cues.append({"t": t, "bus": "LED", "op": "look",
                     "targets": ["towerL", "towerR"], "palette": towers})  # fmt: skip
        cues.append({"t": t, "bus": "LED", "op": "look", "targets": ["door"],
                     "palette": door})  # fmt: skip
    return cues, plan


def _mood(c: Cue, d: Draft) -> bool:
    """A chord's mood (ensemble_show.mood_looks): a palette-only record for
    both towers that is not the look's own."""
    return (
        set(c) == {"t", "bus", "op", "targets", "palette"}
        and c["targets"] == ["towerL", "towerR"]
        and c["palette"] != PALETTE.get(d.look_at(c["t"]).name)
    )


def repaint(
    cues: Sequence[Cue], d: Draft, plan: Sequence[tuple[int, str, str]]
) -> list[Cue]:
    """Every palette a look record gives follows its section's; a chord's
    mood keeps its own."""
    starts = [t for t, _a, _b in plan]
    out: list[Cue] = []
    for c in cues:
        if c["op"] != "look" or "palette" not in c or not plan:
            out.append(c)
            continue
        i = max(bisect_right(starts, c["t"]) - 1, 0)
        _t, towers, door = plan[i]
        mood = _mood(c, d)
        rest = [z for z in c["targets"] if z != "door"]
        if rest:
            out.append(
                {**c, "targets": rest, "palette": c["palette"] if mood else towers}
            )
        if "door" in c["targets"]:
            out.append({**c, "targets": ["door"], "palette": door})
    return out


def jitter(cues: Sequence[Cue]) -> list[Cue]:
    """Idea 3: each coloured hit turned a few degrees by its moment."""
    out = []
    for c in cues:
        col = c.get("color")
        if c["op"] != "strike" or col is None or col[3] > 0.5:
            out.append(c)
            continue
        h, s, v = colorsys.rgb_to_hsv(col[0], col[1], col[2])
        turn = (
            zlib.crc32(f"{c['t']}:{c['targets']}".encode()) % 2001 / 1000 - 1
        ) * JITTER
        r, g, b = colorsys.hsv_to_rgb((h + turn / 360) % 1.0, s, v)
        out.append({**c, "color": [round(r, 3), round(g, 3), round(b, 3), col[3]]})
    return out


def spectrum(
    source: Mapping[str, Any], layers: Mapping[str, Any], pitch: Pitch | None,
    voice: VoiceTrack | None, chroma: Chroma | None,
) -> tuple[Parts, list[Cue]]:  # fmt: skip
    """Ideas 1-3: the show with its themes and shades, palettes still as the
    ensemble named them."""
    seed = zlib.crc32(str(source.get("id", "")).encode())
    p = ensemble(source, layers, pitch, voice, chroma, restyle=restyler(seed))
    cues = colour_show.budget_white(p.shown, p.d, p.end.at)
    glows, held = colour_show.handoff(cues, p.d)
    cues = [c for c in cues if not colour_show._hat_in_glow(c, held)]
    return p, jitter(colour_show.shade([*cues, *glows], p.d, pitch))


def choreograph_spectrum(
    source: Mapping[str, Any], layers: Mapping[str, Any], pitch: Pitch | None = None,
    voice: VoiceTrack | None = None, chroma: Chroma | None = None,
    lead_ms: int = 0,
) -> dict[str, Any]:  # fmt: skip
    """Option 15, in the prepared-preview shape (pulse_clarity.encode_preview)."""
    p, cues = spectrum(source, layers, pitch, voice, chroma)
    records, plan = glow(p.d)
    cues = [*repaint(cues, p.d, plan), *records]
    zones = palette_show.zones_for(p.zones, [(t, door) for t, _tw, door in plan])
    if plan:
        for tower in ("towerL", "towerR"):
            zones.setdefault(tower, {})["palette"] = plan[0][1]
    return colour_show.card(
        source, p, sorted(cues, key=lambda c: c["t"]), zones, lead_ms
    )
