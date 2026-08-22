#!/usr/bin/env python3
"""Expand a scene's pulse streams into strike cues.

Beat markers from the audio render, turned into light pulses — the Python
copy of the arithmetic in web/src/track_lights.ts (bandStrikes), consuming
the shared per-stream dynamics in pulse_dynamics.py. Split from
gen_esphome.py along the seam that was already documented there: this is
what a pulse stream MEANS; gen_esphome.py is how the device is told.

The same arithmetic lives in web/src/track_lights.ts and is exercised
digit-for-digit by web/test/fuzz_parity.mjs -> tools/fuzz_check.py.
"""

from __future__ import annotations

import math

from pulse_dynamics import (
    PAN_DECISIVE,
    TAKEOVER_COLORS,
    TAKEOVER_HOT,
    drift_base,
    gate_mul,
    gate_note,
    is_accent,
    round3,
    section_gates,
    tempo_decay,
    tempo_factor,
)

WHITE = [1.0, 1.0, 1.0, 1.0]   # default strike colour (lightning)
DEFAULT_DECAY = 0.90           # default strike decay per 16 ms frame


def blend_color(base: list, hot: list | None, vel: float) -> list:
    """color -> color_hot by velocity. Identical in track_lights.ts."""
    if not hot:
        return base
    return [round3(b + (h - b) * vel) for b, h in zip(base, hot)]


def pixels_for(cfg: dict, vel: float) -> str:
    """Velocity picks the strike mask: soft centre, medium scatter, hard all.

    Thresholds are shared with track_lights.ts and gen_previewer.py.
    """
    if not cfg.get("pixels_by_vel"):
        return str(cfg.get("pixels", "all"))
    return "center" if vel < 0.40 else ("scatter" if vel < 0.72 else "all")


def pulse_cues(scene: dict, markers: dict) -> list[dict]:
    """Beat markers from the audio render, turned into light pulses.

    The renderer reports the ACTUAL event times AND loudness of every sound
    (both heartbeat thumps, each whispered word, chord onsets, downbeats), so
    these stay locked to the audio even when a synth jitters its own timing.

    A scene opts in with a `pulse:` list — one stream per synth, each with
    its own colour, intensity and decay, so a heartbeat can be a fast red
    snap while a bell toll is a slow violet bloom. `zones` + `alternate`
    round-robins markers across zones (whispers moving between the towers).

    Dynamics, all per hit, all driven by the marker's velocity:
      colors:        a LIST of colours the stream cycles through hit by hit,
                     so consecutive hits differ in hue, not just brightness.
                     Overrides `color` when present.
      color_hot:     a second colour; each hit blends its base->color_hot by
                     vel, so soft hits sit deep in the hue and hard hits go
                     bright. Keep it saturated: a white-ish hot colour makes
                     every LOUD hit white, and the loud hits are the ones you
                     see — the show reads as "mostly white" (round-3 gauntlet
                     screenshots proved it).
      pixels_by_vel: soft hits touch the centre, medium hits scatter, hard
                     hits take the whole jewel (overrides `pixels`).
      boost_at/boost_targets: a hit at or above `boost_at` spills onto the
                     extra zones too — the big downbeat that lights the castle.
      ms:            strike length for this stream (default 120).

    The same arithmetic lives in web/src/track_lights.ts and
    tools/gen_previewer.py — keep all three in lockstep.
    """
    scene_marks = markers.get(scene["id"], {})
    gates = section_gates(scene)
    out = []
    for cfg in scene.get("pulse") or []:
        beats = scene_marks.get(cfg["synth"], [])
        if not beats:
            print(f"note: scene {scene['id']}: no markers for synth "
                  f"{cfg['synth']!r} — pulse stream skipped")
        zones = cfg.get("zones") or ([cfg["zone"]] if cfg.get("zone") else None)
        # Times arrive in ms here; the tempo maths speaks seconds everywhere.
        factor = tempo_factor([b[0] / 1000.0 for b in beats])
        decay = tempo_decay(cfg.get("decay", DEFAULT_DECAY), factor)
        ms = math.floor(int(cfg.get("ms", 120)) * factor + 0.5)
        vels = [b[1] for b in beats]
        for i, beat in enumerate(beats):
            t, vel = beat[0], beat[1]
            pan = beat[2] if len(beat) > 2 else None
            mul = gate_mul(cfg["synth"], gates, t)
            if mul is None:
                continue                       # gated out by its section (#9)
            targets: list[str] | None
            if zones and cfg.get("alternate"):
                # A decisively panned hit goes to ITS tower (#7); everything
                # else keeps the round-robin movement.
                if (pan is not None and abs(pan) >= PAN_DECISIVE
                        and "towerL" in zones and "towerR" in zones):
                    targets = ["towerL" if pan < 0 else "towerR"]
                else:
                    targets = [zones[i % len(zones)]]
            else:
                targets = list(zones) if zones else None   # None -> all zones
            if (targets and cfg.get("boost_targets")
                    and (vel >= cfg.get("boost_at", 2) or is_accent(vels, i))):
                targets = targets + [z for z in cfg["boost_targets"]
                                     if z not in targets]
            cyc = cfg.get("colors")
            hot = cfg.get("color_hot")
            if cfg.get("takeover") and gate_note(gates, t) == "chorus":
                # #2: in a chorus the castle agrees on one warm family.
                base = TAKEOVER_COLORS[i % len(TAKEOVER_COLORS)]
                hot = TAKEOVER_HOT
            elif cyc and cfg.get("drift"):
                base = drift_base(cyc, i, t)     # #1: hues walk over time
            else:
                base = cyc[i % len(cyc)] if cyc else cfg.get("color", WHITE)
            out.append({"t": t, "op": "strike", "targets": targets,
                        "ms": ms,
                        "intensity": round3(cfg.get("intensity", 0.3) * vel * mul),
                        "color": blend_color(base, hot, vel),
                        "decay": decay,
                        # #10: rise time to peak; 0 keeps the instant slam.
                        "attack": int(cfg.get("attack_ms", 0)),
                        # WHERE on the jewel the pulse lands: a bass thump can
                        # hit the door's centre while highs scatter the rings.
                        "pixels": pixels_for(cfg, vel),
                        "note": cfg["synth"]})
    return out
