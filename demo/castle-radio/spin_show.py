"""Option 11: Spin. Option 10's plan (sections, looks, drops, stops) with
the lights put in MOTION — for firmware v5.71 (cue format v2 with arcs).
Opt-in and offline like every lab candidate.

The whole castle still lands together on every bar's "one"; between those,
light travels:

- The singer: each sung note lights one ARC of the door ring, and the arc
  walks with the melody — clockwise when the tune climbs, back when it
  falls, a step on when a note repeats — so a sung line circles the door.
  A held note sets a chase spinning round the ring for its length; a high
  note burns whiter. (voice_pitch.py measures the note from the vocal stem.)
- The towers: every hit that is not on the "one" lands as an arc exactly
  where the tempo-locked chase is at that moment, so the towers visibly turn.
- A section change races a light across the castle — left tower, the door's
  left then right side, right tower — the other way at the next change.
- A build spins faster bar by bar into the drop; after the white slam two
  lights burst apart round the door and meet at the bottom.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from typing import Any

import porch
from beat_grid import Grid
from choreography import beat_table
from looks import WHITE, ZONES, Colour, Cue, Look, strike
from sections_show import Draft, Planned, draft, preview, sung_cue
from sections_v2 import FIRMWARE, PALETTE, motion_cues
from voice_pitch import Pitch

ARCS = 8
PHRASE_GAP = 1500  # ms of silence that starts a new sung line at the top
HIGH = 4  # semitones over the singer's median that burn whiter
END = 20  # ms: the last cue lands at least a frame (16 ms) before the end


def arc(k: int) -> str:
    return f"arc{k % ARCS}"


class Heads:
    """Where the towers' chase head is — the v2 overlay clock, replayed
    from the look records (web/src/show_layers.ts setMotion)."""

    def __init__(self, looks: Sequence[Cue]) -> None:
        self.marks: list[tuple[int, float, float]] = []  # (t, rate, head0)
        for cue in sorted(looks, key=lambda c: c["t"]):
            if cue.get("rate") is None:
                continue
            now = self.at(cue["t"])
            head = cue["head"] if cue.get("head") is not None else now
            self.marks.append((cue["t"], float(cue["rate"]), float(head)))

    def at(self, t: int) -> float:
        i = bisect_right([m[0] for m in self.marks], t) - 1
        if i < 0:
            return 0.0
        t0, rate, head0 = self.marks[i]
        return (head0 + rate * (t - t0) / 1000) % 1.0


def downbeats(grid: Grid) -> list[int]:
    return list(grid.beats[grid.downbeat :: 4])


def turning(cues: Sequence[Cue], heads: Heads, grid: Grid) -> list[Cue]:
    """Tower hits off the "one" land where the chase head is; the door's
    band hits (an instrumental passage) step round a turn every two bars."""
    ones = downbeats(grid)
    out = []
    for cue in cues:
        if (
            cue["op"] != "strike" or cue.get("pixels", "all") != "all"
            or cue["attack"] or cue["color"] == WHITE
            or any(abs(cue["t"] - b) <= 40 for b in ones)
        ):  # fmt: skip
            out.append(cue)
            continue
        towers = [z for z in cue["targets"] if z != "door"]
        if towers:
            k = round(heads.at(cue["t"]) * ARCS)
            out.append({**cue, "targets": towers, "pixels": arc(k)})
        if "door" in cue["targets"]:
            k = bisect_right(grid.beats, cue["t"] + 30)
            out.append({**cue, "targets": ["door"], "pixels": arc(k)})
    return out


def race(
    plan: Planned, following: Look, _halves: bool = False
) -> tuple[list[Cue], int]:
    """A section change: one light runs across the castle in four eighths —
    left tower's door-side edge, the door's left then right, the right
    tower's near edge — and back the other way at the next change."""
    table = beat_table(plan.phrase)
    cut, period = table[-2][0], table[-2][1]
    path = [("towerL", 2), ("door", 6), ("door", 2), ("towerR", 6)]
    if plan.section.visit % 2:
        path = [(zone, (k + 4) % ARCS) for zone, k in reversed(path)]
    colours = (following.a, following.b)
    return [
        strike(cut + i * period / 2, [zone], colours[i % 2], 0.55 + 0.12 * i,
               period * 0.45, pixels=arc(k))
        for i, (zone, k) in enumerate(path)
    ], cut  # fmt: skip


def spin_ups(d: Draft, door_overlay: str) -> tuple[list[Cue], list[tuple[int, int]]]:
    """Into a drop: the chase doubles its speed every bar of the build, the
    door joins it, and after the slam two lights burst apart round the door.
    Returns the cues and the builds' windows, which the door's own spin
    stays out of."""
    out: list[Cue] = []
    builds: list[tuple[int, int]] = []
    for plan in d.plans:
        entry = next(
            (s for s in d.timeline if s["t"] == plan.phrase.start and s["drop"]), None
        )
        if entry is None:
            continue
        table = beat_table(plan.phrase)
        period = table[-1][1]
        base = d.grid.bpm / 240
        for k, (t, _p, _b, _bar) in enumerate(table[-8:-2]):
            rate = round(base * 2 ** (1 + k / 2), 3)
            out.append(
                _look(t, list(ZONES), "chase", PALETTE.get(plan.look.name), rate)
            )
        dark = round(table[-2][0] + 1.5 * period)
        builds.append((table[-8][0], plan.phrase.end))
        out.append({"t": dark, "bus": "LED", "op": "look", "targets": ["door"],
                    "overlay": door_overlay})  # fmt: skip
        slam = plan.phrase.end
        for n in range(1, 5):
            at = round(slam + n * period / 4)
            for layer, k in ((0, n), (1, ARCS - n)):
                out.append({"t": at, "bus": "LED", "op": "strike", "targets": ["door"],
                            "intensity": 0.9, "decay": 0.8, "attack": 0,
                            "pixels": arc(k), "color": WHITE if n == 4 else plan.look.a,
                            "ms": 120, "layer": layer})  # fmt: skip
    return out, builds


def _look(t: int, zones: list[str], overlay: str, palette: str | None,
          rate: float, head: float | None = None) -> Cue:  # fmt: skip
    cue: Cue = {"t": t, "bus": "LED", "op": "look", "targets": zones,
                "overlay": overlay, "rate": rate}  # fmt: skip
    if palette:
        cue["palette"] = palette
    if head is not None:
        cue["head"] = head
    return cue


def _whiter(colour: Colour, share: float) -> Colour:
    return [round(c + (w - c) * share, 3) for c, w in zip(colour, WHITE, strict=True)]


def sung_arcs(
    d: Draft, pitch: Pitch | None, duration: int, door_overlay: str,
    builds: Sequence[tuple[int, int]] = (),
) -> list[Cue]:  # fmt: skip
    """Each sung note as an arc on the ornament layer, walking with the
    melody; a held note spins a chase round the door for its length."""
    notes = [
        (at, s, room) for at, s, room in d.notes
        if at < duration - 50 and not any(a <= at < b for a, b in d.drops)
    ]  # fmt: skip
    heard = [pitch.at(at + 30, at + min(room, 300)) if pitch else None
             for at, _s, room in notes]  # fmt: skip
    known = sorted(n for n in heard if n is not None)
    middle = known[len(known) // 2] if known else None
    out: list[Cue] = []
    pos, last_at, last_note = 0, -PHRASE_GAP, None
    for (at, strength, room), note in zip(notes, heard, strict=True):
        if at - last_at >= PHRASE_GAP:
            pos, last_note = 0, None
        elif note is None or last_note is None or abs(note - last_note) < 1:
            pos += 1
        else:
            step = round((note - last_note) / 2) or (1 if note > last_note else -1)
            pos += max(-3, min(3, step))
        look = d.look_at(at)
        colour = look.voice
        if note is not None and middle is not None and note >= middle + HIGH:
            colour = _whiter(colour, 0.35)
        cue = {**sung_cue(at, strength, room, colour), "layer": 1, "pixels": arc(pos)}
        out.append(cue)
        spinning = any(a <= at < b for a, b in builds)
        if cue["attack"] and not spinning:  # a held note: spin for its length
            rate = round(max(0.6, min(2.5, 1000 / room)), 3)
            out.append(_look(at, ["door"], "chase", PALETTE.get(look.name), rate,
                             head=(pos % ARCS) / ARCS))  # fmt: skip
            back = min(round(at + room * 0.85), duration - END)  # inside the song
            out.append({"t": back, "bus": "LED", "op": "look",
                        "targets": ["door"], "overlay": door_overlay})  # fmt: skip
        last_at = at
        if note is not None:
            last_note = note
    return out


def choreograph_spin(
    source: Mapping[str, Any], layers: Mapping[str, Any], pitch: Pitch | None = None,
    lead_ms: int = 0,
) -> dict[str, Any]:  # fmt: skip
    """Option 11, in the prepared-preview shape (pulse_clarity.encode_preview)."""
    d = draft(source, layers, halves=True, handover=race)
    duration = int(source["dur"])
    zones = {z: dict(source.get("zones", {}).get(z, {})) for z in ZONES}
    door_overlay = zones["door"].get("overlay", "none")
    ups, builds = spin_ups(d, door_overlay)
    motion = motion_cues(d) + ups
    heads = Heads([c for c in motion if c["op"] == "look" and "towerL" in c["targets"]])
    band = turning([c for c in d.cues if c["t"] < duration - 50], heads, d.grid)
    voice = sung_arcs(d, pitch, duration, door_overlay, builds)
    motion = [c for c in motion if c["t"] <= duration - END]  # a drop at the end
    shown = porch.lead([*band, *motion, *voice], lead_ms)
    return {**preview(source, d, shown, zones), "firmware": FIRMWARE}
