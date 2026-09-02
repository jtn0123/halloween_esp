"""Fuzz the generators with random scenes.yaml-shaped documents.

gen_esphome.py and gen_previewer.py each read the same scene and must not
crash, must write YAML that loads, and must describe the SAME show: same cue
times, same zones, every index inside the zone_* arrays, every delay
non-negative and summing back to the scene's length. The hand-written tests
pin the shapes the real scenes.yaml uses; this throws every combination the
format allows — odd rigs, empty zones, out-of-order cues, strikes with every
optional field, pulse streams with random markers — with a fixed seed, so a
red run is reproducible and the seed is in the failure message.
"""

from __future__ import annotations

import contextlib
import io
import json
import random
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gen_esphome as ge
import gen_previewer as gp
import gen_rig
import rig_layout as rl
import yaml

SEED = 20260820
CASES = 40
ZIDS = ["towerL", "towerR", "door"]
EFFECTS = list(ge.EFFECT_IDS)
OUTPUT_PATHS = (
    "SRC",
    "MARKERS",
    "OUT",
    "AUDIO_SD",
    "RIG_OUT",
    "LIGHTS_OUT",
)


class EsphomeLoader(yaml.SafeLoader):
    """SafeLoader that accepts ESPHome's tags (!lambda, !secret, !include)
    as opaque values — enough to prove the generated file parses."""


EsphomeLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def rand_zones(r: random.Random) -> list[dict[str, Any]]:
    zones = []
    pins = r.sample([14, 16, 18, 5, 6, 9, 10], 3)
    for i, zid in enumerate(ZIDS):
        z: dict[str, Any] = {"id": zid, "channel": i + 1, "pin": pins[i]}
        if r.random() < 0.85:
            fx = r.choice(list(rl.FIXTURES))
            z["fixture"] = fx
            if fx == "mini":
                z["pixels"] = r.randint(1, 5)
            z["rgbw"] = fx not in ("wing32", "mini") and r.random() < 0.7
        zones.append(z)
    return zones


def rand_cue(r: random.Random, dur: int) -> dict[str, Any]:
    t = r.choice([0, dur, r.randint(0, dur)])
    if r.random() < 0.5:
        c: dict[str, Any] = {
            "t": t,
            "op": "set",
            "zone": r.choice(ZIDS),
            "effect": r.choice(EFFECTS),
        }
        if r.random() < 0.5:
            c["level"] = round(r.random(), 3)
    else:
        c = {"t": t, "op": "strike", "ms": r.choice([40, 80, 900])}
        k = r.random()
        if k < 0.3:
            c["zone"] = r.choice(ZIDS)
        elif k < 0.6:
            c["targets"] = r.sample(ZIDS, r.randint(1, 3))
        if r.random() < 0.5:
            c["intensity"] = round(r.random(), 3)
        if r.random() < 0.5:
            c["color"] = [round(r.random(), 2) for _ in range(4)]
        if r.random() < 0.5:
            c["decay"] = r.choice([0.82, 0.9, 0.955, 0.99])
        if r.random() < 0.5:
            c["pixels"] = r.choice(list(ge.FLASH_MODE_IDS))
        if r.random() < 0.3:
            c["attack"] = r.choice([0, 16, 90, 400])
    if r.random() < 0.3:
        c["note"] = "fuzz note"
    return c


def rand_scene(r: random.Random, i: int) -> tuple[dict[str, Any], dict[str, Any]]:
    dur = r.choice([100, 1000, 8000, 30000])
    scene: dict[str, Any] = {
        "id": f"fz{i}",
        "name": f"Fuzz {i}",
        "kind": r.choice(["ambient", "triggered", "motion"]),
        "duration_ms": dur,
        "volume": round(r.random(), 2),
        "loop": r.random() < 0.5,
        "base": {z: r.choice(EFFECTS) for z in r.sample(ZIDS, r.randint(1, 3))},
        "cues": [rand_cue(r, dur) for _ in range(r.randint(0, 12))],
    }
    if r.random() < 0.6:
        scene["levels"] = {
            z: round(r.random(), 2) for z in r.sample(ZIDS, r.randint(1, 3))
        }
    if r.random() < 0.7:
        scene["zones"] = {}
        for z in r.sample(ZIDS, r.randint(1, 3)):
            d: dict[str, Any] = {}
            if r.random() < 0.5:
                d["center"] = r.choice(EFFECTS)
            if r.random() < 0.5:
                d["overlay"] = r.choice(list(ge.OVERLAY_IDS))
            if r.random() < 0.5:
                d["palette"] = r.choice(list(ge.PALETTE_IDS))
            if r.random() < 0.5:
                d["phase"] = round(r.random() * 3, 2)
            scene["zones"][z] = d
    markers: dict[str, Any] = {}
    if r.random() < 0.6:
        scene["pulse"] = []
        for s in range(r.randint(1, 3)):
            synth = f"syn{s}"
            p: dict[str, Any] = {
                "synth": synth,
                "intensity": round(r.random(), 2),
                "decay": r.choice([0.82, 0.9, 0.95]),
            }
            k = r.random()
            if k < 0.3:
                p["zone"] = r.choice(ZIDS)
            elif k < 0.7:
                p["zones"] = r.sample(ZIDS, r.randint(1, 3))
                p["alternate"] = r.random() < 0.5
            if r.random() < 0.5:
                p["color"] = [round(r.random(), 2) for _ in range(4)]
            if r.random() < 0.3:
                p["pixels"] = r.choice(list(ge.FLASH_MODE_IDS))
            scene["pulse"].append(p)
            markers[synth] = sorted(
                [
                    [r.randint(0, dur), round(r.uniform(0.05, 1.0), 3)]
                    for _ in range(r.randint(0, 10))
                ]
            )
    return scene, markers


def replay_times(lines: list[str]) -> list[int]:
    """Walk the emitted script the way ESPHome would; when does each cue land?"""
    times, t, started = [], 0, False
    for ln in lines:
        s = ln.strip()
        if s == "id: sfx":
            started = True
        elif started and s.startswith("- delay:"):
            dt = int(s.split()[-1].removesuffix("ms"))
            assert dt > 0, f"non-positive delay {dt}"
            t += dt
        elif started and s.startswith("- lambda:"):
            times.append(t)
    return times


class TestGeneratorFuzz(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._saved = {name: getattr(ge, name) for name in OUTPUT_PATHS}
        # The generator narrates ("wrote …", "note: …"); keep -q output clean.
        self.out = self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self._saved["ROOT"] = ge.ROOT
        ge.ROOT = self.tmp
        ge.SRC = self.tmp / "scenes.yaml"
        ge.MARKERS = self.tmp / "markers.json"
        for name in OUTPUT_PATHS:
            if name not in ("SRC", "MARKERS"):
                setattr(ge, name, self.tmp / "generated" / Path(getattr(ge, name)).name)

    def tearDown(self) -> None:
        for name, value in self._saved.items():
            setattr(ge, name, value)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_random_documents_generate_consistently(self) -> None:
        r = random.Random(SEED)
        for case in range(CASES):
            zones = rand_zones(r)
            scenes = [rand_scene(r, i) for i in range(r.randint(1, 4))]
            doc = {
                "hardware": {"pixels_per_zone": r.choice([1, 7])},
                "zones": zones,
                "show": {"gap_ms": r.randint(0, 20000)},
                "scenes": [s for s, _ in scenes],
            }
            markers = {s["id"]: m for s, m in scenes}
            with self.subTest(case=case, seed=SEED):
                self.check_case(doc, markers, zones)

    def check_case(
        self, doc: dict[str, Any], markers: dict[str, Any], zones: list[dict[str, Any]]
    ) -> None:
        nz = len(zones)
        for idx, scene in enumerate(doc["scenes"], start=1):
            lines = ge.emit_scene(scene, zones, idx, markers)
            script = yaml.safe_load("script:\n" + "\n".join(lines))["script"][0]
            self.assertEqual(script["id"], f"scene_{scene['id']}")
            text = "\n".join(lines)
            # Every zone_* index the lambdas touch is inside the arrays.
            for m in re.finditer(r"id\(zone_(\w+)\)\[(\d+)\]", text):
                name, i = m.group(1), int(m.group(2))
                self.assertLess(i, nz * 4 if name == "flash_col" else nz, m.group(0))
            # Replaying the deltas lands on the source times, in order, and
            # the script runs for exactly duration_ms.
            pulse = ge.pulse_cues(scene, markers)
            want = sorted(c["t"] for c in (scene.get("cues") or []) + pulse)
            self.assertEqual(replay_times(lines), want)
            total = sum(
                int(ln.strip().split()[-1].removesuffix("ms"))
                for ln in lines
                if ln.strip().startswith("- delay:")
            )
            self.assertEqual(total, scene["duration_ms"])
            self.assertEqual(
                "script.execute: scene_" + scene["id"] in text, bool(scene.get("loop"))
            )
            # The previewer describes the same strikes at the same times.
            prev = gp.to_previewer(scene, idx, "", markers)
            self.assertEqual(
                sorted(c["t"] for c in prev["cues"] if c["bus"] == "LED"), want
            )
            for c in prev["cues"]:
                if c["op"] == "strike" and c.get("targets"):
                    self.assertLessEqual(set(c["targets"]), set(ZIDS))
            self.assertEqual(prev["loop"], bool(scene.get("loop")))
            self.assertEqual(prev["dur"], scene["duration_ms"])

        # The whole document, through main(): every output loads as YAML.
        ge.SRC.write_text(yaml.safe_dump(doc))
        ge.MARKERS.write_text(json.dumps(markers))
        self.assertEqual(ge.main(), 0)
        out = yaml.safe_load(ge.OUT.read_text())
        ids = [s["id"] for s in out["script"]]
        # Long scenes continue in cont_<id>_N scripts (gen_esphome.CHUNK);
        # the heads keep the document's order, the fixed scripts follow.
        heads = [i for i in ids if not i.startswith("cont_")]
        self.assertEqual(
            heads[: len(doc["scenes"])], [f"scene_{s['id']}" for s in doc["scenes"]]
        )
        self.assertEqual(
            heads[len(doc["scenes"]) :], ["scene_stop", "run_scene", "show_playlist"]
        )
        for sc in out["script"]:  # and no chain is ever deep again
            self.assertLessEqual(len(sc["then"]), ge.CHUNK, sc["id"])
        self.assertEqual(
            out["select"][0]["options"][:-1], [s["id"] for s in doc["scenes"]]
        )
        for path in (ge.AUDIO_SD, ge.LIGHTS_OUT):
            yaml.load(path.read_text(), Loader=EsphomeLoader)
        # The rig outputs agree with the layouts the cues were emitted against.
        layouts = rl.zone_layouts(zones, doc["hardware"]["pixels_per_zone"])
        self.assertEqual(
            ge.RIG_OUT.read_text(), gen_rig.emit_rig_header(layouts, zones)
        )
        lights = yaml.safe_load(ge.LIGHTS_OUT.read_text())
        live = [z["id"] for z in zones if layouts[z["id"]].n > 0]
        self.assertEqual(
            [s["id"] for s in lights["light"]], [f"zone_{z}" for z in live]
        )
        for s in lights["light"]:
            self.assertEqual(s["rmt_symbols"], 64)
            self.assertIs(s["use_psram"], False)
        header = ge.RIG_OUT.read_text()
        biggest = max(layouts[z["id"]].n for z in zones)
        self.assertIn(f"RIG_MAX_PIXELS = {max(1, biggest)};", header)
        for z in zones:
            self.assertIn(f"{z['id']}_walk[]", header)


if __name__ == "__main__":
    unittest.main(verbosity=2)
