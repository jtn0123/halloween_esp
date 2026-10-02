"""Option 9: the section-aware show. Opt-in and offline, like every lab
candidate — nothing here is wired into rich_show.prepare.

Options 5-8 put a pattern on the beat but still change look every four bars
(about every seven seconds), so the song never seems to have a shape. This
one dresses the song's STRUCTURE (structure.py):

- A look per kind of passage, held for the whole section. The same music
  coming back wears the same look, so a chorus is recognisable; a section
  that runs long switches to its kind's alternate look.
- The beat's own strength scales each hit, so accents in the band are
  accents in the light instead of a metronome.
- The tower base level follows the band bar by bar.
- Into a louder section: two bars of rising level, a colour swell (not a
  white stutter — that is a strobe), half a beat of dark, one white slam.
  Into a different one: a last-bar run that hands over to the next colours.
- Where the band stops dead, the castle goes dark and only the singer is lit;
  it slams back in the next look's colour when the band returns.
- The door is the singer's while there is singing (option 8's rule), and a
  held note glows instead of flickering. A near-silent vocal stem is bleed,
  not a singer, and is ignored.
- Written for the porch as it is: every beat-rate hit is compensated for the
  Soften switch that is on by default (porch.py).
"""

from __future__ import annotations

import zlib
from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import porch
from beat_grid import Grid, Phrase, analyse
from choreography import (
    POOLS,
    Pattern,
    Placer,
    beat_table,
    finale,
    merged,
    spaced,
    without_door,
)
from looks import WHITE, ZONES, Cue, Look, casting, level_cue, look_cues, strike
from structure import (
    Section,
    absolute,
    beat_strengths,
    breaks,
    has_singer,
    label_phrases,
    rhythm_bands,
    sections,
)

LIFT = 0.1  # a next section this much louder (0..1 energy) earns a drop
HELD_MS = 450  # a sung note with this much room before the next is held


@dataclass(frozen=True)
class Planned:
    phrase: Phrase
    section: Section
    look: Look
    pattern: Pattern


def cast(grid: Grid, secs: Sequence[Section], key: str) -> list[Planned]:
    """A look per section, a pattern per phrase. Same label → same look and,
    at the same loudness, the same pattern; the last visit of the loudest
    kind of passage (the final chorus, usually) steps its pattern up."""
    energy: dict[int, list[float]] = {}
    for s in secs:
        energy.setdefault(s.label, []).append(s.energy)
    mean = {label: sum(v) / len(v) for label, v in energy.items()}
    count = len(grid.phrases) or 1
    share = {
        label: sum(len(s.phrases) for s in secs if s.label == label) / count
        for label in mean
    }
    pairs = casting(mean, share, zlib.crc32(key.encode()) % 5)
    loudest = max(mean, key=lambda label: (mean[label], -label))
    out: list[Planned] = []
    run = 0
    for i, sec in enumerate(secs):
        run = run + 1 if i and secs[i - 1].label == sec.label else 0
        look = pairs[sec.label][run % 2]
        for index in sec.phrases:
            phrase = grid.phrases[index]
            pool = POOLS[phrase.rank]
            pattern = pool[sec.label % len(pool)]
            if sec.label == loudest and sec.last_visit and sec.visit > 0:
                pattern = POOLS[2][(sec.label + 1) % len(POOLS[2])]
            out.append(Planned(phrase, sec, look, pattern))
    return out


def with_velocity(cues: list[Cue], grid: Grid, strength: Sequence[float]) -> list[Cue]:
    """Scale each strike by how hard the beat it belongs to is hit."""
    out = []
    for cue in cues:
        if cue["op"] == "strike":
            i = bisect_right(grid.beats, cue["t"] + 30) - 1
            v = strength[i] if 0 <= i < len(strength) else 1.0
            out.append(
                {**cue, "intensity": round(cue["intensity"] * (0.6 + 0.4 * v), 3)}
            )
        else:
            out.append(cue)
    return out


def dynamics(
    plan: Planned, env: Sequence[float], mean: float, duration: int
) -> list[Cue]:
    """The tower base level follows the band, bar by bar, within ±50%."""
    phrase, look = plan.phrase, plan.look
    cues: list[Cue] = []
    last = look.tower_level
    table = beat_table(phrase)
    for t, period, beat, _bar in table:
        if beat != 0:
            continue
        a = t * len(env) // max(1, duration)
        b = max(a + 1, (t + 4 * period) * len(env) // max(1, duration))
        here = sum(env[a:b]) / max(1, len(env[a:b])) if env else mean
        level = round(look.tower_level * min(1.5, max(0.5, here / (mean or 1.0))), 2)
        if abs(level - last) >= 0.03:
            cues += [level_cue(t, z, look.towers, level) for z in ZONES[:2]]
            last = level
    return cues


def build_and_drop(
    plan: Planned, following: Look, halves: bool = False
) -> tuple[list[Cue], int]:
    """The last two bars climb; the last two beats are a colour swell into
    half a beat of dark; the next section opens on one white slam. Returns
    the cues and when the pattern must stop to make room. With `halves`
    (cue format v2) the swell also rises up the door, bottom then top."""
    table = beat_table(plan.phrase)
    ramp = table[-8:]
    cues: list[Cue] = []
    top = min(1.0, plan.look.tower_level * 2.5)
    for k, (t, _p, _b, _bar) in enumerate(ramp):
        level = round(
            plan.look.tower_level + (top - plan.look.tower_level) * (k + 1) / len(ramp),
            2,
        )
        cues += [level_cue(t, z, plan.look.towers, level) for z in ZONES[:2]]
    cut, period = table[-2][0], table[-2][1]
    dark = round(cut + 1.5 * period)
    swell = strike(
        cut, ZONES[:2], following.a, 0.95, period * 0.4, attack=round(1.5 * period)
    )
    cues.append(swell)
    if halves:
        cues += [
            strike(cut + k * period / 2, ["door"], following.a, 0.8, period * 0.4,
                   pixels=side, attack=round(period))
            for k, side in enumerate(("bottom", "top"))
        ]  # fmt: skip
    cues += look_cues(dark, following, 0.0)
    cues.append(strike(plan.phrase.end, ZONES, WHITE, 1.0, period * 2.5))
    return cues, cut


def hand_over(
    plan: Planned, following: Look, halves: bool = False
) -> tuple[list[Cue], int]:
    """Into a different (not louder) section: the last two beats become a
    left-right eighth-note run in the next look's colours, climbing. With
    `halves` (cue format v2) the door's own left and right halves run with
    the towers, so the run crosses the whole castle."""
    table = beat_table(plan.phrase)
    cut, period = table[-2][0], table[-2][1]
    cues = [
        strike(cut + i * period / 2, [ZONES[i % 2]], (following.a, following.b)[i % 2],
               0.5 + 0.12 * i, period * 0.45)
        for i in range(4)
    ]  # fmt: skip
    if halves:
        cues += [
            {**c, "targets": ["door"], "pixels": ("left", "right")[i % 2]}
            for i, c in enumerate(list(cues))
        ]
    return cues, cut


def break_cues(start: int, end: int, look: Look, period: int) -> list[Cue]:
    """The band stops: dark. It comes back: the look, and one hit in its colour."""
    return [
        *look_cues(start, look, 0.0),
        *look_cues(end, look),
        strike(end, ZONES, look.a, 1.0, period * 1.5),
    ]


def voice_notes(
    layers: Mapping[str, Any], duration: int
) -> list[tuple[int, float, int]]:
    """(time, strength, room) per sung onset that is really sung: loud enough
    against the band where it lands, so bleed in an instrumental is dropped."""
    if not has_singer(layers):
        return []
    vocals = layers["vocals"]["both"]
    hits = [h for h in spaced(merged(vocals["onsets"]), 180) if h[0] < duration - 100]
    voice, band = absolute(vocals), absolute(layers["backing"]["both"])
    n = len(voice)

    def audible(t: int) -> bool:
        i = min(n - 1, t * n // max(1, duration)) if n else 0
        return bool(n) and voice[i] >= 0.15 * (band[i] if i < len(band) else 0.0)

    kept = [h for h in hits if audible(h[0])]
    rooms = [b[0] - a[0] for a, b in pairwise(kept)] + [600]
    return [(at, s, room) for (at, s), room in zip(kept, rooms, strict=True)]


def sung_cue(at: int, strength: float, room: int, colour: list[float]) -> Cue:
    """A held note swells in and glows for its length; a quick one flashes."""
    if room >= HELD_MS:
        fade = min(room * 0.85, 1400)
        return strike(at, ["door"], colour, 0.55 + 0.4 * strength, fade, attack=60)
    return strike(at, ["door"], colour, 0.6 + 0.4 * strength, 320)


#: A section change that is not a drop: (plan, next look, halves) → the
#: cues and the time the phrase's own pattern must stop.
Transition = Callable[[Planned, Look, bool], tuple[list[Cue], int]]


def _phrase_cues(
    plans: Sequence[Planned], grid: Grid, layers: Mapping[str, Any], duration: int,
    voice: Sequence[tuple[int, float]], stops: Sequence[tuple[int, int]] = (),
    halves: bool = False, handover: Transition | None = None,
) -> tuple[list[Cue], list[tuple[int, int]], list[dict[str, Any]]]:  # fmt: skip
    """Every band cue, the drops' windows (which the voice stays out of), and
    the timeline. A transition whose last bar holds a stop is left out:
    there is no band to build, and the band's return is the entrance."""
    strength = beat_strengths(grid, rhythm_bands(layers))
    env = absolute(layers["backing"]["both"])
    cues: list[Cue] = []
    reserved: list[tuple[int, int]] = []
    timeline = []
    slam = -1000
    mean = sum(env) / len(env) if env else 0.0
    for i, plan in enumerate(plans):
        nxt = plans[i + 1] if i + 1 < len(plans) else None
        first = i == 0 or plans[i - 1].section is not plan.section
        if first:
            cues += look_cues(plan.section.start, plan.look)
        cues += dynamics(plan, env, mean, duration)
        band = [
            c for c in plan.pattern(plan.phrase, plan.look) if abs(c["t"] - slam) > 120
        ]
        band = with_velocity(
            without_door(band, voice) if voice else band, grid, strength
        )
        drop = False
        if nxt is None:
            band = [c for c in band if c["t"] < plan.phrase.beats[-1]]
            band += finale(plan.phrase, plan.look, duration)
        elif (
            nxt.section is not plan.section
            and len(plan.phrase.beats) >= 8
            and not _stopped(plan.phrase, stops)
        ):
            drop = nxt.section.energy - plan.section.energy > LIFT
            change = build_and_drop if drop else (handover or hand_over)
            extra, cut = change(plan, nxt.look, halves)
            band = [c for c in band if c["t"] < cut] + extra
            if drop:
                slam = plan.phrase.end
                reserved.append((cut, plan.phrase.end))
        cues += band
        timeline.append(
            {"t": plan.phrase.start, "end": plan.phrase.end, "look": plan.look.name,
             "pattern": plan.pattern.__name__, "rank": plan.phrase.rank, "drop": drop,
             "label": "ABCDEFGH"[plan.section.label % 8]}
        )  # fmt: skip
    return cues, reserved, timeline


def _stopped(phrase: Phrase, stops: Sequence[tuple[int, int]]) -> bool:
    """Does a stop fall in the phrase's last bar, where a transition's swell
    or run goes? (A stop that only clips the start of a build's climb
    leaves the band playing through the rest of it.)"""
    since = beat_table(phrase)[-4][0]
    return any(a < phrase.end and since < b for a, b in stops)


def _apply_breaks(
    cues: list[Cue], stops: Sequence[tuple[int, int]], drops: Sequence[tuple[int, int]],
    look_at: Any, grid: Grid,
) -> tuple[list[Cue], list[tuple[int, int]]]:  # fmt: skip
    """The band's cues with each stop cut out and dressed, and the stops that
    were used. The voice is not kept out of a stop: it is what stays lit."""
    used = []
    for start, end in stops:
        if any(a < end and start < b for a, b in drops):
            continue  # a drop already owns this moment
        i = max(0, bisect_right(grid.beats, start) - 1)
        period = grid.beats[i + 1] - grid.beats[i] if i + 1 < len(grid.beats) else 500
        cues = [c for c in cues if not start <= c["t"] < end + 60]
        cues += break_cues(start, end, look_at(end), period)
        used.append((start, end))
    return cues, used


@dataclass
class Draft:
    """Everything options 9 and 10 share: the plan, the band's cues with the
    stops dressed, and the sung notes still to place."""

    grid: Grid
    plans: list[Planned]
    cues: list[Cue]
    drops: list[tuple[int, int]]
    stops: list[tuple[int, int]]
    notes: list[tuple[int, float, int]]
    timeline: list[dict[str, Any]]

    def look_at(self, t: int) -> Look:
        for plan in self.plans:
            if plan.phrase.start <= t < plan.phrase.end:
                return plan.look
        if self.plans:
            return self.plans[-1].look
        return casting({0: 0.5}, {0: 1.0}, 0)[0][0]


def draft(
    source: Mapping[str, Any], layers: Mapping[str, Any], halves: bool = False,
    handover: Transition | None = None,
    restyle: Callable[[list[Planned]], list[Planned]] | None = None,
) -> Draft:  # fmt: skip
    """`restyle` may re-dress the plan (option 13's colours) before any cue
    is written from it."""
    duration = int(source["dur"])
    grid = analyse(layers, duration)
    labels = label_phrases(grid, layers, duration)
    plans = cast(grid, sections(grid, labels), str(source.get("id", "")))
    if restyle is not None:
        plans = restyle(plans)
    notes = voice_notes(layers, duration)
    voice = [(at, s) for at, s, _room in notes]
    stops = breaks(grid, rhythm_bands(layers), layers, duration)
    cues, drops, timeline = _phrase_cues(
        plans, grid, layers, duration, voice, stops, halves, handover
    )
    out = Draft(grid, plans, [], drops, [], notes, timeline)
    out.cues, out.stops = _apply_breaks(cues, stops, drops, out.look_at, grid)
    return out


def preview(
    source: Mapping[str, Any], d: Draft, cues: Sequence[Cue],
    zones: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:  # fmt: skip
    """The prepared-preview shape (pulse_clarity.encode_preview)."""
    first = d.plans[0].look if d.plans else d.look_at(0)
    return {
        **{k: source[k] for k in ("id", "name", "dur") if k in source},
        "base": {"towerL": first.towers, "towerR": first.towers, "door": first.door},
        "levels": {"towerL": first.tower_level, "towerR": first.tower_level,
                   "door": first.door_level},
        "zones": dict(zones),
        "cues": sorted(cues, key=lambda c: c["t"]),
        "sections": d.timeline,
        "breaks": [list(b) for b in d.stops],
        "bpm": round(d.grid.bpm, 1),
        "beats": list(d.grid.beats),
    }  # fmt: skip


def choreograph_sections(
    source: Mapping[str, Any], layers: Mapping[str, Any], lead_ms: int = 0
) -> dict[str, Any]:
    """Option 9, written for today's castle (cue format v1, v5.70)."""
    d = draft(source, layers)
    duration = int(source["dur"])
    placer = Placer()
    placer.fixed(
        sorted([c for c in d.cues if c["t"] < duration - 50], key=lambda c: c["t"])
    )
    placer.reserved = list(d.drops)
    placer.settle()
    for at, s, room in d.notes:
        placer.ornament(sung_cue(at, s, room, d.look_at(at).voice))
    shown = porch.lead(porch.for_todays_castle(placer.cues), lead_ms)
    zones = {z: dict(source.get("zones", {}).get(z, {})) for z in ZONES}
    for tower in ZONES[:2]:
        zones[tower]["overlay"] = "chase"
    return preview(source, d, shown, zones)
