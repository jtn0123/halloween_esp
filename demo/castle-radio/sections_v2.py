"""Option 10: option 9's plan, written for cue format v2 (firmware v5.71).
Opt-in and offline like every lab candidate; today's castle (v5.70) refuses
a v2 file and plays the song dark, so this is a look at the new firmware,
not something the porch can show yet.

The same sections, looks, drops, stops and sung notes as option 9
(sections_show.draft); what v2 changes is how they land:

- The singer is on the ORNAMENT layer (layer 1), which the firmware adds to
  the band's flash instead of replacing it: a sung note no longer cuts a
  band hit short, nor a band hit a held note, so every sung note is kept.
- Each section sends a LOOK record to the towers: a chase locked to the
  tempo — one turn a bar, its head on the downbeat — in the loud looks, a
  slow meteor that drips every two bars in the quiet ones, and two turns a
  bar for the final chorus. A stop stills the overlay; the band's return
  restarts it on the beat.
- The door's halves take part: a hand-over runs left-right across the
  whole castle, and a build's swell rises up the door, bottom then top.
- No soften compensation. v5.71 softens only a zone struck again within
  333 ms — a real strobe — so a beat-rate hit renders as designed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import porch
from looks import HOT, ZONES, Cue, Look
from sections_show import Draft, Planned, draft, preview, sung_cue

#: Which overlay palette a look's chase or meteor wears.
PALETTE = {
    "Graveyard": "haunt", "Haunt": "haunt", "Séance": "moonlight",
    "Mansion": "moonlight", "Watchers": "ember", "Furnace": "ember",
    "Blood moon": "ember", "Heartbeat": "ember", "Toxic": "toxic",
}  # fmt: skip
FIRMWARE = "v5.71"


def motion(t: int, look: Look, bpm: float, turns_per_bar: float) -> Cue:
    """The towers' overlay for a section, its head at the bar line `t`."""
    hot = look.name in HOT
    return {
        "t": t, "bus": "LED", "op": "look", "targets": list(ZONES[:2]),
        "overlay": "chase" if hot else "meteor",
        "palette": PALETTE.get(look.name, "haunt"),
        "rate": round(bpm / 240 * (turns_per_bar if hot else 0.5), 3),
        "head": 0.0,
    }  # fmt: skip


def _turns(plan: Planned, plans: list[Planned]) -> float:
    """One turn a bar; two for the last visit of the loudest kind of passage
    — the section sections_show.cast steps its pattern up for."""
    energy: dict[int, list[float]] = {}
    for p in {id(p.section): p.section for p in plans}.values():
        energy.setdefault(p.label, []).append(p.energy)
    mean = {label: sum(v) / len(v) for label, v in energy.items()}
    loudest = max(mean, key=lambda label: (mean[label], -label))
    sec = plan.section
    return 2.0 if sec.label == loudest and sec.last_visit and sec.visit > 0 else 1.0


def motion_cues(d: Draft) -> list[Cue]:
    cues: list[Cue] = []
    for i, plan in enumerate(d.plans):
        if i == 0 or d.plans[i - 1].section is not plan.section:
            start = plan.section.start
            cues.append(motion(start, plan.look, d.grid.bpm, _turns(plan, d.plans)))
    for start, end in d.stops:
        cues = [c for c in cues if not start <= c["t"] < end]
        back = next((p for p in d.plans if p.phrase.start <= end < p.phrase.end), None)
        cues.append({"t": start, "bus": "LED", "op": "look",
                     "targets": list(ZONES[:2]), "overlay": "none"})  # fmt: skip
        if back is not None:
            cues.append(motion(end, back.look, d.grid.bpm, _turns(back, d.plans)))
    return cues


def sung(d: Draft, duration: int) -> list[Cue]:
    """Every sung note on the ornament layer, except inside a drop's dark."""
    out = []
    for at, strength, room in d.notes:
        if at >= duration - 50 or any(a <= at < b for a, b in d.drops):
            continue
        out.append({**sung_cue(at, strength, room, d.look_at(at).voice), "layer": 1})
    return out


def choreograph_sections_v2(
    source: Mapping[str, Any], layers: Mapping[str, Any], lead_ms: int = 0
) -> dict[str, Any]:
    """Option 10, in the prepared-preview shape (pulse_clarity.encode_preview)."""
    d = draft(source, layers, halves=True)
    duration = int(source["dur"])
    band = [c for c in d.cues if c["t"] < duration - 50]
    shown = porch.lead([*band, *motion_cues(d), *sung(d, duration)], lead_ms)
    zones = {z: dict(source.get("zones", {}).get(z, {})) for z in ZONES}
    return {**preview(source, d, shown, zones), "firmware": FIRMWARE}
