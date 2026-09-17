#!/usr/bin/env python3
"""Render a song's light show to a card cue file — any song, no browser.

    tools/render_cues.py <track-id> [<track-id> ...]     # or --all

Until this existed a song got the full show only by becoming one of the
twelve scenes in scenes.yaml (a splice in the desk, a firmware build, an
OTA), and everything else on the card played under four solid colours a
second or under nothing. A cue file (tools/cue_file.py) is the same show
without the script: the firmware loads `/sd/<track>.cue`, beside the song,
when the track is played. `sd_sync.py cues` puts it there.

Three steps, each one somebody else's code on purpose:

  1. castle-core's analyze_track answers the studio's own waveform — onsets
     per band and the loudness envelope;
  2. the scene block is the one in scenes.yaml when the song is already a
     scene (your edits are the show), and otherwise the desk's own
     `sceneYaml`, bundled for node from web/src/scene_cli.ts;
  3. pulse_expand turns it into strikes, EVERY one of them — PULSE_CAP is a
     fact about script RAM, and a file in PSRAM does not pay it.

Output goes to `audio/card/cues/`, beside the card audio `sd_sync` pushes.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import build_paths as bp
import core_bins
import cue_file
import yaml
from pulse_expand import pulse_cues
from scene_schema import load_show
from scene_schema import validate as validate_scene
from track_lib import AUDIO_EXT, TRACKS

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
OUT = bp.AUDIO / "card" / "cues"
#: The studio's default when a scene does not carry its own.
SENSITIVITY = 1.1


def track_file(tid: str) -> Path:
    for ext in AUDIO_EXT:
        path = TRACKS / f"{tid}.{ext}"
        if path.exists():
            return path
    raise SystemExit(f"no track {tid!r} in {TRACKS}")


def waveform(path: Path, sensitivity: Any) -> dict[str, Any]:
    req = {"path": str(path), "sensitivity": sensitivity, "waveform": True}
    run = subprocess.run(
        [str(core_bins.core_bin("analyze_track"))],
        input=json.dumps(req).encode(),
        capture_output=True,
        check=False,
    )
    if run.returncode != 0:
        raise SystemExit(run.stderr.decode().strip() or f"cannot analyse {path.name}")
    wave: dict[str, Any] = json.loads(run.stdout)
    return wave


def _esbuild() -> Path:
    local = WEB / "node_modules" / ".bin" / "esbuild"
    found = local if local.exists() else shutil.which("esbuild")
    if not found or not shutil.which("node"):
        raise SystemExit(
            "a song that is not a scene yet gets its show from the desk's own "
            "builder, which needs node and esbuild: cd web && npm ci"
        )
    return Path(found)


def desk_scene(tid: str, wave: dict[str, Any], ext: str) -> dict[str, Any]:
    """The scene the desk would splice for this track (web/src/scene_cli.ts)."""
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "scene_cli.mjs"
        wave_json = Path(tmp) / "wave.json"
        wave_json.write_text(json.dumps(wave))
        subprocess.run(
            [str(_esbuild()), str(WEB / "src" / "scene_cli.ts"), "--bundle",
             "--platform=node", "--format=esm", "--log-level=warning",
             f"--outfile={bundle}"],
            check=True,
        )  # fmt: skip
        run = subprocess.run(
            ["node", str(bundle), tid, str(wave_json), ext],
            capture_output=True, text=True, check=True,
        )  # fmt: skip
    scene: dict[str, Any] = yaml.safe_load(run.stdout)[0]
    return scene


def markers_ms(wave: dict[str, Any], scene: dict[str, Any]) -> dict[str, list]:
    """The studio's onsets as render_audio.py writes them to markers.json:
    whole milliseconds, nothing inside the last 100 ms of the song."""
    dur = float(wave["duration"])
    at = float(scene.get("track_at", 0.0))
    return {
        band: [[int((h[0] + at) * 1000), *h[1:]] for h in hits if h[0] < dur - 0.1]
        for band, hits in wave["onsets"].items()
    }


def render(tid: str, doc: Mapping[str, Any]) -> tuple[Path, int, int]:
    """Write <tid>.cue; answers (path, cue count, of which pulses)."""
    path = track_file(tid)
    zone_ids = [z["id"] for z in doc["zones"]]
    scene = next((s for s in doc["scenes"] if s["id"] == tid), None)
    wave = waveform(path, (scene or {}).get("sensitivity", SENSITIVITY))
    if not wave.get("onsets"):
        raise SystemExit(f"{tid}: the analyser found nothing to light")
    if scene is None:
        scene = desk_scene(tid, wave, path.suffix[1:])
    problems = validate_scene(scene, zone_ids)
    if problems:
        raise SystemExit(f"scene {tid}: " + "\n  ".join(problems))
    pulses = pulse_cues(scene, {tid: markers_ms(wave, scene)})
    blob = cue_file.encode(scene, (scene.get("cues") or []) + pulses, zone_ids)
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{tid}.cue"
    out.write_bytes(blob)
    return out, len(scene.get("cues") or []) + len(pulses), len(pulses)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "tracks", nargs="*", help="track ids (file names without extension)"
    )
    ap.add_argument("--all", action="store_true", help="every track in the library")
    args = ap.parse_args()
    ids = list(args.tracks)
    if args.all:
        ids = sorted({p.stem for e in AUDIO_EXT for p in TRACKS.glob(f"*.{e}")})
    if not ids:
        ap.error("name a track, or --all")
    doc = load_show(bp.SCENES)
    for tid in ids:
        out, total, pulses = render(tid, doc)
        print(f"{tid}: {total} cues ({pulses} pulses, none thinned) -> {bp.rel(out)}"
              f"  {out.stat().st_size // 1024 + 1} KB")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
