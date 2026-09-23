"""One prepared timeline: rich desk choreography, card bytes, and exact preview.

Preparation is local. Only remote_library's explicit Sync uploads anything.
The preview is decoded FROM the card bytes, including their quantization.
"""

import json
import subprocess
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import cue_file
from effect_vocab import EFFECT_IDS, FLASH_MODE_IDS, OVERLAY_IDS, PALETTE_IDS
from pulse_expand import pulse_cues
from render_cues import desk_scene, markers_ms, waveform

ZONES = ["towerL", "towerR", "door"]


def preview_from_blob(key, blob):
    doc = cue_file.decode(blob)
    effects = {v: k for k, v in EFFECT_IDS.items()}
    modes = {v: k for k, v in FLASH_MODE_IDS.items()}
    overlays = {v: k for k, v in OVERLAY_IDS.items()}
    palettes = {v: k for k, v in PALETTE_IDS.items()}
    zones = {}
    for name, z in zip(ZONES, doc["zones"]):
        zones[name] = {
            "overlay": overlays[z["overlay"]],
            "palette": palettes[z["palette"]],
            "phase": z["phase"],
        }
        if z["center"] >= 0:
            zones[name]["center"] = effects[z["center"]]
    cues = []
    for r in doc["records"]:
        targets = [z for i, z in enumerate(ZONES) if r["mask"] & (1 << i)]
        if r["op"] == "set":
            for target in targets:
                c = {
                    "t": r["t"],
                    "bus": "LED",
                    "op": "set",
                    "zone": target,
                    "eff": effects[r["effect"]],
                }
                if r["level"] is not None:
                    c["level"] = r["level"]
                cues.append(c)
        else:
            cues.append(
                {
                    "t": r["t"],
                    "bus": "LED",
                    "op": "strike",
                    "targets": targets,
                    "intensity": r["intensity"],
                    "decay": r["decay"],
                    "attack": r["attack"],
                    "pixels": modes[r["mode"]],
                    "color": r["color"],
                    "ms": 120,
                }
            )
    return {
        "cue_crc32": f"{zlib.crc32(blob):08x}",
        "id": key,
        "name": key,
        "dur": doc["duration_ms"],
        "loop": False,
        "volume": 0.7,
        "blurb": "Prepared castle show",
        "file": "",
        "bytes": 0,
        "yaml": "",
        "base": {z: effects[b["effect"]] for z, b in zip(ZONES, doc["zones"])},
        "levels": {z: b["level"] for z, b in zip(ZONES, doc["zones"])},
        "zones": zones,
        "cues": cues,
    }


def build(key, wave, layers=None, ext="mp3"):
    scene = desk_scene(key, wave, ext)
    cues = list(scene.get("cues") or [])
    if layers:
        # Keep voice on the door and stereo backing on its respective tower.
        # Every band still gets the desk's color ramps, masks and tempo decay.
        for layer, channel, target in (
            ("vocals", "both", "door"),
            ("backing", "left", "towerL"),
            ("backing", "right", "towerR"),
        ):
            marks = layers[layer][channel]["onsets"]
            # A band present only in a stem must still receive a style recipe.
            recipe = desk_scene(key, {**wave, "onsets": marks}, ext)
            configs = []
            for original in recipe.get("pulse", []):
                cfg = {**original, "zones": [target], "alternate": False}
                cfg.pop("boost_at", None)
                cfg.pop("boost_targets", None)
                configs.append(cfg)
            routed = {**scene, "pulse": configs}
            cues.extend(
                pulse_cues(routed, {key: markers_ms({**wave, "onsets": marks}, routed)})
            )
    else:
        cues.extend(pulse_cues(scene, {key: markers_ms(wave, scene)}))
    blob = cue_file.encode(scene, cues, ZONES)
    return blob, preview_from_blob(key, blob)


def prepare(library, row):
    """Report tool failures to the job/API instead of terminating its thread."""
    try:
        return _prepare(library, row)
    except (SystemExit, subprocess.CalledProcessError) as exc:
        raise ValueError(f"Could not prepare the light show: {exc}") from exc


def _prepare(library, row):
    """Create local companion files; never copy, re-encode or play the audio."""
    name = Path(row.get("playback_file") or f"{row['key']}.mp3").name
    source = library / name
    if not source.is_file():
        raise ValueError("Audio is unavailable for show preparation")
    wave = waveform(source, 1.1)
    layers = None
    analysis = library / "stems" / row["key"] / "analysis.json"
    if row.get("split"):
        if not analysis.is_file():
            raise ValueError("Separated analysis is missing; reprocess the song first")
        layers = json.loads(analysis.read_text())["layers"]
    blob, preview = build(row["key"], wave, layers, source.suffix[1:])
    preview["name"] = row.get("title", row["key"])
    cue_path = source.with_suffix(".cue")
    show_path = source.with_suffix(".show.json")
    for path, data in ((cue_path, blob), (show_path, json.dumps(preview).encode())):
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_bytes(data)
        temp.replace(path)
    return metadata(library, row)


def metadata(library, row):
    name = Path(row.get("playback_file") or f"{row['key']}.mp3").name
    audio = library / name
    cue = audio.with_suffix(".cue")
    show = audio.with_suffix(".show.json")
    if not cue.is_file() or not show.is_file() or not audio.is_file():
        return None
    if min(cue.stat().st_mtime_ns, show.stat().st_mtime_ns) < audio.stat().st_mtime_ns:
        return None
    blob = cue.read_bytes()
    try:
        if json.loads(show.read_text()).get("cue_crc32") != f"{zlib.crc32(blob):08x}":
            return None
    except (ValueError, OSError):
        return None
    return {
        "url": f"/radio/audio/{show.name}",
        "filename": cue.name,
        "bytes": len(blob),
        "crc32": f"{zlib.crc32(blob):08x}",
        "records": len(cue_file.decode(blob)["records"]),
    }
