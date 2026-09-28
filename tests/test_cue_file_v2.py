"""Cue file version 2 (firmware v5.71), from the encoder's side.

The promise the format change rests on is that nothing which does not ask
for a v2 feature changes: a show with no ornament strike, no half mask and no
look is the same bytes a v5.70 castle has always played. That is pinned here
against the pre-v2 encoder's own output for a synthetic show touching every
v1 field (never the user's library). The rest is the v2 shapes round-tripping,
the version the encoder picks, and a v1-only reader refusing a v2 file whole.
The device's side of the same file is tests/test_cue_file_cxx.py.
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import cue_file
import helpers  # noqa: F401  (hermetic env)
from effect_vocab import EFFECT_IDS, FLASH_MODE_IDS, OVERLAY_IDS, PALETTE_IDS

ZONES = ["towerL", "towerR", "door"]
V1_SCENE: dict[str, Any] = {
    "id": "pin",
    "duration_ms": 61000,
    "base": {"towerL": "candle", "towerR": "ember", "door": "furnace"},
    "levels": {"towerL": 0.35, "towerR": 0.999, "door": 1.0},
    "zones": {
        "towerL": {"center": "eyes", "overlay": "chase", "palette": "toxic"},
        "towerR": {"overlay": "meteor", "phase": 2.345},
        "door": {"overlay": "sparkle", "palette": "moonlight"},
    },
}
V1_CUES: list[dict[str, Any]] = [
    {"t": 0, "op": "set", "zone": "door", "effect": "strobe"},
    {"t": 0, "op": "set", "zone": "towerL", "effect": "throb", "level": 0.555},
    {"t": 16, "op": "strike", "intensity": 0.5},
    {"t": 17, "op": "strike", "zone": "towerR", "pixels": "scatter", "decay": 0.93333},
    {"t": 250, "op": "strike", "targets": ["towerL", "door"], "pixels": "center",
     "color": [0.123, 0.5, 1.0, 0.0], "attack": 120, "intensity": 1.2345},
    # `zones` is a look's key only: a strike never read it and still must not.
    {"t": 400, "op": "strike", "zones": ["door"], "pixels": "ring", "layer": 0},
    {"t": 400, "op": "strike", "pixels": "all", "intensity": 0.0004, "decay": 1.0},
    {"t": 60999, "op": "set", "zone": "towerR", "effect": "off", "level": 0.0},
]  # fmt: skip
#: sha256 of what the pre-v2 encoder (git 061c2d0 tools/cue_file.py) wrote
#: for V1_SCENE + V1_CUES — 168 bytes.
V1_SHA256 = "1be184670046bc7777690060b3b4ab6c38b046c0d864d80be2c7d3c03cf89cb2"

#: Every JSON shape a v2 cue may take — what the lab's choreography emits.
V2_CUES: list[dict[str, Any]] = [
    {"t": 0, "op": "look", "targets": ["towerL"], "overlay": "chase", "rate": 0.5},
    {"t": 10, "op": "look", "zones": ["towerR", "door"], "palette": "toxic",
     "center": "none", "head": 0.25},
    {"t": 20, "op": "look", "zone": "door", "center": "eyes", "rate": 0},
    {"t": 30, "op": "look"},
    {"t": 40, "op": "strike", "zone": "door", "layer": 1, "pixels": "left",
     "intensity": 0.8, "decay": 0.85, "attack": 32, "color": [1, 0.5, 0, 0.1]},
    {"t": 50, "op": "strike", "targets": ["towerL", "towerR"], "pixels": "right"},
    {"t": 60, "op": "strike", "pixels": "top", "layer": 1},
    {"t": 70, "op": "strike", "zone": "towerL", "pixels": "bottom"},
    {"t": 80, "op": "look", "overlay": "meteor", "rate": 1.2345, "head": 0.9999},
]  # fmt: skip


def enc(cues: list[dict[str, Any]]) -> bytes:
    return cue_file.encode(V1_SCENE, cues, ZONES)


class TestVersionOne(unittest.TestCase):
    def test_a_show_with_no_v2_feature_is_the_bytes_it_always_was(self) -> None:
        blob = enc(V1_CUES)
        self.assertEqual(len(blob), 168)
        self.assertEqual(hashlib.sha256(blob).hexdigest(), V1_SHA256)
        self.assertEqual(blob[4], cue_file.VERSION)

    def test_a_v1_file_reads_the_same_to_both_readers(self) -> None:
        blob = enc(V1_CUES)
        old = cue_file.decode(blob, versions=(1,))
        self.assertEqual(old, cue_file.decode(blob))
        self.assertTrue(all(r.get("layer", 0) == 0 for r in old["records"]))

    def test_each_v2_feature_alone_is_what_makes_a_file_v2(self) -> None:
        strike: dict[str, Any] = {"t": 5, "op": "strike", "zone": "door"}
        extras: list[dict[str, Any]] = [{"layer": 1}]
        halves = ("left", "right", "top", "bottom")
        extras += [{"pixels": p} for p in (*halves, *(f"arc{k}" for k in range(8)))]
        for extra in extras:
            self.assertEqual(enc([*V1_CUES, {**strike, **extra}])[4], 2, extra)
        self.assertEqual(enc([*V1_CUES, {"t": 5, "op": "look"}])[4], 2)
        for pixels in ("all", "scatter", "center", "ring"):
            self.assertEqual(enc([{**strike, "pixels": pixels, "layer": 0}])[4], 1)


class TestVersionTwo(unittest.TestCase):
    def test_every_v2_shape_round_trips(self) -> None:
        doc = cue_file.decode(enc(V2_CUES))
        self.assertEqual(doc["version"], 2)
        r = doc["records"]
        self.assertEqual(
            r[0],
            {"t": 0, "op": "look", "mask": 0b001, "overlay": OVERLAY_IDS["chase"],
             "palette": None, "center": None, "rate": 0.5, "head": None},
        )  # fmt: skip
        self.assertEqual(
            (r[1]["mask"], r[1]["palette"], r[1]["center"], r[1]["head"]),
            (0b110, PALETTE_IDS["toxic"], -1, 0.25),
        )
        self.assertEqual((r[2]["center"], r[2]["rate"]), (EFFECT_IDS["eyes"], 0.0))
        self.assertEqual(
            {k: r[3][k] for k in ("mask", "overlay", "palette", "center", "rate")},
            {"mask": 0b111, "overlay": None, "palette": None, "center": None,
             "rate": None},
        )  # fmt: skip
        self.assertEqual(
            {k: r[4][k] for k in ("mask", "layer", "mode", "attack", "color")},
            {"mask": 0b100, "layer": 1, "mode": FLASH_MODE_IDS["left"], "attack": 32,
             "color": [1.0, 0.5, 0.0, 0.1]},
        )  # fmt: skip
        self.assertEqual((r[5]["mask"], r[5]["layer"], r[5]["mode"]), (0b011, 0, 5))
        self.assertEqual((r[6]["mask"], r[6]["layer"], r[6]["mode"]), (0b111, 1, 6))
        self.assertEqual((r[7]["mode"], r[7]["layer"]), (7, 0))
        # Milli-turns, half up; a head wraps into 0..999.
        self.assertEqual((r[8]["rate"], r[8]["head"]), (1.235, 0.0))

    def test_every_arc_round_trips_on_both_layers(self) -> None:
        """arc0..arc7 are masks 8-15: the whole high nibble of the op byte."""
        for k in range(8):
            for layer in (0, 1):
                cue = {"t": 0, "op": "strike", "zone": "door", "pixels": f"arc{k}",
                       "layer": layer}  # fmt: skip
                blob = enc([cue])
                self.assertEqual((blob[4], blob[16 + 24 + 4] >> 4), (2, 8 + k))
                rec = cue_file.decode(blob)["records"][0]
                self.assertEqual((rec["mode"], rec["layer"]), (8 + k, layer))
                self.assertEqual(FLASH_MODE_IDS[f"arc{k}"], 8 + k)

    def test_the_layer_bit_is_bit_seven_of_the_mask(self) -> None:
        blob = enc([{"t": 0, "op": "strike", "zone": "towerR", "layer": 1}])
        self.assertEqual(blob[16 + 24 + 5], 0x80 | 0b010)

    def test_rate_and_head_scaling(self) -> None:
        def look(**kw: Any) -> dict[str, Any]:
            blob = enc([{"t": 0, "op": "look", **kw}])
            rec: dict[str, Any] = cue_file.decode(blob)["records"][0]
            return rec

        self.assertEqual(look(rate=0.0001)["rate"], 0.001)  # never rounds to legacy
        self.assertEqual(look(rate=-3)["rate"], 0.0)
        self.assertEqual(look(rate=1e9)["rate"], 65.534)  # below the keep value
        self.assertEqual(look(head=1.25)["head"], 0.25)
        self.assertIsNone(look(rate=None)["rate"])

    def test_bad_v2_input_is_refused_by_name(self) -> None:
        for bad in ({"op": "strike", "layer": 2}, {"op": "look", "overlay": "nope"},
                    {"op": "look", "palette": "nope"}, {"op": "look", "center": "nope"},
                    {"op": "look", "zones": ["attic"]}):  # fmt: skip
            with self.assertRaises(SystemExit, msg=str(bad)):
                enc([{"t": 0, **bad}])


class TestOldReaders(unittest.TestCase):
    def test_a_v1_only_reader_refuses_a_v2_file_whole(self) -> None:
        """What a v5.70 castle does with it: nothing, and says so."""
        with self.assertRaises(ValueError):
            cue_file.decode(enc(V2_CUES), versions=(1,))

    def test_an_unknown_version_is_refused_by_this_reader_too(self) -> None:
        blob = bytearray(enc(V1_CUES))
        blob[4] = 3
        with self.assertRaises(ValueError):
            cue_file.decode(bytes(blob))

    def test_the_emulator_accepts_a_v2_file_as_the_device_does(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "song.cue"
            path.write_bytes(enc(V2_CUES))
            self.assertTrue(cue_file.loads(path))
            self.assertEqual(cue_file.loaded_count(path), len(V2_CUES))
            path.write_bytes(bytes([*enc(V2_CUES)[:4], 3, *enc(V2_CUES)[5:]]))
            self.assertFalse(cue_file.loads(path))
            self.assertEqual(cue_file.loaded_count(path), 0)


if __name__ == "__main__":
    unittest.main()
