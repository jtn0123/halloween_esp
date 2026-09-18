"""A scene's cue list, previewer-shaped.

Split from gen_previewer.py along the seam that was already commented there:
this is what a cue MEANS to the browser desk — score events, hand-written
cues, and the pulse streams expanded hit by hit — while gen_previewer.py
stays the page BUILDER (template, bundle, styles, data URIs, budget).

Deliberately a mirror of tools/pulse_expand.py rather than a shared
implementation: that module writes the firmware's strike cues and this one
writes the desk's, the two shapes differ in their field names, and the
arithmetic they must agree on is the part that already lives in
pulse_dynamics.py. web/test/fuzz_parity.ts compares all three answers
digit-for-digit, so a drift here is a red test rather than a bad show.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Mapping, Sequence
from typing import Any

import pulse_dynamics as pd
from effect_vocab import KNOWN_EFFECTS

#: The section gate table pulse_dynamics builds and reads: (t_ms, note).
Gates = list[tuple[int, str]]

#: One previewer cue. Untyped values because the desk's cue is a union of
#: three shapes (AUD play, LED set, LED strike) keyed by `op`.
Cue = dict[str, Any]


def blend_color(base: list[float], hot: list[float] | None, vel: float) -> list[float]:
    """color -> color_hot by velocity — same maths as gen_esphome.blend_color
    and track_lights.ts."""
    if not hot:
        return base
    return [pd.round3(b + (h - b) * vel) for b, h in zip(base, hot)]


def score_cues(scene: Mapping[str, Any]) -> list[Cue]:
    """The scene's audio score as AUD cues. A looping scene's wind bed is the
    one event that plays as a loop rather than a one-shot."""
    return [
        {
            "t": int(ev["t"] * 1000),
            "bus": "AUD",
            "op": "play_loop"
            if scene.get("loop") and ev["synth"] == "wind"
            else "play",
            "snd": ev["synth"],
        }
        for ev in scene.get("score") or []
    ]


def _set_cue(cue: Mapping[str, Any], sid: str) -> Cue:
    """A hand-written `set`: one zone told to run one effect."""
    if cue["effect"] not in KNOWN_EFFECTS:
        sys.exit(f"scene {sid}: unknown effect {cue['effect']!r}")
    c: Cue = {
        "t": cue["t"],
        "bus": "LED",
        "op": "set",
        "zone": cue["zone"],
        "eff": cue["effect"],
        "detail": cue.get("note", ""),
    }
    if "level" in cue:
        c["level"] = float(cue["level"])
    return c


def _strike_cue(cue: Mapping[str, Any]) -> Cue:
    """A hand-written `strike`, carrying every field gen_esphome.py honours.

    It has always read targets/intensity/color/decay; this side used to copy
    only zone, so a cue aimed at one zone flashed the whole chain in the
    browser, in default white, at full intensity. Latent — today's scenes only
    set those inside `pulse:` — but a divergence between preview and device is
    the one bug this project cannot afford, latent or not.
    """
    c: Cue = {
        "t": cue["t"],
        "bus": "LED",
        "op": "strike",
        "ms": cue.get("ms", 80),
        "detail": cue.get("note", ""),
    }
    if cue.get("targets"):
        c["targets"] = cue["targets"]
    if cue.get("zone"):
        c["zone"] = cue["zone"]
    if "intensity" in cue:
        c["intensity"] = float(cue["intensity"])
    if "color" in cue:
        c["color"] = cue["color"]
    if "decay" in cue:
        c["decay"] = float(cue["decay"])
    if "pixels" in cue:
        c["pixels"] = cue["pixels"]
    if "attack" in cue:
        c["attack"] = int(cue["attack"])
    return c


def hand_cues(scene: Mapping[str, Any], sid: str) -> list[Cue]:
    """The scene's own `cues:` list. An op this side does not know is a hard
    stop, not a dropped cue: the desk would otherwise preview a show the
    firmware plays differently."""
    out: list[Cue] = []
    for cue in scene.get("cues") or []:
        if cue["op"] == "set":
            out.append(_set_cue(cue, sid))
        elif cue["op"] == "strike":
            out.append(_strike_cue(cue))
        else:
            sys.exit(f"scene {sid}: unknown cue op {cue['op']!r}")
    return out


def _stream_color(
    pcfg: Mapping[str, Any], gates: Gates, i: int, t: int
) -> tuple[list[float], list[float] | None]:
    """The hit's (base, hot) colours, before velocity blends between them."""
    cyc = pcfg.get("colors")
    hot = pcfg.get("color_hot")
    if pcfg.get("takeover") and pd.gate_note(gates, t) == "chorus":
        # #2: in a chorus the castle agrees on one warm family.
        return pd.TAKEOVER_COLORS[i % len(pd.TAKEOVER_COLORS)], pd.TAKEOVER_HOT
    if cyc and pcfg.get("drift"):
        return pd.drift_base(cyc, i, t), hot  # #1: hues walk over time
    return (cyc[i % len(cyc)] if cyc else pcfg.get("color", [1, 1, 1, 1])), hot


def _stream_pixels(pcfg: Mapping[str, Any], vel: float) -> str | None:
    """Where on the fixture the hit lands, or None to leave the field off the
    cue. Velocity picks the mask when the stream asked for it — soft centre,
    medium scatter, hard all, the thresholds pulse_expand.pixels_for uses."""
    if pcfg.get("pixels_by_vel"):
        if vel < 0.40:
            return "center"
        return "scatter" if vel < 0.72 else "all"
    px = pcfg.get("pixels")
    return str(px) if px else None


def _stream_targets(
    pcfg: Mapping[str, Any],
    zones: list[str] | None,
    i: int,
    pan: float | None,
    vel: float,
    vels: list[float],
) -> list[str] | None:
    """Which zones one hit lands on, or None to leave the field off the cue —
    which is how the desk spells "every zone", the same as a targets-less
    strike on the device."""
    targets: list[str] | None = None
    if zones and pcfg.get("alternate"):
        if (
            pan is not None
            and abs(pan) >= pd.PAN_DECISIVE
            and "towerL" in zones
            and "towerR" in zones
        ):
            # #7: a decisively panned hit goes to ITS tower rather than
            # taking its turn in the round-robin.
            targets = ["towerL" if pan < 0 else "towerR"]
        else:
            targets = [zones[i % len(zones)]]
    elif zones:
        targets = list(zones)
    if (
        targets
        and pcfg.get("boost_targets")
        and (vel >= pcfg.get("boost_at", 2) or pd.is_accent(vels, i))
    ):
        targets = targets + [z for z in pcfg["boost_targets"] if z not in targets]
    return targets


def _stream_cues(
    pcfg: Mapping[str, Any], beats: Sequence[Any], gates: Gates
) -> list[Cue]:
    """One `pulse:` stream: a strike per marker the section gates let through,
    at the stream's own tempo-adjusted length and decay."""
    zones = pcfg.get("zones") or ([pcfg["zone"]] if pcfg.get("zone") else None)
    factor = pd.tempo_factor([b[0] / 1000.0 for b in beats])
    decay = pd.tempo_decay(pcfg.get("decay", 0.90), factor)
    ms = math.floor(int(pcfg.get("ms", 120)) * factor + 0.5)
    vels = [b[1] for b in beats]
    out: list[Cue] = []
    for i, beat in enumerate(beats):
        t, vel = beat[0], beat[1]
        pan = beat[2] if len(beat) > 2 else None
        mul = pd.gate_mul(pcfg["synth"], gates, t)
        if mul is None:
            continue  # gated out by its section (#9)
        base, hot = _stream_color(pcfg, gates, i, t)
        c: Cue = {
            "t": t,
            "bus": "LED",
            "op": "strike",
            "ms": ms,
            "intensity": pd.round3(pcfg.get("intensity", 0.3) * vel * mul),
            "color": blend_color(base, hot, vel),
            "decay": decay,
            "detail": pcfg["synth"],
        }
        if pcfg.get("attack_ms"):
            c["attack"] = int(pcfg["attack_ms"])  # #10: rise time to peak
        pixels = _stream_pixels(pcfg, vel)
        if pixels is not None:
            c["pixels"] = pixels
        targets = _stream_targets(pcfg, zones, i, pan, vel, vels)
        if targets is not None:
            c["targets"] = targets
        out.append(c)
    return out


def pulse_cues(scene: Mapping[str, Any], markers: Mapping[str, Any]) -> list[Cue]:
    """Every pulse stream expanded: one stream per synth, colour and decay per
    stream, velocity per marker. The same merge as tools/pulse_expand.py
    (which documents the per-hit dynamics) and web/src/track_lights.ts — keep
    all three in lockstep.
    """
    scene_marks = markers.get(scene["id"], {})
    gates = pd.section_gates(scene)
    out: list[Cue] = []
    for pcfg in scene.get("pulse") or []:
        out.extend(_stream_cues(pcfg, scene_marks.get(pcfg["synth"], []), gates))
    return out
