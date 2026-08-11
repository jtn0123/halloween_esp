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
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "scenes" / "scenes.yaml"
MARKERS_FILE = ROOT / "audio" / "markers.json"
HTML = ROOT / "previewer" / "castle-cue-desk.html"
AUDIO = ROOT / "audio"

START = "// @GEN-DATA-START"
END = "// @GEN-DATA-END"

# Must match firmware/castle_effects.h and tools/gen_esphome.py.
KNOWN_EFFECTS = {
    "off", "candle", "ember", "furnace", "spirit", "eyes",
    "seance", "wisp", "mansion", "chill", "throb", "strobe",
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
            cues.append({"t": cue["t"], "bus": "LED", "op": "set",
                         "zone": cue["zone"], "eff": cue["effect"],
                         "detail": cue.get("note", "")})
        elif cue["op"] == "strike":
            c = {"t": cue["t"], "bus": "LED", "op": "strike",
                 "ms": cue.get("ms", 80), "detail": cue.get("note", "")}
            if cue.get("zone"):
                c["zone"] = cue["zone"]
            cues.append(c)
        else:
            sys.exit(f"scene {sid}: unknown cue op {cue['op']!r}")
    pcfg = scene.get("pulse")
    for t in (markers.get(sid, []) if pcfg else []):
        c = {"t": t, "bus": "LED", "op": "strike", "ms": 120,
             "intensity": pcfg.get("intensity", 0.3), "detail": "beat"}
        if pcfg.get("zone"):
            c["zone"] = pcfg["zone"]
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
        "cues": cues,
        "file": f"{idx:02d}_{sid}.mp3",
        "yaml": scene_yaml_slice(raw, sid),
    }


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
        f"  const GEN = {json.dumps({'scenes': scenes, 'audio': audio})};\n"
        f"  {END}"
    )

    html = HTML.read_text()
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if not pattern.search(html):
        sys.exit(f"markers not found in {HTML} — expected {START} ... {END}")
    HTML.write_text(pattern.sub(lambda _: block, html))

    kb = sum(len(v) for v in audio.values()) // 1024
    print(f"wrote {len(scenes)} scenes + {len(audio)} audio files (~{kb} KB base64) "
          f"into {HTML.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
