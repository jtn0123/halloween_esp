#!/usr/bin/env python3
"""Per-stream pulse dynamics shared by both Python generators.

tempo (#3), local accents (#8), pan routing threshold (#7), and section
gating (#5/#9) — the rules gen_esphome.py and gen_previewer.py apply
identically to every pulse stream. One Python copy here; the deliberate
duplicate is web/src/track_lights.ts, pinned digit-for-digit by
tests/test_stream_dynamics.py and web/test/track_lights_logic.mjs.
"""

from __future__ import annotations

import math


def tempo_factor(times_s: list[float]) -> float:
    """#3: the stream's own pace, from the median gap between its hits.

    A 180 BPM track and a 70 BPM one used to get identical tails, so fast
    material smeared and slow material strobed. Below 8 hits there is no
    tempo evidence and the factor stays neutral — which also keeps every
    hand-written pulse stream exactly as it was.

    Same arithmetic in gen_previewer.py and track_lights.ts.
    """
    if len(times_s) < 8:
        return 1.0
    gaps = sorted(times_s[i + 1] - times_s[i] for i in range(len(times_s) - 1))
    m = len(gaps) // 2
    g = gaps[m] if len(gaps) % 2 else (gaps[m - 1] + gaps[m]) / 2
    return min(1.6, max(0.7, g / 0.45))


def tempo_decay(decay: float, factor: float) -> float:
    """Stretch or shrink the tail: factor scales time-to-dark, not the decay
    number itself (0.945 x 1.05 would exceed 1 and never die). floor(+0.5)
    instead of round(): Python rounds half-even, JS half-up, and the parity
    tests compare digits."""
    return math.floor((1 - (1 - decay) / factor) * 10000 + 0.5) / 10000


def round3(x: float) -> float:
    """floor(x*1000+0.5)/1000 — the exact arithmetic of JS's
    Math.round(x*1000)/1000, which is what track_lights.ts does to every
    intensity and colour channel. Python's round(x, 3) disagrees with it
    whenever the *1000 float drift crosses the half (the fuzz found 546
    such strikes in 2433): same cure as tempo_decay, half-up via floor."""
    return math.floor(x * 1000 + 0.5) / 1000


def is_accent(vels: list[float], i: int) -> bool:
    """#8: louder than its own recent neighbourhood, not just loud.

    On a compression-mastered track every other hit clears a global
    threshold; against a rolling window only the real accents do. Needs 3
    prior hits — the first few have no neighbourhood to stand out from.
    """
    w = vels[max(0, i - 8):i]
    return len(w) >= 3 and vels[i] >= sum(w) / len(w) + 0.25 and vels[i] >= 0.55


# A pan this far off-centre overrides round-robin movement (#7). Below it,
# stereo position is a mixing accident, not a statement. 0.25 was the guess
# before any stereo material existed; measured against the real library
# (median |pan| 0.00-0.07, wide moments 0.10-0.61) it fired on ~0.3% of
# hits. 0.10 sits above the analyzer's dead zone and the mixes' centre
# wobble, and catches the moments that are audibly on one side.
PAN_DECISIVE = 0.10

# The section notes a generated scene's set cues carry (track_lights.ts
# sectionCues). "predim" is deliberately absent: the dip is scenery, not a
# section, and must not gate strikes.
SECTION_NOTES = ("hush", "verse", "chorus", "silence")


def section_gates(scene: dict) -> list[tuple[int, str]]:
    """The scene's section timeline, reconstructed from its own set cues.

    Generated scenes carry hush/verse/chorus/silence as explicit `set` cues
    (that is ALL the section information the YAML has), so gating strikes by
    section needs no envelope here — the browser built the timeline, wrote
    it down, and this reads it back. Same list as sectionGates() in
    track_lights.ts, [t_ms, note], deduped across the three zones.
    """
    out: list[tuple[int, str]] = []
    for c in scene.get("cues") or []:
        if c.get("op") != "set" or c.get("note") not in SECTION_NOTES:
            continue
        if not out or out[-1][0] != c["t"]:
            out.append((c["t"], c["note"]))
    return out


def gate_mul(synth: str, gates: list[tuple[int, str]], t_ms: int) -> float | None:
    """#9: what a hit's section does to it — None drops it, else a
    multiplier. Twin of gateMul in track_lights.ts."""
    note = None
    for gt, n in gates:
        if gt <= t_ms:
            note = n
        else:
            break
    if note == "silence":
        return None
    if note == "hush":
        if synth == "onset_high":
            return None
        if synth == "onset_mid":
            return 0.5
    return 1.0


def gate_note(gates: list[tuple[int, str]], t_ms: int) -> str | None:
    """The section a moment lives in, or None before any boundary."""
    note = None
    for gt, n in gates:
        if gt <= t_ms:
            note = n
        else:
            break
    return note


# ── Phase-3 flavours (both default OFF; a scene opts in per stream) ────

# #1 Palette drift: the hit's base colour walks AROUND the band's own triad
# over time — one full lap per DRIFT_PERIOD_S — so a three-minute song never
# repeats a look. Plain lerp between neighbouring cycle colours: no HSV
# matrices, so the two Python sides and the TS side agree to the digit.
DRIFT_PERIOD_S = 60.0


def drift_base(colors: list, i: int, t_ms: int) -> list:
    """Twin of driftBase in track_lights.ts."""
    n = len(colors)
    if n < 2:
        return list(colors[0])
    p = (t_ms / 1000.0 % DRIFT_PERIOD_S) / DRIFT_PERIOD_S
    pos = (i + p * n) % n
    k = int(pos)
    f = pos - k
    a, b = colors[k], colors[(k + 1) % n]
    return [round3(x + (y - x) * f) for x, y in zip(a, b)]


# #2 Chorus takeover: during a chorus the whole castle agrees on ONE warm
# family instead of three per-band hues — the splinter back to per-band
# colour at the verse is what makes the chorus feel like an event. Table
# passes the same saturation guards as BAND_STYLE (one channel low, W low).
TAKEOVER_COLORS = [
    [1.0, 0.55, 0.0, 0.0],    # gold
    [1.0, 0.25, 0.0, 0.05],   # flame orange
    [1.0, 0.0, 0.35, 0.0],    # hot rose
]
TAKEOVER_HOT = [1.0, 0.75, 0.1, 0.12]

#: ESPHome keeps ~20 bytes of STATIC RAM per generated action, and the S2's
#: DRAM segment is full (firmware/castle.yaml, the dram0 diet). A 4-minute
#: track carries 1,200+ pulse hits = 2,400 actions = 32 KB — v5.25 did not
#: boot. Each scene keeps its strongest PULSE_CAP pulse hits, in time order;
#: hand-written cues are never thinned. BOTH generators apply it (device and
#: preview), so the desk shows exactly what the porch will do. Raising this
#: costs device RAM; the lasting fix is a cue table in flash (see
#: firmware/pending/README.md).
PULSE_CAP = 200


def thin_pulses(cues: list[dict], cap: int = PULSE_CAP) -> list[dict]:
    """The `cap` strongest pulse cues (intensity, then earlier), time-ordered."""
    if len(cues) <= cap:
        return cues
    ranked = sorted(enumerate(cues),
                    key=lambda ic: (-float(ic[1].get("intensity", 0.0)),
                                    ic[1]["t"], ic[0]))
    keep = sorted(ranked[:cap], key=lambda ic: (ic[1]["t"], ic[0]))
    return [c for _, c in keep]

