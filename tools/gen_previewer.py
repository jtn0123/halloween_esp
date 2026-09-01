#!/usr/bin/env python3
"""Inject generated scene data (and the rendered audio) into the previewer.

The previewer began life with a hand-coded scene list. Now that scenes.yaml is
the source of truth, this script splices a generated data block into the HTML
between two markers, so the browser cue desk and the firmware can no longer
drift apart:

    // @GEN-DATA-START ... // @GEN-DATA-END

The block carries:
  - every scene: cues, score, base effects, volume, blurb — previewer-shaped
  - the per-scene slice of scenes.yaml, verbatim, for the source panel
  - the rendered audio/NN_<id>.mp3 files as data URIs, so the previewer can
    play EXACTLY what will ship in flash, not just its live approximation

Run after `make audio`:  tools/gen_previewer.py
"""

from __future__ import annotations

import base64
import json
import math
import re

# `as` marks the explicit seam: the build tests swap gen_previewer.subprocess
# for a double, so the binding is part of this module's surface.
import subprocess as subprocess
import sys
from pathlib import Path

# The pulse dynamics helpers (tempo, accent, pan threshold) are shared with
# the firmware generator rather than duplicated a third time — one pair of
# implementations (Python here, TS in track_lights.ts) is a parity burden;
# three was how the last drift happened.
from typing import Any

import build_paths as bp
import pulse_dynamics as pd
import scene_schema
import yaml
from effect_vocab import KNOWN_EFFECTS as KNOWN_EFFECTS  # one vocabulary, re-exported

ROOT = Path(__file__).resolve().parent.parent
# What is read from the show and what is written follow build_paths.py, so
# a sandboxed studio's rebuild lands beside its own scenes file. The
# template, styles and bundle are the repo's — they are inputs, not show.
SRC = bp.SCENES
MARKERS_FILE = bp.AUDIO / "markers.json"
TEMPLATE = ROOT / "previewer" / "template.html"
HTML = bp.PREVIEW_HTML
AUDIO = bp.AUDIO
WEB = ROOT / "web"
BUNDLE = WEB / "dist" / "bundle.js"
STYLES = ROOT / "previewer" / "styles.css"
# The overflow room: styles.css sits at the 500-line cap, and pushing new
# rules into cssText strings was costing panels their theming.
PANELS = ROOT / "previewer" / "panels.css"
MOBILE = ROOT / "previewer" / "mobile.css"

START = "// @GEN-DATA-START"
END = "// @GEN-DATA-END"
BUNDLE_MARK = "/* @BUNDLE"
STYLE_MARK = "/* @STYLES"

# Must match firmware/castle_effects.h and tools/gen_esphome.py.


def scene_yaml_slice(text: str, sid: str) -> str:
    """The scene's verbatim block from scenes.yaml, for the source panel."""
    # (?:.*\n)*? — `.*` not `.+`, so blank lines inside a block don't end it.
    m = re.search(rf"^  - id: {sid}\n(?:.*\n)*?(?=^  - id: |\Z)", text, re.MULTILINE)
    return m.group(0).rstrip() if m else f"# (slice for {sid} not found)"


def _blend_color(base: list[float], hot: list[float] | None, vel: float) -> list[float]:
    """color -> color_hot by velocity — same maths as gen_esphome.blend_color
    and track_lights.ts."""
    if not hot:
        return base
    return [pd.round3(b + (h - b) * vel) for b, h in zip(base, hot)]


def to_previewer(
    scene: dict[str, Any], idx: int, raw: str, markers: dict[str, Any]
) -> dict[str, Any]:
    sid = scene["id"]
    cues = [
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
    for cue in scene.get("cues") or []:
        if cue["op"] == "set":
            if cue["effect"] not in KNOWN_EFFECTS:
                sys.exit(f"scene {sid}: unknown effect {cue['effect']!r}")
            c = {
                "t": cue["t"],
                "bus": "LED",
                "op": "set",
                "zone": cue["zone"],
                "eff": cue["effect"],
                "detail": cue.get("note", ""),
            }
            if "level" in cue:
                c["level"] = float(cue["level"])
            cues.append(c)
        elif cue["op"] == "strike":
            c = {
                "t": cue["t"],
                "bus": "LED",
                "op": "strike",
                "ms": cue.get("ms", 80),
                "detail": cue.get("note", ""),
            }
            # Carry every field gen_esphome.py honours on a hand-written
            # strike. It has always read targets/intensity/color/decay; this
            # side used to copy only zone, so a cue aimed at one zone flashed
            # the whole chain in the browser, in default white, at full
            # intensity. Latent — today's scenes only set those inside
            # `pulse:` — but a divergence between preview and device is the
            # one bug this project cannot afford, latent or not.
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
            cues.append(c)
        else:
            sys.exit(f"scene {sid}: unknown cue op {cue['op']!r}")
    # Pulse streams: one per synth, colour/decay per stream, velocity per
    # marker. Same merge as tools/gen_esphome.py (which documents the
    # per-hit dynamics: color_hot, pixels_by_vel, boost_at/boost_targets,
    # ms) and web/src/track_lights.ts — keep all three in lockstep.
    scene_marks = markers.get(sid, {})
    gates = pd.section_gates(scene)
    pulses: list[dict[str, Any]] = []  # capped with pd.thin_pulses below
    for pcfg in scene.get("pulse") or []:
        beats = scene_marks.get(pcfg["synth"], [])
        zones = pcfg.get("zones") or ([pcfg["zone"]] if pcfg.get("zone") else None)
        factor = pd.tempo_factor([b[0] / 1000.0 for b in beats])
        decay = pd.tempo_decay(pcfg.get("decay", 0.90), factor)
        ms = math.floor(int(pcfg.get("ms", 120)) * factor + 0.5)
        vels = [b[1] for b in beats]
        for i, beat in enumerate(beats):
            t, vel = beat[0], beat[1]
            pan = beat[2] if len(beat) > 2 else None
            mul = pd.gate_mul(pcfg["synth"], gates, t)
            if mul is None:
                continue  # gated out by its section (#9)
            cyc = pcfg.get("colors")
            hot = pcfg.get("color_hot")
            if pcfg.get("takeover") and pd.gate_note(gates, t) == "chorus":
                base = pd.TAKEOVER_COLORS[i % len(pd.TAKEOVER_COLORS)]
                hot = pd.TAKEOVER_HOT
            elif cyc and pcfg.get("drift"):
                base = pd.drift_base(cyc, i, t)
            else:
                base = cyc[i % len(cyc)] if cyc else pcfg.get("color", [1, 1, 1, 1])
            c = {
                "t": t,
                "bus": "LED",
                "op": "strike",
                "ms": ms,
                "intensity": pd.round3(pcfg.get("intensity", 0.3) * vel * mul),
                "color": _blend_color(base, hot, vel),
                "decay": decay,
                "detail": pcfg["synth"],
            }
            if pcfg.get("attack_ms"):
                c["attack"] = int(pcfg["attack_ms"])
            if pcfg.get("pixels_by_vel"):
                c["pixels"] = (
                    "center" if vel < 0.40 else "scatter" if vel < 0.72 else "all"
                )
            elif pcfg.get("pixels"):
                c["pixels"] = pcfg["pixels"]
            if zones and pcfg.get("alternate"):
                if (
                    pan is not None
                    and abs(pan) >= pd.PAN_DECISIVE
                    and "towerL" in zones
                    and "towerR" in zones
                ):
                    c["targets"] = ["towerL" if pan < 0 else "towerR"]
                else:
                    c["targets"] = [zones[i % len(zones)]]
            elif zones:
                c["targets"] = list(zones)
            if (
                c.get("targets")
                and pcfg.get("boost_targets")
                and (vel >= pcfg.get("boost_at", 2) or pd.is_accent(vels, i))
            ):
                c["targets"] = c["targets"] + [
                    z for z in pcfg["boost_targets"] if z not in c["targets"]
                ]
            pulses.append(c)
    # Same cap as gen_esphome (PULSE_CAP): the desk must show the hits the
    # device will actually play, not the 1,200 its RAM cannot hold.
    cues.extend(pd.thin_pulses(pulses))
    cues.sort(key=lambda c: c["t"])

    for eff in scene["base"].values():
        if eff not in KNOWN_EFFECTS:
            sys.exit(f"scene {sid}: unknown base effect {eff!r}")

    return {
        "id": sid,
        "name": scene["name"],
        "kind": scene["kind"] + (" · loops" if scene.get("loop") else ""),
        "dur": scene["duration_ms"],
        "loop": bool(scene.get("loop")),
        "volume": float(scene.get("volume", 0.8)),
        "blurb": " ".join(str(scene.get("blurb", "")).split()),
        "base": scene["base"],
        "levels": scene.get("levels") or {},
        "zones": scene.get("zones") or {},
        "cues": cues,
        "file": f"{idx:02d}_{sid}.mp3",
        # What this scene costs in flash. The budget is the single hardest
        # constraint on the show — ~2.9 MB for everything — so the number
        # belongs next to the scene, not only in the render log.
        "bytes": (AUDIO / f"{idx:02d}_{sid}.mp3").stat().st_size
        if (AUDIO / f"{idx:02d}_{sid}.mp3").exists()
        else 0,
        "yaml": scene_yaml_slice(raw, sid),
    }


# ── The lean page the studio serves ──────────────────────────────────
# The committed build inlines the rendered audio as data URIs so the file is
# portable: open it from disk, publish it as an artifact, copy it to the
# card. Served by the studio that portability buys nothing and costs every
# phone on the LAN ~1.9 MB of base64 it may never play — so the studio serves
# the lean rewrite instead: the same page, each data URI replaced by a
# /studio/scene-audio/<id> link the studio answers with Range support from
# the audio/ directory the page was built from. Same bytes, fetched when
# played. The rewrite happens at serve time, cached by the page's (mtime,
# size), so `make preview` keeps one artefact and one source of truth.
AUDIO_ROUTE = "/studio/scene-audio/"
_DATA_URI = re.compile(r'"(\w+)": ?"data:audio/mpeg;base64,[A-Za-z0-9+/=]*"')
_lean_cache: dict[tuple[str, int, int], bytes] = {}


def lean(html: str, route: str = AUDIO_ROUTE, suffix: str = "") -> str:
    """The page with every inlined scene audio swapped for its URL.

    The studio serves `/studio/scene-audio/<id>` (the default); the device
    build gets `/site/<id>.mp3` — sd_sync pushes the files beside the page
    (grade report A5/G1), served by the firmware's existing /site/* handler.
    """
    return _DATA_URI.sub(lambda m: f'"{m[1]}": "{route}{m[1]}{suffix}"', html)


def lean_page(page: Path) -> tuple[bytes, str]:
    """(body, etag) of the lean rewrite of `page`, computed once per
    (mtime, size) — the rewrite is one pass over ~2.4 MB, not per request."""
    st = page.stat()
    key = (str(page), st.st_mtime_ns, st.st_size)
    if key not in _lean_cache:
        _lean_cache.clear()
        _lean_cache[key] = lean(page.read_text()).encode()
    return _lean_cache[key], f'"{st.st_mtime_ns}-{st.st_size}-lean"'


def scene_audio(audio_dir: Path, sid: str) -> Path | None:
    """The rendered file for scene `sid` in `audio_dir` (NN_<sid>.mp3), or
    None. The id is matched as a whole name: no separators, no traversal."""
    if not re.fullmatch(r"[A-Za-z0-9_]+", sid):
        return None
    return next(iter(sorted(audio_dir.glob(f"[0-9][0-9]_{sid}.mp3"))), None)


def inject_bundle(html: str) -> str:
    """Build web/src with esbuild and splice the result into the page.

    The output has to stay a single self-contained file with no external
    requests: the published artifact runs under a strict CSP, and the plan to
    serve a cut-down copy off the device rules out a CDN as well. So the
    bundle is inlined rather than referenced.
    """
    if not (WEB / "node_modules").exists():
        sys.exit("web/node_modules missing — run `cd web && npm install` first")

    r = subprocess.run(
        # --minify: the page is re-sent on every studio restart and every
        # phone load, and the unminified bundle was a quarter of it. Debug
        # against `npm run watch`'s dist/bundle.js, not the spliced page.
        [
            "npx",
            "esbuild",
            "src/main.ts",
            "--bundle",
            "--minify",
            "--format=iife",
            "--target=es2020",
            "--outfile=dist/bundle.js",
        ],
        cwd=WEB,
        capture_output=True,
        text=True,
        check=False,  # handled below
    )
    if r.returncode != 0:
        sys.exit(f"esbuild failed:\n{r.stdout}\n{r.stderr}")

    js = BUNDLE.read_text()
    i = html.find(BUNDLE_MARK)
    if i < 0:
        sys.exit(f"{BUNDLE_MARK} marker not found in {TEMPLATE}")
    j = html.index("*/", i) + 2
    # Nothing in the bundle may contain a literal </script>; esbuild will not
    # produce one from this source, but check rather than trust.
    if "</script>" in js:
        sys.exit("bundle contains a literal </script> — it would close the tag early")
    return html[:i] + js + html[j:]


def inject_styles(html: str) -> str:
    """Inline previewer/styles.css.

    Split out of the template purely so both files stay inside the 500-line
    cap — markup and styling are a real seam, and the check caught the
    combined file honestly rather than being exempted around.
    """
    i = html.find(STYLE_MARK)
    if i < 0:
        sys.exit(f"{STYLE_MARK} marker not found in {TEMPLATE}")
    j = html.index("*/", i) + 2
    css = (
        STYLES.read_text().rstrip()
        + "\n\n"
        + PANELS.read_text().rstrip()
        + "\n\n"
        + MOBILE.read_text().rstrip()
    )
    return html[:i] + css + html[j:]


#: Ceiling for the portable inlined build, in KB — a HARD limit: past it the
#: build fails and nothing is written (see enforce_budget below).
#:
#: History, because the number has moved and should not keep moving: 3 MB
#: held the nine-scene rig; scene 10 (the 3-minute Ballad) pushed the honest
#: size past it and the line was raised to 4 MB. Twice is a ratchet, and a
#: warning nobody can fail is what let it happen — hence the hard stop. The
#: page grows ~1.2 MB per song scene and is ~3.3 MB at ten scenes, so this
#: budget has room for one more and then the show must answer for it.
PAGE_BUDGET_KB = 4 * 1024


def enforce_budget(body: bytes, audio: dict[str, str]) -> str | None:
    """The complaint when the built page is over PAGE_BUDGET_KB, else None.

    Why fail rather than warn (grade report G2): the page grows with scenes
    times audio length and nothing bounds it, and the previous budget was a
    warning — so when it was crossed the constant moved instead of the page.
    A build that stops is the only version of this budget that is a budget.

    The complaint names the heaviest scenes, because "the page is too big" is
    not actionable and "the Ballad is 1.3 MB of it" is.
    """
    total_kb = len(body) // 1024
    if total_kb <= PAGE_BUDGET_KB:
        return None
    heavy = sorted(((len(v) // 1024, k) for k, v in audio.items()), reverse=True)[:3]
    worst = ", ".join(f"{sid} ({kb / 1024:.1f} MB)" for kb, sid in heavy)
    return (
        f"page budget FAILED — the portable build is {total_kb / 1024:.2f} MB, "
        f"over its {PAGE_BUDGET_KB // 1024} MB ceiling. Nothing was written; "
        f"{bp.rel(HTML)} still holds the last good build.\n"
        f"  heaviest inlined audio: {worst}\n"
        "  The weight is inlined mp3s, and they are inlined so the file plays "
        "when opened from disk with no server. Ways out, best first:\n"
        "   - shorten or re-render the heaviest scene (a song scene is "
        "~1.2 MB of base64 here);\n"
        "   - if the show genuinely needs the scenes, stop inlining past the "
        "budget and emit /studio/scene-audio/<id> for the rest — the lean "
        "rewrite already does exactly that, but a page opened from disk "
        "cannot fetch those, so the desk needs a per-scene fallback to the "
        "live synth first (web/src/audio.ts, main.ts: the rendered/synth "
        "choice is one global switch today);\n"
        "   - raising PAGE_BUDGET_KB is the third answer, not the first, and "
        "belongs in its own commit that says why."
    )


def main() -> int:
    raw = SRC.read_text()
    doc = yaml.safe_load(raw)
    markers = scene_schema.load_markers(MARKERS_FILE)
    scenes = [
        to_previewer(s, i, raw, markers) for i, s in enumerate(doc["scenes"], start=1)
    ]

    audio: dict[str, str] = {}
    missing = []
    for sc in scenes:
        mp3 = AUDIO / sc["file"]
        if mp3.exists():
            b64 = base64.b64encode(mp3.read_bytes()).decode()
            audio[sc["id"]] = f"data:audio/mpeg;base64,{b64}"
        else:
            missing.append(sc["file"])
    if missing:
        print(
            f"note: no rendered audio for {missing} — run `make audio` first;"
            " previewer will fall back to live synth for those scenes"
        )

    block = (
        f"{START} (written by tools/gen_previewer.py — do not edit, do not format)\n"
        f"  window.CASTLE_GEN = {json.dumps({'scenes': scenes, 'audio': audio})};\n"
        f"  {END}"
    )

    html = TEMPLATE.read_text()
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.DOTALL)
    if not pattern.search(html):
        sys.exit(f"markers not found in {TEMPLATE} — expected {START} ... {END}")
    # sub() treats backslashes in the replacement as escapes, and the audio is
    # base64 so it will contain them eventually. Pass a function instead.
    html = pattern.sub(lambda _: block, html)

    html = inject_styles(html)
    html = inject_bundle(html)

    # Weighed BEFORE it is written: an over-budget build leaves the last good
    # page in the tree rather than replacing it with the one that failed.
    body = html.encode()
    complaint = enforce_budget(body, audio)
    if complaint is not None:
        print(complaint, file=sys.stderr)
        return 1

    HTML.parent.mkdir(parents=True, exist_ok=True)
    HTML.write_bytes(body)

    kb = sum(len(v) for v in audio.values()) // 1024
    total_kb = len(body) // 1024
    print(
        f"wrote {len(scenes)} scenes + {len(audio)} audio files (~{kb} KB base64) "
        f"into {bp.rel(HTML)} ({total_kb / 1024:.1f} MB "
        f"of the {PAGE_BUDGET_KB // 1024} MB budget)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
