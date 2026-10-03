"""The desk's scene builder for a song nobody has edited — in Python.

`sceneYaml` (web/src/track_scene.ts) decides what an imported song's show
looks like: the pulse streams each band strikes with, and the section looks
(`track_sections.ts`) the castle holds between hits. tools/render_cues.py and
Castle Radio's import ask for exactly that scene with no desk open — no band
editor, no style knobs, every flavour off — and until 2026-10-03 they got it
by bundling the TypeScript with esbuild and running it under node. A buyer
has neither, so every song a buyer imported failed at "Generating light
show" (tests/install_smoke.py found it on its first run).

This is that headless case and nothing more, written to produce the SAME
TEXT — byte for byte, number formatting included — and
web/test/scene_parity.ts holds it there: seeded envelopes and band counts
through the real `sceneYaml` and through `scene_yaml` below, compared as
strings. The desk stays the source of truth; change a look there and the
parity suite says what to change here.

    scene_yaml(id, duration_s, counts, ext, env) -> the scenes.yaml block
    scene(id, wave, ext)                          -> that block, parsed
    track_scene.py --check < cases.json           -> the parity suite's face
"""

from __future__ import annotations

import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any

import yaml

Env = Sequence[Sequence[float]]

#: Playback level for a generated scene (track_scene.ts VOLUME).
VOLUME = 0.7

#: bands.ts BANDS: name, the Hz range the comment names, in emit order.
BANDS = (("onset_low", 20, 200), ("onset_mid", 200, 2000), ("onset_high", 2000, 16000))

#: band_style.ts BAND_STYLE, as `pulseLine` spells each field. Flavours are
#: off and no knob is turned, so styleFor(band, true) is this table.
BAND_STYLE: dict[str, dict[str, Any]] = {
    "onset_low": {
        "zones": ["door"], "alternate": False, "intensity": 0.85, "decay": 0.90,
        "ms": 140,
        "colors": [[1.0, 0.04, 0.0, 0.0], [1.0, 0.18, 0.0, 0.0], [0.85, 0.0, 0.25, 0.0]],
        "color_hot": [1.0, 0.45, 0.02, 0.10],
        "pixels_by_vel": True, "boost_at": 0.85, "boost_targets": ["towerL", "towerR"],
    },
    "onset_mid": {
        "zones": ["towerL", "towerR"], "alternate": True, "intensity": 0.80,
        "decay": 0.945, "ms": 160,
        "colors": [[0.55, 0.0, 1.0, 0.0], [1.0, 0.0, 0.75, 0.0], [0.25, 0.12, 1.0, 0.0]],
        "color_hot": [1.0, 0.15, 0.85, 0.12],
        "pixels_by_vel": True, "attack_ms": 90,
    },
    "onset_high": {
        "zones": ["towerR", "door", "towerL"], "alternate": True, "intensity": 0.72,
        "decay": 0.95, "ms": 130,
        "colors": [[0.05, 1.0, 0.35, 0.0], [0.0, 0.85, 1.0, 0.0], [0.65, 1.0, 0.0, 0.0]],
        "color_hot": [0.25, 1.0, 0.70, 0.15],
        "pixels": "scatter",
    },
}  # fmt: skip

#: track_sections.ts TIERS (quiet, mid, loud) and SILENCE_LOOK, as
#: (towers effect, towers level, door effect, door level).
TIERS = (("chill", 0.40, "ember", 0.50), ("seance", 0.70, "ember", 0.80),
         ("mansion", 0.95, "furnace", 0.95))  # fmt: skip
SILENCE_TIER = 3
SILENCE_LOOK = ("chill", 0.06, "ember", 0.08)
NOTES = ("hush", "verse", "chorus", "silence")
SILENCE_LEVEL = 0.04
SILENCE_MIN_SEC = 2.0
DEBOUNCE_SEC = 1.0
HYST = 0.08
FLAT_RANGE = 0.15
STEP = 0.25


def js_round(x: float) -> int:
    """Math.round: the nearest integer, a tie toward +infinity (Python's
    round() sends a tie to the even one)."""
    whole = math.floor(x)
    return whole + 1 if x - whole >= 0.5 else whole


def js_str(x: float) -> str:
    """String(x) for the numbers a scene holds: a whole number without the
    `.0` (and -0 as 0), else the shortest round-trip digits, which Python's
    repr and JavaScript agree on everywhere a 3-decimal value can land."""
    if x == math.floor(x) and abs(x) < 1e21:
        return str(int(x))
    return repr(float(x))


def num(v: float) -> str:
    """track_scene.ts `num`: three decimals, written as JavaScript would."""
    return js_str(js_round(v * 1000) / 1000)


def _rgbw(c: Sequence[float]) -> str:
    return f"[{', '.join(num(v) for v in c)}]"


def env_at(env: Env, sec: float) -> float:
    """track_sections.ts `envAt`: the nearest point within half a second,
    the earlier one on a tie, else 0 (near-silence is not in the envelope)."""
    lo, hi = 0, len(env)
    while lo < hi:
        m = (lo + hi) >> 1
        if env[m][0] < sec:
            lo = m + 1
        else:
            hi = m
    best, best_dt = 0.0, 0.5
    for j in (lo - 1, lo):
        if 0 <= j < len(env):
            dt = abs(env[j][0] - sec)
            if dt < best_dt:
                best_dt, best = dt, env[j][1]
    return best


def _desired(v: float, cur: int, up: Sequence[float]) -> int:
    if cur < 0:
        if v >= up[1]:
            return 2
        return 1 if v >= up[0] else 0
    nxt = cur
    if v >= up[1]:
        nxt = 2
    elif v >= up[0]:
        nxt = max(nxt, 1)
    if v < up[0] - HYST:
        nxt = 0
    elif v < up[1] - HYST:
        nxt = min(nxt, 1)
    return nxt


def _clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, v))


def sections(env: Env, start: float, end: float) -> list[tuple[float, int]]:
    """track_sections.ts `sections`: [seconds, tier] boundaries, first at 0."""
    if not env:
        return [(0, 1)]
    smoothed = []
    sec = start
    while sec <= end:
        smoothed.append(
            (env_at(env, sec - 0.5) + env_at(env, sec) + env_at(env, sec + 0.5)) / 3
        )
        sec += STEP
    ordered = sorted(smoothed)

    def pct(q: float) -> float:
        return ordered[math.floor(q * (len(ordered) - 1))]

    if pct(0.9) - pct(0.1) < FLAT_RANGE:
        return [(0, 1)]
    up0 = _clamp(pct(0.35), 0.25, 0.55)
    up = (up0, _clamp(pct(0.70), up0 + 0.15, 0.80))
    out: list[tuple[float, int]] = []
    tier, pending, pending_for = -1, -1, 0.0
    for i, v in enumerate(smoothed):
        sec = start + i * STEP
        want = _desired(v, tier, up)
        if tier < 0:
            tier = want
            out.append((0, tier))
            continue
        if want == tier:
            pending, pending_for = -1, 0.0
            continue
        if want == pending:
            pending_for += STEP
            if pending_for >= DEBOUNCE_SEC:
                tier = want
                out.append((sec - start, tier))
                pending, pending_for = -1, 0.0
        else:
            pending, pending_for = want, 0.0
    return _silence(out or [(0, 1)], env, start, end)


def _silence(
    segs: list[tuple[float, int]], env: Env, start: float, end: float
) -> list[tuple[float, int]]:
    """track_sections.ts `overlaySilence`, on the raw envelope."""
    n = max(1, math.floor((end - start) / STEP) + 1)
    need = js_round(SILENCE_MIN_SEC / STEP)
    in_run = [False] * n
    found = False
    i = 0
    while i < n:
        if env_at(env, start + i * STEP) >= SILENCE_LEVEL:
            i += 1
            continue
        j = i
        while j < n and env_at(env, start + j * STEP) < SILENCE_LEVEL:
            j += 1
        if j - i >= need:
            in_run[i:j] = [True] * (j - i)
            found = True
        i = j
    if not found:
        return segs

    def tier_at(rel: float) -> int:
        t = segs[0][1]
        for s, tier in segs:
            if s <= rel + 1e-9:
                t = tier
            else:
                break
        return t

    merged: list[tuple[float, int]] = []
    for k in range(n):
        rel = k * STEP
        tier = SILENCE_TIER if in_run[k] else tier_at(rel)
        if not merged or merged[-1][1] != tier:
            merged.append((rel, tier))
    return merged


def _set_lines(env: Env, end: float) -> list[str]:
    """track_sections.ts `sectionCues`, written as `setLine` writes it."""
    segs = sections(env, 0, end)
    out: list[str] = []

    def trio(t: int, tier: int, note: str, dim: float = 1) -> None:
        look = SILENCE_LOOK if tier == SILENCE_TIER else TIERS[tier]
        for zone, eff, level in (
            ("towerL", look[0], look[1]),
            ("towerR", look[0], look[1]),
            ("door", look[2], look[3]),
        ):
            lv = js_round(level * dim * 1000) / 1000
            out.append(
                f"      - {{t: {t}, op: set, zone: {zone}, effect: {eff}, "
                f"level: {num(lv)}, note: {note}}}"
            )

    for i, (sec, tier) in enumerate(segs):
        prev = segs[i - 1] if i else None
        if (
            tier == 2
            and prev is not None
            and prev[1] not in (2, SILENCE_TIER)
            and sec - prev[0] >= 0.7
        ):
            trio(js_round((sec - 0.45) * 1000), prev[1], "predim", 0.45)
        trio(js_round(sec * 1000), tier, NOTES[tier])
    return out


def _pulse_line(name: str, lo: int, hi: int, n: int) -> str:
    """track_scene.ts `pulseLine` with no band editor and every flavour off."""
    s = BAND_STYLE[name]
    opts = [f"synth: {name}", f"zones: [{', '.join(s['zones'])}]"]
    if s["alternate"]:
        opts.append("alternate: true")
    opts += [f"intensity: {num(s['intensity'])}", f"decay: {num(s['decay'])}",
             f"ms: {s['ms']}"]  # fmt: skip
    if s.get("attack_ms"):
        opts.append(f"attack_ms: {s['attack_ms']}")
    opts.append(f"colors: [{', '.join(_rgbw(c) for c in s['colors'])}]")
    opts.append(f"color_hot: {_rgbw(s['color_hot'])}")
    if s.get("pixels_by_vel"):
        opts.append("pixels_by_vel: true")
    if s.get("pixels"):
        opts.append(f"pixels: {s['pixels']}")
    if "boost_at" in s:
        opts.append(f"boost_at: {num(s['boost_at'])}")
        opts.append(f"boost_targets: [{', '.join(s['boost_targets'])}]")
    return f"      - {{{', '.join(opts)}}}   # {n} onsets in {lo}-{hi}Hz"


def _title(tid: str) -> str:
    """`id.replace(/_/g, " ").replace(/\\b\\w/g, upper)` — ASCII word
    characters only, as a JavaScript regex without the u flag sees them."""
    spaced = tid.replace("_", " ")
    return re.sub(r"\b\w", lambda m: m.group().upper(), spaced, flags=re.ASCII)


def scene_yaml(
    tid: str,
    dur: float,
    counts: Mapping[str, int],
    ext: str = "mp3",
    env: Env | None = None,
) -> str:
    """The block `sceneYaml(tid, dur, counts, ext, undefined, env)` writes."""
    quiet = TIERS[0]
    lines = [
        f"  - id: {tid}",
        f"    name: {_title(tid)}",
        "    kind: custom",
        f"    volume: {js_str(VOLUME)}",
        f"    duration_ms: {js_round(dur * 1000)}",
        "    loop: true",
        "    blurb: >",
        "      Imported track. Light cues are onset-detected from the audio",
        "      itself, so they follow whatever the track actually does.",
        f"    audio_file: tracks/{tid}.{ext}",
        f"    base: {{towerL: {quiet[0]}, towerR: {quiet[0]}, door: {quiet[2]}}}",
        (
            f"    levels: {{towerL: {js_str(quiet[1])}, "
            f"towerR: {js_str(quiet[1])}, door: {js_str(quiet[3])}}}"
        ),
        "    zones:",
        "      towerL: {center: ember, palette: haunt}",
        "      towerR: {center: ember, palette: moonlight, phase: 1.3}",
        "      door: {overlay: sparkle, palette: ember}",
        "    pulse:",
    ]
    for name, lo, hi in BANDS:
        if counts.get(name):
            lines.append(_pulse_line(name, lo, hi, counts[name]))
    sets = _set_lines(env, dur) if env and dur else []
    lines += ["    cues:", *sets] if sets else ["    cues: []"]
    return "\n".join(lines)


def scene(tid: str, wave: Mapping[str, Any], ext: str = "mp3") -> dict[str, Any]:
    """The scene the desk would splice for a song with this waveform answer
    (analyze_track's `"waveform": true`: duration, onsets, env)."""
    counts = {band: len(hits) for band, hits in (wave.get("onsets") or {}).items()}
    text = scene_yaml(tid, float(wave["duration"]), counts, ext, wave.get("env"))
    block: dict[str, Any] = yaml.safe_load(text)[0]
    return block


def main(argv: Sequence[str]) -> int:
    """`--check`: {"cases": [{id, dur, counts, ext, env}]} on stdin, the
    texts on stdout as {"yaml": [...]} — what web/test/scene_parity.ts
    compares with the desk's own."""
    if list(argv) != ["--check"]:
        print("usage: track_scene.py --check < cases.json", file=sys.stderr)
        return 2
    cases = json.loads(sys.stdin.buffer.read().decode("utf-8"))["cases"]
    texts = [
        scene_yaml(c["id"], c["dur"], c["counts"], c.get("ext", "mp3"), c.get("env"))
        for c in cases
    ]
    sys.stdout.write(json.dumps({"yaml": texts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
