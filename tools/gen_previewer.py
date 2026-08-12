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
import subprocess
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "scenes" / "scenes.yaml"
MARKERS_FILE = ROOT / "audio" / "markers.json"
TEMPLATE = ROOT / "previewer" / "template.html"
HTML = ROOT / "previewer" / "castle-cue-desk.html"
AUDIO = ROOT / "audio"
WEB = ROOT / "web"
BUNDLE = WEB / "dist" / "bundle.js"
STYLES = ROOT / "previewer" / "styles.css"

START = "// @GEN-DATA-START"
END = "// @GEN-DATA-END"
BUNDLE_MARK = "/* @BUNDLE"
STYLE_MARK = "/* @STYLES"

# Must match firmware/castle_effects.h and tools/gen_esphome.py.
KNOWN_EFFECTS = {
    "off", "candle", "ember", "furnace", "spirit", "eyes",
    "seance", "wisp", "mansion", "chill", "throb", "strobe", "blood",
}


def scene_yaml_slice(text: str, sid: str) -> str:
    """The scene's verbatim block from scenes.yaml, for the source panel."""
    # (?:.*\n)*? — `.*` not `.+`, so blank lines inside a block don't end it.
    m = re.search(rf"^  - id: {sid}\n(?:.*\n)*?(?=^  - id: |\Z)", text, re.M)
    return m.group(0).rstrip() if m else f"# (slice for {sid} not found)"


def to_previewer(scene: dict, idx: int, raw: str, markers: dict) -> dict:
    sid = scene["id"]
    cues = []
    for ev in scene.get("score") or []:
        cues.append({
            "t": int(ev["t"] * 1000),
            "bus": "AUD",
            "op": "play_loop" if scene.get("loop") and ev["synth"] == "wind" else "play",
            "snd": ev["synth"],
        })
    for cue in scene.get("cues") or []:
        if cue["op"] == "set":
            if cue["effect"] not in KNOWN_EFFECTS:
                sys.exit(f"scene {sid}: unknown effect {cue['effect']!r}")
            c = {"t": cue["t"], "bus": "LED", "op": "set",
                 "zone": cue["zone"], "eff": cue["effect"],
                 "detail": cue.get("note", "")}
            if "level" in cue:
                c["level"] = float(cue["level"])
            cues.append(c)
        elif cue["op"] == "strike":
            c = {"t": cue["t"], "bus": "LED", "op": "strike",
                 "ms": cue.get("ms", 80), "detail": cue.get("note", "")}
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
            cues.append(c)
        else:
            sys.exit(f"scene {sid}: unknown cue op {cue['op']!r}")
    # Pulse streams: one per synth, colour/decay per stream, velocity per
    # marker. Same merge as tools/gen_esphome.py — keep them in lockstep.
    scene_marks = markers.get(sid, {})
    for pcfg in scene.get("pulse") or []:
        beats = scene_marks.get(pcfg["synth"], [])
        zones = pcfg.get("zones") or ([pcfg["zone"]] if pcfg.get("zone") else None)
        for i, (t, vel) in enumerate(beats):
            c = {"t": t, "bus": "LED", "op": "strike", "ms": 120,
                 "intensity": round(pcfg.get("intensity", 0.3) * vel, 3),
                 "color": pcfg.get("color", [1, 1, 1, 1]),
                 "decay": pcfg.get("decay", 0.90),
                 "detail": pcfg["synth"]}
            if zones and pcfg.get("alternate"):
                c["targets"] = [zones[i % len(zones)]]
            elif zones:
                c["targets"] = zones
            cues.append(c)
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
        "cues": cues,
        "file": f"{idx:02d}_{sid}.mp3",
        # What this scene costs in flash. The budget is the single hardest
        # constraint on the show — ~2.9 MB for everything — so the number
        # belongs next to the scene, not only in the render log.
        "bytes": (AUDIO / f"{idx:02d}_{sid}.mp3").stat().st_size
                 if (AUDIO / f"{idx:02d}_{sid}.mp3").exists() else 0,
        "yaml": scene_yaml_slice(raw, sid),
    }


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
        ["npx", "esbuild", "src/main.ts", "--bundle", "--format=iife",
         "--target=es2020", "--outfile=dist/bundle.js"],
        cwd=WEB, capture_output=True, text=True,
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
    return html[:i] + STYLES.read_text().rstrip() + html[j:]


def main() -> int:
    raw = SRC.read_text()
    doc = yaml.safe_load(raw)
    import json
    markers = json.loads(MARKERS_FILE.read_text()) if MARKERS_FILE.exists() else {}
    scenes = [to_previewer(s, i, raw, markers)
              for i, s in enumerate(doc["scenes"], start=1)]

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
        print(f"note: no rendered audio for {missing} — run `make audio` first;"
              " previewer will fall back to live synth for those scenes")

    block = (
        f"{START} (written by tools/gen_previewer.py — do not edit, do not format)\n"
        f"  window.CASTLE_GEN = {json.dumps({'scenes': scenes, 'audio': audio})};\n"
        f"  {END}"
    )

    html = TEMPLATE.read_text()
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if not pattern.search(html):
        sys.exit(f"markers not found in {TEMPLATE} — expected {START} ... {END}")
    # sub() treats backslashes in the replacement as escapes, and the audio is
    # base64 so it will contain them eventually. Pass a function instead.
    html = pattern.sub(lambda _: block, html)

    html = inject_styles(html)
    html = inject_bundle(html)
    HTML.write_text(html)

    kb = sum(len(v) for v in audio.values()) // 1024
    print(f"wrote {len(scenes)} scenes + {len(audio)} audio files (~{kb} KB base64) "
          f"into {HTML.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
