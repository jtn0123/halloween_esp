"""Option 12: Ensemble. Option 11 (Spin) with every part of the band and
every kind of voice given its own light — for firmware v5.71 (cue format v2
with arcs). Opt-in and offline like every lab candidate.

On top of Spin's sections, looks, drops and travelling lights:

- The drums as three instruments (drum_kit.py): kick low on the towers,
  snare cracking round them with the chase, hi-hats as faint sparkles.
- Colour from harmony (harmony.py): each bar's chord, read against the
  song's key, tints the band's hits — home in the look's own colours, away
  answering, shadow (another minor chord) colder, strange (outside the key)
  a sickly green — and the towers' chase changes palette with it.
- Kinds of voice (voice_kinds.py): spoken lines turn the door into watching
  eyes that flicker with the words instead of walking a melody; a choir
  behind the lead lights the towers too; a held note with vibrato shimmers
  instead of spinning; a bright, belted note burns whiter and a dark, breathy
  one deeper and softer.
- Bookends (bookends.py): a candle-lit intro that wakes into the first bar,
  and a finale that matches how the song ends — a cold stop to black, a fade
  with the band, or a last chord left to ring.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import bookends
import drum_kit
import harmony
import porch
import sections_v2
from choreography import merged
from looks import WHITE, ZONES, Colour, Cue, Look, level_cue, strike
from sections_show import Draft, Planned, draft, preview
from sections_v2 import FIRMWARE, PALETTE, motion_cues
from spin_show import END, Heads, downbeats, race, spin_ups, sung_arcs, turning
from structure import absolute
from voice_kinds import Kind, VoiceTrack, kinds
from voice_pitch import Pitch

SHADOW: Colour = [0.2, 0.08, 1.0, 0.0]
STRANGE: Colour = [0.35, 1.0, 0.05, 0.0]
PALE: Colour = [0.45, 0.55, 0.7, 0.4]  # a spoken word
MOOD_PALETTE = {"shadow": "moonlight", "strange": "toxic"}
TOWERS = list(ZONES[:2])


def blend(a: Colour, b: Colour, share: float) -> Colour:
    return [round(x + (y - x) * share, 3) for x, y in zip(a, b, strict=True)]


def bars_of(d: Draft) -> list[tuple[int, int]]:
    ones = downbeats(d.grid)
    return list(pairwise(ones))


def tint(cues: Sequence[Cue], moods: Sequence[str | None],
         bars: Sequence[tuple[int, int]], d: Draft) -> list[Cue]:  # fmt: skip
    """The band's hits recoloured by their bar's mood."""
    starts = [a for a, _b in bars]
    out = []
    for cue in cues:
        i = bisect_right(starts, cue["t"] + 30) - 1
        m = moods[i] if 0 <= i < len(moods) else None
        if cue["op"] != "strike" or cue["color"] == WHITE or m in (None, "home"):
            out.append(cue)
            continue
        look, colour = d.look_at(cue["t"]), cue["color"]
        if m == "away":
            colour = {tuple(look.a): look.b, tuple(look.b): look.a}.get(
                tuple(colour), colour
            )
        else:
            colour = blend(colour, SHADOW if m == "shadow" else STRANGE, 0.55)
        out.append({**cue, "color": colour})
    return out


def mood_looks(moods: Sequence[str | None], bars: Sequence[tuple[int, int]],
               d: Draft) -> list[Cue]:  # fmt: skip
    """The towers' chase palette follows the mood, bar by bar; a bar with no
    chord keeps what is showing."""
    out, showing = [], None
    for (t, _end), m in zip(bars, moods, strict=True):
        if m is None:
            continue
        want = MOOD_PALETTE.get(m) or PALETTE.get(d.look_at(t).name, "haunt")
        if want != showing:
            out.append({"t": t, "bus": "LED", "op": "look", "targets": TOWERS,
                        "palette": want})  # fmt: skip
            showing = want
    return out


def _spans(
    notes: Sequence[tuple[int, float, int]], flags: Sequence[bool]
) -> list[tuple[int, int]]:
    """Runs of flagged notes as (start, end) ms."""
    out: list[tuple[int, int]] = []
    for (at, _s, room), on in zip(notes, flags, strict=True):
        end = at + min(room, 600)
        if on and out and at - out[-1][1] < 1500:
            out[-1] = (out[-1][0], end)
        elif on:
            out.append((at, end))
    return out


def _toned(cue: Cue, k: Kind, look: Look) -> Cue:
    if k.tone > 0.3:
        return {**cue, "color": blend(cue["color"], WHITE, 0.3 * k.tone),
                "intensity": round(min(1.0, cue["intensity"] + 0.1), 3)}  # fmt: skip
    if k.tone < -0.3:
        return {**cue, "color": blend(cue["color"], look.b, 0.35 * -k.tone),
                "intensity": round(cue["intensity"] * 0.85, 3),
                "attack": max(cue["attack"], 60)}  # fmt: skip
    return cue


def dress(
    voice: Sequence[Cue], d: Draft, ks: Sequence[Kind]
) -> tuple[list[Cue], list[tuple[int, int]]]:
    """Spin's voice cues dressed by kind of voice, and the windows a choir
    holds the towers' ornament layer (which the hi-hats keep out of)."""
    note = {at: (s, room, k) for (at, s, room), k in zip(d.notes, ks, strict=True)}
    out: list[Cue] = []
    choir: list[tuple[int, int]] = []
    for cue in voice:
        t = cue["t"]
        if t not in note:
            out.append(cue)
            continue
        s, room, k = note[t]
        look = d.look_at(t)
        if cue["op"] == "look":  # a held note's spin
            if k.spoken:
                continue
            shimmer = {"t": t, "bus": "LED", "op": "look", "targets": ["door"],
                       "overlay": "sparkle", "palette": PALETTE.get(look.name, "haunt")}  # fmt: skip
            out.append(shimmer if k.vibrato else cue)
            continue
        if k.spoken:
            flicker = strike(t, ["door"], PALE, 0.3 + 0.3 * s, 180, "scatter")
            out.append({**flicker, "layer": 1})
            continue
        out.append(_toned(cue, k, look))
        if k.choir:
            fade = min(room * 0.8, 1400)
            swell = strike(t, TOWERS, blend(look.voice, WHITE, 0.2), 0.35 + 0.3 * s,
                           fade, "ring", attack=80)  # fmt: skip
            out.append({**swell, "layer": 1})
            choir.append((t, round(t + fade)))
    for start, end in _spans(d.notes, [k.spoken for k in ks]):
        look = d.look_at(end)
        out += [level_cue(start, "door", "eyes", 0.5),
                level_cue(end, "door", look.door, look.door_level)]  # fmt: skip
    return out, choir


def faded(
    band: Sequence[Cue], env: Sequence[float], duration: int, start: int
) -> list[Cue]:
    """From `start` on, each hit only as bright as the band still is."""
    top, n = (max(env) if env else 0.0) or 1.0, max(1, len(env))
    out = []
    for cue in band:
        if cue["t"] < start or cue["op"] != "strike" or not env:
            out.append(cue)
            continue
        share = min(1.0, 2 * env[min(n - 1, cue["t"] * n // duration)] / top)
        out.append({**cue, "intensity": round(cue["intensity"] * share, 3)})
    return out


def _last_hit(layers: Mapping[str, Any], before: int) -> int:
    hits = [round(h[0] * 1000) for h in merged(layers["backing"]["both"]["onsets"])
            if h[1] >= 0.5 and h[0] * 1000 <= before]  # fmt: skip
    return max(hits, default=before)


def _period(d: Draft, t: int) -> int:
    i = max(0, bisect_right(d.grid.beats, t) - 1)
    beats = d.grid.beats
    return beats[i + 1] - beats[i] if i + 1 < len(beats) else 500


@dataclass
class Parts:
    """Option 12 before the porch's lead: what option 13 dresses further."""

    d: Draft
    shown: list[Cue]
    zones: dict[str, dict[str, Any]]
    story: dict[str, Any]
    end: bookends.Ending
    ks: list[Kind]


def ensemble(
    source: Mapping[str, Any], layers: Mapping[str, Any], pitch: Pitch | None = None,
    voice: VoiceTrack | None = None, chroma: harmony.Chroma | None = None,
    restyle: Callable[[list[Planned]], list[Planned]] | None = None,
) -> Parts:  # fmt: skip
    d = draft(source, layers, halves=True, handover=race, restyle=restyle)
    duration = int(source["dur"])
    zones = {z: dict(source.get("zones", {}).get(z, {})) for z in ZONES}
    door_overlay = zones["door"].get("overlay", "none")
    ups, builds = spin_ups(d, door_overlay)
    motion = motion_cues(d) + ups
    heads = Heads([c for c in motion if c["op"] == "look" and "towerL" in c["targets"]])
    band = turning([c for c in d.cues if c["t"] < duration - 50], heads, d.grid)
    kit = drum_kit.kit(layers)
    band = drum_kit.drummed(band, kit, d, heads)
    bars = bars_of(d)
    moods: list[str | None] = (
        harmony.bar_moods(chroma, bars) if chroma else [None] * len(bars)
    )
    band = tint(band, moods, bars, d)
    motion += mood_looks(moods, bars, d)
    ks = kinds(d.notes, pitch, voice)
    sung, choir = dress(sung_arcs(d, pitch, duration, door_overlay, builds), d, ks)
    band = [c for c in band if not (c.get("layer") and c["targets"][0] in TOWERS
                                    and any(a <= c["t"] < b for a, b in choir))]  # fmt: skip
    env = absolute(layers["backing"]["both"])
    first = bookends.band_in(env, duration, downbeats(d.grid))
    if first >= bookends.INTRO_MS:  # candles until the band comes in
        band = [c for c in band if c["t"] >= first]
        motion = [c for c in motion if c["t"] >= first]
        look = d.look_at(first)
        motion.append(sections_v2.motion(first, look, d.grid.bpm, 1.0))
    last_beat = d.plans[-1].phrase.beats[-1] if d.plans else duration
    end = bookends.ending(env, duration, _last_hit(layers, last_beat + 400))
    cut = min(end.at, last_beat)
    if end.kind == "fade":
        band = faded([c for c in band if c["t"] < last_beat], env, duration, end.at)
        motion = [c for c in motion if c["t"] < end.at]
    else:
        band = [c for c in band if c["t"] < cut]
        motion = [c for c in motion if c["t"] < cut]
        if end.kind == "cold":
            sung = [c for c in sung if c["t"] < end.at + _period(d, end.at)]
    finale = bookends.finale(end, d.look_at(end.at), _period(d, end.at), duration, env)
    shown = [
        *bookends.intro(first, d.look_at(first), door_overlay),
        *band,
        *motion,
        *sung,
        *finale,
    ]
    shown = [c for c in shown if c["t"] <= duration - END]
    story = {
        "drums": kit is not None,
        "moods": {m: moods.count(m) for m in harmony.MOODS if m in moods},
        "spoken": sum(k.spoken for k in ks), "choir": sum(k.choir for k in ks),
        "vibrato": sum(k.vibrato for k in ks), "intro": first >= bookends.INTRO_MS,
        "ending": end.kind,
    }  # fmt: skip
    return Parts(d, shown, zones, story, end, ks)


def choreograph_ensemble(
    source: Mapping[str, Any], layers: Mapping[str, Any], pitch: Pitch | None = None,
    voice: VoiceTrack | None = None, chroma: harmony.Chroma | None = None,
    lead_ms: int = 0,
) -> dict[str, Any]:  # fmt: skip
    """Option 12, in the prepared-preview shape (pulse_clarity.encode_preview)."""
    p = ensemble(source, layers, pitch, voice, chroma)
    shown = porch.lead(p.shown, lead_ms)
    return {**preview(source, p.d, shown, p.zones), "firmware": FIRMWARE,
            "story": p.story}  # fmt: skip
