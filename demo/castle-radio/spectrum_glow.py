"""Option 16: Spectrum with the resting glow in any colour — a PREVIEW of
what option 15 would look like if the castle's glow were not limited to the
firmware's four palettes. No card can carry it: the lab draws it, nothing
else does (web/src/effects.ts previewPalette).

Every palette option 15 hands a zone becomes the exact colours of the
section's theme instead of the nearest of the four:

- the towers glow in the section's first colour, drifting to its answer;
- the door glows in the answer, drifting to the first colour;
- a chord's mood leans the towers' glow toward deep blue (shadow) or sickly
  green (strange), as its palette did;
- the race hands each tower the NEXT section's glow as it passes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import colour_show
import palette_show
import porch
import spectrum_show
from ensemble_show import SHADOW, STRANGE, blend
from harmony import Chroma
from looks import Colour, Cue, Look
from sections_show import Draft
from sections_v2 import PALETTE
from voice_kinds import VoiceTrack
from voice_pitch import Pitch

Poles = list[list[float]]
TOWERS = ["towerL", "towerR"]
MOOD = {"moonlight": SHADOW, "toxic": STRANGE}


def poles(first: Colour, second: Colour) -> Poles:
    return [[round(v, 3) for v in first[:3]], [round(v, 3) for v in second[:3]]]


def towers_of(look: Look) -> Poles:
    return poles(look.a, look.b)


def door_of(look: Look) -> Poles:
    return poles(look.b, look.a)


def _glow(t: int, targets: list[str], glow: Poles) -> Cue:
    return {"t": t, "bus": "LED", "op": "look", "targets": targets, "glow": glow}


def _next_look(d: Draft, t: int) -> Look:
    """The look of the first section that starts after `t`."""
    later = [p for p in d.plans if p.section.start > t]
    return later[0].look if later else d.look_at(t)


def any_colour(cues: Sequence[Cue], d: Draft) -> tuple[list[Cue], list[Cue]]:
    """The card's cues with every palette taken out, and the glow records
    that stand in for them."""
    card: list[Cue] = []
    glow: list[Cue] = []
    for c in cues:
        if c["op"] != "look" or "palette" not in c:
            card.append(c)
            continue
        rest = {k: v for k, v in c.items() if k != "palette"}
        if set(rest) - {"t", "bus", "op", "targets"}:
            card.append(rest)
        t, look = c["t"], d.look_at(c["t"])
        towers = [z for z in c["targets"] if z != "door"]
        only = set(c) == {"t", "bus", "op", "targets", "palette"}
        if towers and only and len(towers) == 1:  # the race passing a tower
            glow.append(_glow(t, towers, towers_of(_next_look(d, t))))
        elif towers and only and c["palette"] != PALETTE.get(look.name) \
                and c["palette"] in MOOD:  # fmt: skip
            tint = MOOD[c["palette"]]
            glow.append(_glow(t, towers, poles(blend(look.a, tint, 0.5),
                                               blend(look.b, tint, 0.5))))  # fmt: skip
        elif towers:
            glow.append(_glow(t, towers, towers_of(look)))
        if "door" in c["targets"]:
            glow.append(_glow(t, ["door"], door_of(look)))
    for p in d.plans:
        if p.phrase.start == p.section.start:
            glow.append(_glow(p.section.start, TOWERS, towers_of(p.look)))
            glow.append(_glow(p.section.start, ["door"], door_of(p.look)))
    return card, sorted(glow, key=lambda g: g["t"])


def choreograph_spectrum_glow(
    source: Mapping[str, Any], layers: Mapping[str, Any], pitch: Pitch | None = None,
    voice: VoiceTrack | None = None, chroma: Chroma | None = None,
    lead_ms: int = 0,
) -> dict[str, Any]:  # fmt: skip
    """Option 16: option 15's card with its glow as `preview` records the
    lab draws on top (show_lab._write adds them after the card is decoded)."""
    p, cues = spectrum_show.spectrum(source, layers, pitch, voice, chroma)
    card, glow = any_colour(cues, p.d)
    if p.d.plans:
        first = p.d.plans[0].look
        glow = [_glow(0, TOWERS, towers_of(first)), _glow(0, ["door"], door_of(first)),
                *glow]  # fmt: skip
    show = colour_show.card(source, p, sorted(card, key=lambda c: c["t"]),
                            palette_show.zones_for(p.zones, []), lead_ms)  # fmt: skip
    return {**show, "preview": porch.lead(glow, lead_ms)}
