"""Fuzz the generators with random scenes.yaml-shaped documents.

The card path (gen_scene_cards.py -> cue_file.py + scene_manifest.py) and
gen_previewer.py each read the same scene and must not crash, must produce
something that loads back, and must describe the SAME show: same cue times,
same zones, every zone index inside the arrays the firmware will write into,
every scene present in the manifest with its own length. The hand-written
tests pin the shapes the real scenes.yaml uses; this throws every combination
the format allows — odd rigs, empty zones, out-of-order cues, strikes with
every optional field, pulse streams with random markers — with a fixed seed,
so a red run is reproducible and the seed is in the failure message.

Until v5.67 the device half of this was `ge.emit_scene`, and the fuzz was
largely about its delta arithmetic: no `delay:` may be negative, and they must
sum back to duration_ms. Both facts are gone with the deltas. What replaced
them is the ENCODER, which is a stricter target for a fuzzer than a text
emitter ever was: a field that does not fit a u8 or a u16 is a refusal rather
than a wrong number, and decode() has to give back exactly what was put in.
"""

from __future__ import annotations

import contextlib
import io
import json
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import cue_file
import gen_esphome as ge
import gen_previewer as gp
import gen_rig
import gen_scene_cards as gc
import rig_layout as rl
import scene_manifest
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
    "FALLBACK_SCENES_OUT",
    "CARD_SCENES",
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


def zone_writes(rec: dict[str, Any], nz: int) -> list[tuple[str, int]]:
    """(global, index) for every zone array slot castle_cues::apply will write
    for this record — the successor of scanning `id(zone_x)[i]` out of an
    emitted lambda. An index past the end of one of these arrays is memory
    corruption on the device, which is why it is checked at all.
    """
    out = []
    for i in range(nz):
        if not rec["mask"] >> i & 1:
            continue
        if rec["op"] == "set":
            out.append(("effect", i))
            if rec["level"] is not None:
                out.append(("level", i))
            continue
        out += [
            ("flash", i),
            ("flash_target", i),
            ("flash_decay", i),
            ("flash_mode", i),
            ("flash_epoch", i),
        ]
        out += [("flash_col", i * 4 + k) for k in range(4)]
    return out  # fmt: skip


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
        zids = [z["id"] for z in zones]
        for idx, scene in enumerate(doc["scenes"], start=1):
            # The card path, all the way through the encoder and back.
            cues = gc.scene_cues(scene, markers)
            card = cue_file.decode(cue_file.encode(scene, cues, zids))
            self.assertEqual(len(card["zones"]), nz)
            # Every zone array slot the firmware will write is inside it.
            for rec in card["records"]:
                for name, i in zone_writes(rec, nz):
                    self.assertLess(i, nz * 4 if name == "flash_col" else nz,
                                    f"{name}[{i}]")  # fmt: skip
            # The records land on the source times, in order, and the file
            # carries the length the runner will wait for.
            pulse = ge.pulse_cues(scene, markers)
            want = sorted(c["t"] for c in (scene.get("cues") or []) + pulse)
            self.assertEqual([r["t"] for r in card["records"]], want)
            self.assertEqual(card["duration_ms"], scene["duration_ms"])
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
        out = yaml.load(ge.OUT.read_text(), Loader=EsphomeLoader)
        # Three scripts, whatever the document holds: the show's shape is on
        # the card. (Their action counts are tests/test_gen_chunks.py's.)
        self.assertEqual(
            [s["id"] for s in out["script"]],
            ["scene_stop", "run_scene", "show_playlist"],
        )
        # And the card carries the whole show: a manifest row per scene, in
        # the document's order, with its own length and level, plus its cues.
        entries = scene_manifest.decode((ge.CARD_SCENES / "show.man").read_bytes())
        self.assertEqual([e["id"] for e in entries], [s["id"] for s in doc["scenes"]])
        for e, s in zip(entries, doc["scenes"], strict=True):
            self.assertEqual(e["duration_ms"], s["duration_ms"])
            self.assertEqual(e["loop"], bool(s.get("loop")))
            self.assertTrue((ge.CARD_SCENES / f"{s['id']}.cue").exists())
        # The PIR's scene is a `text`, not a `select`, since v5.69 (J1, grade
        # report 2026-09-17 pm): the legal ids are the CARD's, checked by
        # /api/pir, so no compiled option list can go stale. What the generator
        # still decides is the DEFAULT, and it has to be a scene this document
        # actually has.
        self.assertNotIn("select", out)
        pir = next(t for t in out["text"] if t["id"] == "pir_scene")
        self.assertIn(pir["initial_value"], [s["id"] for s in doc["scenes"]])
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
            # One block per strip, and a block is 48 words on the ESP32-S3 —
            # the S2's 64 went with the S2 on 2026-09-17. Pinned as a literal
            # rather than gen_rig.RMT_BLOCK on purpose: a generator that
            # silently changed the number would agree with itself.
            self.assertEqual(s["rmt_symbols"], 48)
            self.assertIs(s["use_psram"], False)
        header = ge.RIG_OUT.read_text()
        biggest = max(layouts[z["id"]].n for z in zones)
        self.assertIn(f"RIG_MAX_PIXELS = {max(1, biggest)};", header)
        for z in zones:
            self.assertIn(f"{z['id']}_walk[]", header)


if __name__ == "__main__":
    unittest.main(verbosity=2)
