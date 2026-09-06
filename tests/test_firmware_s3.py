"""The ESP32-S3 carrier build against the board's own written spec.

`firmware/castle_s3.yaml` is the one build with no hardware behind it. The
S2 in the yard is checked by a porch: a pin that moved shows up as a dark
window that evening. This one is checked by nothing at all until the
castle-carrier v5 board arrives, so its contract is written down here
instead — the pin table copied out of that board's spec, the four platform
statements the port turns on, and the RMT arithmetic that fails quietly on
the wrong chip.

The spec is `docs/V5-SPEC.md` in the castle-carrier v5 KiCad project, which
is a different repository on a different volume. It is NOT importable from
here and must never be edited from here, so the table below is a COPY, with
the section it came from named beside every claim. If the two ever disagree,
the board is right and this file is stale — `gen/check_firmware_pins.py`
over there is the tool that says so, and it reads this firmware directly.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gen_rig
import rig_layout as rl
import yaml

FW = ROOT / "firmware"

# ── The pin table, V5-SPEC §2.2 ──────────────────────────────────────
# GPIO -> the carrier net it lands on. Identical to gen/design.py PIN_MAP in
# that project, which is the table the schematic is generated from.
CARRIER_PINS: dict[int, str] = {
    0: "BOOT0",
    1: "SDA",
    2: "SCL",
    5: "SD_CS",
    8: "SPARE_3V3",
    10: "BTN",
    11: "I2S_BCLK",
    12: "I2S_LRC",
    14: "TOWERR_3V3",
    15: "I2S_DIN",
    16: "DOOR_3V3",
    17: "PIR_OUT",
    18: "TOWERL_3V3",
    19: "USB_DM",
    20: "USB_DP",
    35: "SD_MOSI",
    36: "SD_SCK",
    37: "SD_MISO",
    38: "DBG_RX",
    43: "DBG_TX",
}

#: ESPHome substitution -> the carrier net that pin has to reach. The same
#: contract gen/check_firmware_pins.py MAP states from the other side.
SUB_NET = {
    "pin_towerL": "TOWERL_3V3",
    "pin_door": "DOOR_3V3",
    "pin_towerR": "TOWERR_3V3",
    "pin_pir": "PIR_OUT",
    "pin_i2s_dout": "I2S_DIN",
    "pin_i2s_bclk": "I2S_BCLK",
    "pin_i2s_lrclk": "I2S_LRC",
    "sd_cs": "SD_CS",
    "sd_sck": "SD_SCK",
    "sd_mosi": "SD_MOSI",
    "sd_miso": "SD_MISO",
}

#: V5-SPEC §2.2 and design.py STRAP_PINS. GPIO0 has the BOOT button and its
#: pull-up and is hardware's alone; 3/45/46 are wired to NOTHING on the
#: carrier, and their reset defaults are the states the board wants.
STRAP_PINS = {0, 3, 45, 46}
#: The USB Serial/JTAG pair. A peripheral, not a GPIO any component names.
USB_PINS = {19, 20}
#: In-package SPI flash and PSRAM — not on a WROOM-1's pin list at all
#: (V5-SPEC §2.3). GPIO33/34 are not brought out either, which is what
#: deletes the S2 Feather's status pixel (§13.3).
FLASH_PSRAM_PINS = set(range(26, 33))
NOT_BROUGHT_OUT = FLASH_PSRAM_PINS | {33, 34}

#: Carrier nets no firmware pin claims, each for a stated reason
#: (check_firmware_pins.py UNCLAIMED_OK). Named here so a pin quietly
#: appearing on one of them is a test failure rather than a surprise.
UNCLAIMED = {"BOOT0", "BTN", "DBG_RX", "SPARE_3V3", "USB_DM", "USB_DP"}


class Tag:
    """An unresolved YAML tag: `!include x.yaml`, `!secret`, `!remove`, ..."""

    def __init__(self, name: str, value: Any) -> None:
        self.name = name
        self.value = value

    def __repr__(self) -> str:  # pragma: no cover - failure messages only
        return f"!{self.name} {self.value!r}"


class Loader(yaml.SafeLoader):
    """SafeLoader that keeps ESPHome's tags instead of choking on them."""


def _tag(loader: Any, suffix: str, node: Any) -> Tag:
    if isinstance(node, yaml.ScalarNode):
        return Tag(suffix, loader.construct_scalar(node))
    if isinstance(node, yaml.SequenceNode):
        return Tag(suffix, loader.construct_sequence(node))
    return Tag(suffix, loader.construct_mapping(node))


Loader.add_multi_constructor("!", _tag)


def load(rel: str) -> dict[str, Any]:
    doc = yaml.load((FW / rel).read_text(), Loader=Loader)
    assert isinstance(doc, dict), rel
    return doc


def include_tree(rel: str) -> dict[str, dict[str, Any]]:
    """Every file a build reads, by relative path, following `packages:`.

    Which files a target includes IS the deletion, for this port: the S3
    build has no status pixel because it does not include the file that
    declares one. ESPHome packages append lists and cannot subtract from
    them, so "not included" is the only spelling that exists.
    """
    out: dict[str, dict[str, Any]] = {}
    pending = [rel]
    while pending:
        cur = pending.pop()
        if cur in out:
            continue
        out[cur] = doc = load(cur)
        pkgs = doc.get("packages") or {}
        pending.extend(
            str(v.value)
            for v in pkgs.values()
            if isinstance(v, Tag) and v.name == "include"
        )
    return out


S3 = load("castle_s3.yaml")
S3_TREE = include_tree("castle_s3.yaml")
SD_TREE = include_tree("castle_sd.yaml")


#: Substitution names that NAME A PIN — the same rule check_firmware_pins.py
#: uses, and for the same reason: `px_door: "12"` is a pixel count that
#: happens to look like a GPIO number.
PIN_SUB = ("pin_", "sd_")


def subs(tree: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Every substitution a build sees.

    A build's identity (`name`, `friendly`) is deliberately redefined — the
    root file overrides the core's, which is how one core serves several
    targets. A PIN defined twice is a different thing: two files disagreeing
    about where a signal goes, with ESPHome's merge order deciding which one
    wins. Nothing does that today, and nothing should start.
    """
    out: dict[str, str] = {}
    for rel, doc in sorted(tree.items()):
        for k, v in (doc.get("substitutions") or {}).items():
            if k.startswith(PIN_SUB) and k in out and out[k] != v:
                raise AssertionError(f"{rel}: substitution {k} redefined")
            out[k] = v
    return out


S3_SUBS = subs(S3_TREE)

# The show, for the RMT arithmetic below: the same three zones both
# builds drive, read from the one file that declares them.
DOC = yaml.safe_load((ROOT / "scenes" / "scenes.yaml").read_text())
ZONES: list[dict[str, Any]] = DOC["zones"]
LAYOUTS = rl.zone_layouts(ZONES, DOC["hardware"]["pixels_per_zone"])
LIVE = [z for z in ZONES if LAYOUTS[z["id"]].n > 0]


class TestPinTable(unittest.TestCase):
    """Every GPIO the S3 build drives lands where V5-SPEC §2.2 says."""

    def test_every_named_pin_is_the_carriers(self) -> None:
        for sub, net in SUB_NET.items():
            gpio = int(S3_SUBS[sub])
            self.assertIn(gpio, CARRIER_PINS, f"{sub}: GPIO{gpio} is not on the map")
            self.assertEqual(CARRIER_PINS[gpio], net, f"{sub} is on the wrong net")

    def test_no_two_signals_share_a_pin(self) -> None:
        pins = [int(S3_SUBS[s]) for s in SUB_NET]
        self.assertEqual(len(set(pins)), len(pins), "two drivers on one pin")

    def test_the_i2c_bus_is_where_the_ammeter_is(self) -> None:
        """§13.7 — new in v5; there has never been an i2c: block before."""
        self.assertEqual(S3["i2c"]["sda"], "GPIO1")
        self.assertEqual(S3["i2c"]["scl"], "GPIO2")
        self.assertEqual(CARRIER_PINS[1], "SDA")
        self.assertEqual(CARRIER_PINS[2], "SCL")

    def test_the_ammeter_is_the_carriers_and_not_a_breakout(self) -> None:
        """0x41 is the carrier's strapping of U5; a Qwiic INA219 is 0x40
        and reading one instead would silently measure nothing (§13.7)."""
        (ina,) = [s for s in S3["sensor"] if s["platform"] == "ina219"]
        self.assertEqual(ina["address"], 0x41)
        self.assertEqual(ina["shunt_resistance"], "0.01 ohm")
        self.assertEqual(ina["max_current"], "8A")
        self.assertEqual(ina["max_voltage"], "16.0V")

    def test_the_pins_that_survive_the_port_are_the_same_numbers(self) -> None:
        """§2.1's headline: the port renames nothing. The S2 build and the
        S3 build must agree on every one of these, or one of them is wrong."""
        sd = subs(SD_TREE)
        for sub in SUB_NET:
            self.assertEqual(sd[sub], S3_SUBS[sub], sub)


class TestForbiddenPins(unittest.TestCase):
    """Pins the spec says no component may name, and what happens if one does."""

    @staticmethod
    def gpio(pin: str) -> int:
        """`GPIO17` or `GPIO${pin_pir}` — the generated strips name their
        pin through a substitution so a bench build can repoint a zone."""
        raw = pin.removeprefix("GPIO")
        if raw.startswith("${"):
            raw = S3_SUBS[raw[2:-1]]
        return int(raw)

    def driven(self) -> dict[int, str]:
        """GPIO -> what drives it, across every pin the S3 build declares:
        the substitutions AND the literal `GPIOnn` in any component."""
        out = {int(S3_SUBS[s]): s for s in SUB_NET}
        for key in ("sda", "scl"):
            out[self.gpio(str(S3["i2c"][key]))] = f"i2c {key}"
        for rel, doc in sorted(S3_TREE.items()):
            for comp in ("switch", "light", "binary_sensor", "output"):
                for item in doc.get(comp) or []:
                    pin = item.get("pin") if isinstance(item, dict) else None
                    if isinstance(pin, str) and pin.startswith("GPIO"):
                        out[self.gpio(pin)] = f"{rel} {comp}"
        return out

    def test_no_strapping_pin_is_driven(self) -> None:
        """§1.4/§4.5: each has a failure mode if driven, and their reset
        defaults are already the states this board wants. GPIO0 in
        particular is the BOOT button's — hardware's alone."""
        for gpio, what in self.driven().items():
            self.assertNotIn(gpio, STRAP_PINS, f"{what} drives strapping GPIO{gpio}")

    def test_the_usb_pair_is_left_to_the_peripheral(self) -> None:
        for gpio, what in self.driven().items():
            self.assertNotIn(gpio, USB_PINS, f"{what} takes USB GPIO{gpio}")

    def test_nothing_names_a_pin_the_module_does_not_bring_out(self) -> None:
        """GPIO26-32 are the in-package flash and PSRAM (§2.3); 33 and 34
        are simply not on a WROOM-1's pin list. The S2's status pixel was on
        GPIO33, which is exactly why §13.3 deletes it."""
        for gpio, what in self.driven().items():
            self.assertNotIn(
                gpio, NOT_BROUGHT_OUT, f"{what} names GPIO{gpio}, absent on a WROOM-1"
            )

    def test_the_unclaimed_nets_are_still_unclaimed(self) -> None:
        claimed = {CARRIER_PINS.get(g) for g in self.driven()}
        self.assertEqual(claimed & UNCLAIMED, set())

    def test_the_walk_actually_found_the_whole_rig(self) -> None:
        """Every assertion above is `assertNotIn` over what `driven()`
        returns, so a walk that quietly found nothing would pass all of
        them. It has to find the three strips, both I2S clocks, the PIR,
        the four SD lines and the new I2C pair — thirteen pins."""
        found = self.driven()
        self.assertEqual(len(found), 13, found)
        for zone in ("towerL", "towerR", "door"):
            gpio = int(S3_SUBS[f"pin_{zone}"])
            self.assertIn("light", found[gpio], f"{zone}'s strip was not walked")


class TestPlatformBlock(unittest.TestCase):
    """The four statements that decide which chip this firmware is for
    (§13.1, and the same list gen/check_firmware_pins.py PLATFORM checks)."""

    def test_the_variant_replaces_the_feathers_board(self) -> None:
        self.assertEqual(S3["esp32"]["variant"], "esp32s3")
        board = S3["esp32"]["board"]
        self.assertIsInstance(board, Tag, "board: must be !remove'd, not renamed")
        self.assertEqual(board.name, "remove")

    def test_the_module_has_eight_megabytes(self) -> None:
        """N8R2 (§13.6). Left at ESPHome's 4 MB default the OTA slots would
        be the Feather's, which is the constraint this board removes."""
        self.assertEqual(S3["esp32"]["flash_size"], "8MB")

    def test_the_console_is_the_s3s_hardware_peripheral(self) -> None:
        """UART0 was an S2 fact: that board had no other console and the USB
        CDC one faulted the chip inside sinf(). The S3 has USB Serial/JTAG
        in hardware, on GPIO19/20 (§13.1)."""
        self.assertEqual(S3["logger"]["hardware_uart"], "USB_SERIAL_JTAG")
        self.assertEqual(load("castle.yaml")["logger"]["hardware_uart"], "UART0")

    def test_psram_names_its_mode(self) -> None:
        """Required on the S3, and quad is the N8R2's — octal is the R8's,
        which would take GPIO35/36/37 and the microSD bus with them (§1.1)."""
        psram = load("castle.yaml")["psram"]
        self.assertEqual(psram["mode"], "quad")

    def test_the_dram0_diet_is_not_unwound_here(self) -> None:
        """§13.5 is explicit: get it booting on the new chip with the diet
        exactly as it is, then lift one line at a time and measure each."""
        self.assertNotIn("framework", S3["esp32"])


class TestNoOnboardPixel(unittest.TestCase):
    """§13.3 — there is no LED on a WROOM-1 and no GPIO33 to hang one on."""

    def lights(self, tree: dict[str, dict[str, Any]]) -> list[str]:
        return [
            item["id"]
            for doc in tree.values()
            for item in doc.get("light") or []
            if isinstance(item.get("id"), str)
        ]

    def test_the_s3_build_declares_no_status_pixel(self) -> None:
        self.assertNotIn("status_pixel", self.lights(S3_TREE))
        for doc in S3_TREE.values():
            for sw in doc.get("switch") or []:
                self.assertNotEqual(sw.get("id"), "neopixel_power")

    def test_the_s2_build_still_has_it(self) -> None:
        """The Feather in the yard is untouched by the port — this is the
        control on every assertion above."""
        self.assertIn("status_pixel", self.lights(SD_TREE))
        pixel = next(
            item
            for item in load("castle_sd.yaml")["light"]
            if item["id"] == "status_pixel"
        )
        self.assertEqual(pixel["pin"], "GPIO33")
        self.assertEqual(pixel["rmt_symbols"], 64)

    def test_both_builds_read_the_same_show(self) -> None:
        """The seam is the pixel and nothing else: everything about the show
        is in files both builds include, so neither can drift."""
        shared = {"castle.yaml", "castle_sd_common.yaml"}
        self.assertLessEqual(shared, set(S3_TREE))
        self.assertLessEqual(shared, set(SD_TREE))


class TestRmtBudget(unittest.TestCase):
    """§13.4 — the S3 allocates 48-word channel blocks, not the S2's 64, and
    a strip that asks for 64 gets no channel and stays dark without a word."""

    DOC = DOC
    ZONES = ZONES
    LAYOUTS = LAYOUTS
    LIVE = LIVE

    def override(self) -> list[dict[str, Any]]:
        text = gen_rig.emit_rmt_override(self.LAYOUTS, self.ZONES, gen_rig.S3)
        lights = yaml.load(text, Loader=Loader)["light"]
        assert isinstance(lights, list)
        return lights

    def test_the_chip_table_is_the_soc_caps_numbers(self) -> None:
        """SOC_RMT_MEM_WORDS_PER_CHANNEL 48, four TX-capable channels."""
        self.assertEqual((gen_rig.S3.block, gen_rig.S3.total), (48, 192))
        self.assertEqual((gen_rig.S2.block, gen_rig.S2.total), (64, 256))
        self.assertIs(gen_rig.CHIPS["esp32s3"], gen_rig.S3)

    def test_every_strip_is_re_spent_in_whole_s3_blocks(self) -> None:
        for item in self.override():
            self.assertEqual(item["rmt_symbols"] % gen_rig.S3.block, 0)
            self.assertEqual(item["rmt_symbols"], 48)

    def test_the_override_reaches_the_strips_by_name(self) -> None:
        got = [item["id"].value for item in self.override()]
        self.assertEqual(got, [f"zone_{z['id']}" for z in self.LIVE])
        for item in self.override():
            self.assertEqual(item["id"].name, "extend")

    def test_three_zones_leave_one_whole_tx_channel(self) -> None:
        spare = gen_rig.check_rmt_budget(self.ZONES, self.LAYOUTS, gen_rig.S3)
        self.assertEqual(spare, gen_rig.S3.block)
        self.assertIn("144 of 192 symbols spent", self.text())

    def test_the_spare_block_can_be_spent_and_is_not_an_error(self) -> None:
        """§7.4: four channel blocks fit exactly, whether that is four zones
        or three with one long strip taking a second. Spending the last one
        is a decision, not a fault — only asking for a fifth is."""
        zones = [{**z, "rmt_symbols": 128} for z in self.LIVE[:1]] + self.LIVE[1:]
        self.assertEqual(gen_rig.check_rmt_budget(zones, self.LAYOUTS, gen_rig.S3), 0)

    def test_the_carrier_reserves_nothing_for_a_pixel_it_does_not_have(self) -> None:
        """§13.3: there is no LED on a WROOM-1. The S2's status-pixel
        reservation (grade report 2026-09-06 J2) must not follow the port
        across — so the 128 the S2 now refuses is still spendable here, and
        nothing in this build's banner mentions a pixel."""
        self.assertEqual(
            gen_rig.check_rmt_budget(self.ZONES, self.LAYOUTS, gen_rig.S3),
            gen_rig.check_rmt_budget(
                self.ZONES, self.LAYOUTS, gen_rig.S3, reserved_blocks=0
            ),
        )
        self.assertNotIn("status pixel", self.text())
        self.assertNotIn("status pixel", gen_rig.emit_rmt_override.__doc__ or "")

    def test_overspending_stops_the_build_naming_this_chip(self) -> None:
        zones = [{**z, "rmt_symbols": 192} for z in self.LIVE]
        with self.assertRaises(SystemExit) as e:
            gen_rig.check_rmt_budget(zones, self.LAYOUTS, gen_rig.S3)
        self.assertIn("ESP32-S3 has 192", str(e.exception))

    def text(self) -> str:
        return gen_rig.emit_rmt_override(self.LAYOUTS, self.ZONES, gen_rig.S3)

    def test_the_generated_override_is_fresh(self) -> None:
        got = (FW / "generated" / "lights_s3.yaml").read_text()
        self.assertEqual(
            got,
            self.text(),
            "firmware/generated/lights_s3.yaml is stale — run `make generate`",
        )

    def test_the_s2s_strips_are_untouched_by_the_new_chip(self) -> None:
        """The porch board's generated file must not move a byte for a port
        it is not part of — the control on the whole chip-aware change."""
        for item in yaml.safe_load(gen_rig.emit_lights(self.LAYOUTS, self.ZONES, 7))[
            "light"
        ]:
            self.assertEqual(item["rmt_symbols"], 64, item["id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
