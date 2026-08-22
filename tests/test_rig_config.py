"""The rig, end to end: scenes.yaml -> generator -> firmware/generated -> castle.yaml.

Four descriptions of the same three strips have to agree — the `zones:`
block, the strips the generator emits, the geometry header the render loop
indexes, and the substitutions castle.yaml feeds those strips. A pin or a
pixel count that drifts between them is a dark window or a cue aimed at a
pixel the strip does not have, and nothing on the device reports either.

Also pins the two S2 facts bench-diagnosed on 2026-08-19: `rmt_symbols: 64`
per strip (the S2's RMT has 256 symbols total; ESPHome's 192 default lets
the first strip take three blocks and starves the rest) and `use_psram:
false` (the RMT refill ISR must read the buffer during flash-cache
blackouts). Both are the kind of line that gets "tidied" away.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gen_rig
import rig_layout as rl
import yaml

DOC = yaml.safe_load((ROOT / "scenes" / "scenes.yaml").read_text())
ZONES: list[dict] = DOC["zones"]
PER: int = DOC["hardware"]["pixels_per_zone"]
LAYOUTS = rl.zone_layouts(ZONES, PER)
LIVE = [z for z in ZONES if LAYOUTS[z["id"]].n > 0]

#: Fixtures Adafruit only ever made in RGB (mirrors `rgbOnly` in web/src/rig.ts).
RGB_ONLY = {"wing32", "mini"}
#: The pins the wiring doc budgets for the three zones (web/src/rig.ts ZONE_PIN).
ZONE_PIN = {"towerL": 18, "door": 16, "towerR": 14}


def strips() -> list[dict]:
    lights = yaml.safe_load(gen_rig.emit_lights(LAYOUTS, ZONES, PER))["light"]
    assert isinstance(lights, list)
    return lights


class TestZonesBlock(unittest.TestCase):
    def test_three_zones_in_the_declared_order(self) -> None:
        """The firmware indexes zone_* globals by this order; the web desk's
        ZONE_DECL and the generated RIG[] both assume it."""
        self.assertEqual([z["id"] for z in ZONES], ["towerL", "towerR", "door"])

    def test_every_zone_names_a_known_fixture_and_a_distinct_pin(self) -> None:
        pins = [z["pin"] for z in ZONES]
        self.assertEqual(len(set(pins)), len(pins), "two zones share a data pin")
        for z in ZONES:
            self.assertIn(z["fixture"], rl.FIXTURES, z)
            self.assertEqual(z["pin"], ZONE_PIN[z["id"]],
                             f"{z['id']} is not on the pin docs/WIRING.md budgets")

    def test_rgbw_flag_is_honest_about_the_part(self) -> None:
        """A FeatherWing or a mini PCB cannot be RGBW; claiming so makes the
        strip clock 32 bits into a 24-bit pixel and every colour is wrong."""
        for z in ZONES:
            if z["fixture"] in RGB_ONLY:
                self.assertFalse(z.get("rgbw", True), z)
            self.assertIsInstance(z.get("rgbw", True), bool, z)

    def test_pixel_overrides_match_the_part(self) -> None:
        for z in ZONES:
            if "pixels" in z:
                self.assertEqual(rl.layout_of(z["fixture"], z["pixels"]).n, z["pixels"])


class TestEmittedStrips(unittest.TestCase):
    def test_one_strip_per_wired_zone(self) -> None:
        self.assertEqual([s["id"] for s in strips()], [f"zone_{z['id']}" for z in LIVE])

    def test_s2_rmt_budget_and_internal_ram_on_every_strip(self) -> None:
        for s in strips():
            self.assertEqual(s["rmt_symbols"], 64, s["id"])
            self.assertIs(s["use_psram"], False, s["id"])
            self.assertEqual(s["platform"], "esp32_rmt_led_strip")
            self.assertEqual(s["chipset"], "WS2812")

    def test_each_strip_reads_its_own_substitutions(self) -> None:
        """Pin, count and colour type come through ${...} so bench.yaml can
        repoint a zone without editing the generated file."""
        for s, z in zip(strips(), LIVE, strict=True):
            zid = z["id"]
            self.assertEqual(s["pin"], f"GPIO${{pin_{zid}}}")
            self.assertEqual(s["num_leds"], f"${{px_{zid}}}")
            self.assertEqual(s["is_rgbw"], f"${{rgbw_{zid}}}")
            self.assertEqual(s["default_transition_length"], "0s")

    def test_each_strip_renders_its_own_zone_index(self) -> None:
        """RIG[zi] and render_zone(..., zi, ...) must use the declaration
        index; a copy-paste of the wrong one lights a neighbour's geometry."""
        for s, (zi, z) in zip(strips(), enumerate(LIVE), strict=True):
            lam = s["effects"][0]["addressable_lambda"]["lambda"]
            self.assertIn(f"RIG[{zi}]", lam, z["id"])
            self.assertIn(f"render_zone(buf, {zi}, fx", lam, z["id"])
            self.assertIn(f"&id(zone_flash)[{zi}]", lam)
            self.assertIn(f"&id(zone_flash_col)[{zi * 4}]", lam)
            self.assertIn(f"id(trim_{z['id'].lower()}).state", lam)

    def test_override_script_drives_every_live_strip(self) -> None:
        text = gen_rig.emit_lights(LAYOUTS, ZONES, PER)
        for z in LIVE:
            self.assertIn(f"id(zone_{z['id']})", text)


class TestRmtBudget(unittest.TestCase):
    """The S2 has 256 RMT symbols and no DMA. Overspend and a strip goes dark
       (it happened, 2026-08-19); underspend a long strip and its refill ISR
       runs to a 40 us deadline, which is a garbled pixel now and then."""

    def zones(self, **symbols: int) -> list[dict]:
        return [{**z, **({"rmt_symbols": symbols[z["id"]]} if z["id"] in symbols else {})}
                for z in ZONES]

    def test_a_zone_gets_one_block_unless_it_asks(self) -> None:
        self.assertEqual([s["rmt_symbols"] for s in strips()],
                         [gen_rig.RMT_BLOCK] * len(LIVE))

    def test_a_long_strip_can_be_given_a_second_block(self) -> None:
        zones = self.zones(door=128)
        lights = yaml.safe_load(gen_rig.emit_lights(LAYOUTS, zones, PER))["light"]
        got = {s["id"]: s["rmt_symbols"] for s in lights}
        self.assertEqual(got["zone_door"], 128)
        self.assertEqual(got["zone_towerL"], 64)

    def test_the_leftover_blocks_are_stated(self) -> None:
        """The SD build's status pixel needs one of them, so the number is
           not decoration — it is the reason that pixel can exist."""
        self.assertIn("192 of 256 symbols spent, 1 block(s) spare",
                      gen_rig.emit_lights(LAYOUTS, ZONES, PER))
        self.assertIn("256 of 256 symbols spent, 0 block(s) spare",
                      gen_rig.emit_lights(LAYOUTS, self.zones(door=128), PER))

    def test_overspending_the_peripheral_stops_the_build(self) -> None:
        with self.assertRaises(SystemExit) as e:
            gen_rig.emit_lights(LAYOUTS, self.zones(door=128, towerL=128), PER)
        self.assertIn("ESP32-S2 has 256", str(e.exception))

    def test_a_half_block_is_refused(self) -> None:
        for bad in (96, 32, 0):
            with self.assertRaises(SystemExit) as e:
                gen_rig.emit_lights(LAYOUTS, self.zones(door=bad), PER)
            self.assertIn("multiple of 64", str(e.exception))


class TestGeneratedFilesAreFresh(unittest.TestCase):
    """What the firmware was built from must be what scenes.yaml says now."""

    def test_rig_header_matches_scenes_yaml(self) -> None:
        want = gen_rig.emit_rig_header(LAYOUTS, ZONES)
        got = (ROOT / "firmware" / "generated" / "rig.h").read_text()
        self.assertEqual(got, want, "firmware/generated/rig.h is stale — run `make generate`")

    def test_lights_yaml_matches_scenes_yaml(self) -> None:
        want = gen_rig.emit_lights(LAYOUTS, ZONES, PER)
        got = (ROOT / "firmware" / "generated" / "lights.yaml").read_text()
        self.assertEqual(got, want, "firmware/generated/lights.yaml is stale — run `make generate`")

    def test_rig_header_pixel_counts_and_tables(self) -> None:
        text = (ROOT / "firmware" / "generated" / "rig.h").read_text()
        for z in ZONES:
            lay = LAYOUTS[z["id"]]
            row = re.search(rf"\{{(\d+), (-?\d+), (\d+), rig_tables::{z['id']}_walk", text)
            self.assertIsNotNone(row, z["id"])
            assert row is not None
            self.assertEqual(int(row.group(1)), lay.n)
            self.assertEqual(int(row.group(2)), -1 if lay.center is None else lay.center)
            self.assertEqual(int(row.group(3)), lay.fall_steps)
            for kind in ("walk", "fall", "core"):
                arr = re.search(rf"{z['id']}_{kind}\[\] = \{{(.*?)\}};", text)
                assert arr is not None
                n_vals = len(arr.group(1).split(","))
                self.assertEqual(n_vals, max(1, lay.n), f"{z['id']}_{kind}")
        declared = int(text.split("RIG_MAX_PIXELS = ", 1)[1].split(";", 1)[0])
        self.assertEqual(declared, max(1, *(LAYOUTS[z["id"]].n for z in ZONES)))


def _castle_substitutions() -> dict:
    """castle.yaml uses ESPHome tags (!secret, !lambda, !include); the
    substitutions block is plain, so load with those tags ignored."""

    class Loader(yaml.SafeLoader):
        pass

    Loader.add_multi_constructor("!", lambda loader, suffix, node: None)
    subs = yaml.load((ROOT / "firmware" / "castle.yaml").read_text(),
                     Loader=Loader)["substitutions"]
    assert isinstance(subs, dict)
    return subs


class TestCastleYamlSubstitutions(unittest.TestCase):
    """castle.yaml feeds the generated strips; its numbers must be the zones'."""

    SUBS = _castle_substitutions()

    def test_pin_count_and_colour_type_per_zone(self) -> None:
        for z in ZONES:
            zid = z["id"]
            self.assertEqual(int(self.SUBS[f"pin_{zid}"]), z["pin"], zid)
            self.assertEqual(int(self.SUBS[f"px_{zid}"]), LAYOUTS[zid].n, zid)
            self.assertEqual(self.SUBS[f"rgbw_{zid}"],
                             str(bool(z.get("rgbw", True))).lower(), zid)

    def test_no_zone_pin_collides_with_the_rest_of_the_board(self) -> None:
        zone_pins = {int(self.SUBS[f"pin_{z['id']}"]) for z in ZONES}
        others = {k: int(v) for k, v in self.SUBS.items()
                  if k.startswith("pin_") and k[4:] not in {z["id"] for z in ZONES}
                  and str(v).strip().isdigit()}
        for name, pin in others.items():
            self.assertNotIn(pin, zone_pins, f"{name} shares GPIO{pin} with a zone")


if __name__ == "__main__":
    unittest.main(verbosity=2)
