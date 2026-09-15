"""The ESP32-S3 Feather build against the v3.3a carrier it plugs into.

`firmware/castle_feather_s3.yaml` is the S2 porch build with the chip
swapped: the same carrier, the same sockets, and Adafruit's ESP32-S3 Feather
in them. The carrier was drawn for either Feather, so the claim this build
rests on is that NOT ONE PIN MOVES — and the claims it adds are the four
chip facts in that file's header. Both are written down here, because the
board that proves them has never run it.

The pin table is a COPY of `gen/design.py` in the castle-carrier-v3 KiCad
project (the frozen v3.3a that went to JLCPCB). Like the v5 table in
test_firmware_s3.py it is not importable from here and must never be edited
from here: if the two ever disagree, the board is right and this file is
stale.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gen_rig
from test_firmware_s3 import (
    LAYOUTS,
    STRAP_PINS,
    SUB_NET,
    USB_PINS,
    ZONES,
    Tag,
    include_tree,
    load,
    subs,
)

# ── The v3.3a pin table, castle-carrier-v3 gen/design.py ─────────────
# GPIO -> the carrier net its Feather pad lands on. Pads that carry the
# same GPIO on the S2 (#5000) and S3 (#5477) Feathers, which is the only
# kind of pad v3.3a wires. SDA/SCL (GPIO3/4), D6, D9, D13 and TX (GPIO39)
# carry no net on this board.
CARRIER_PINS: dict[int, str] = {
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
    35: "SD_MOSI",
    36: "SD_SCK",
    37: "SD_MISO",
    38: "DBG_RX",
    43: "DBG_TX",
}

#: The Feather's own parts, which never reach a carrier pad: the status
#: NeoPixel and the rail that powers it. `PIN_NEOPIXEL 33` and
#: `NEOPIXEL_POWER 21` in both Feathers' pins_arduino.h — the reason the S2
#: build's pixel carries over unchanged.
FEATHER_ONBOARD = {33: "status_pixel", 21: "neopixel_power"}

#: In-package flash and PSRAM on the S3 chip; not on the Feather's header.
FLASH_PSRAM_PINS = set(range(26, 33))

FS3 = load("castle_feather_s3.yaml")
FS3_TREE = include_tree("castle_feather_s3.yaml")
FS3_SUBS = subs(FS3_TREE)
SD_TREE = include_tree("castle_sd.yaml")


def gpio(pin: str) -> int:
    """`GPIO17` or `GPIO${pin_pir}`."""
    raw = pin.removeprefix("GPIO")
    if raw.startswith("${"):
        raw = FS3_SUBS[raw[2:-1]]
    return int(raw)


def driven() -> dict[int, str]:
    """GPIO -> what drives it: the pin substitutions AND the literal `GPIOnn`
    in any component the build reads."""
    out = {int(FS3_SUBS[s]): s for s in SUB_NET}
    for rel, doc in sorted(FS3_TREE.items()):
        for comp in ("switch", "light", "binary_sensor", "output"):
            for item in doc.get(comp) or []:
                pin = item.get("pin") if isinstance(item, dict) else None
                if isinstance(pin, str) and pin.startswith("GPIO"):
                    out[gpio(pin)] = f"{rel} {comp}"
    return out


class TestPinTable(unittest.TestCase):
    """Every GPIO this build drives lands where v3.3a wires it."""

    def test_every_named_pin_is_the_carriers(self) -> None:
        for sub, net in SUB_NET.items():
            pin = int(FS3_SUBS[sub])
            self.assertIn(pin, CARRIER_PINS, f"{sub}: GPIO{pin} is not wired")
            self.assertEqual(CARRIER_PINS[pin], net, f"{sub} is on the wrong net")

    def test_not_one_pin_moves_from_the_s2_build(self) -> None:
        """The whole premise: the Feather in the yard and this one are the
        same carrier's two chips, so every pin substitution agrees."""
        sd = subs(SD_TREE)
        for sub in SUB_NET:
            self.assertEqual(sd[sub], FS3_SUBS[sub], sub)

    def test_the_feathers_own_pins_never_reach_the_carrier(self) -> None:
        for pin in FEATHER_ONBOARD:
            self.assertNotIn(pin, CARRIER_PINS)

    def test_the_walk_found_the_rig_and_the_pixel(self) -> None:
        """Every assertion in TestForbiddenPins is `assertNotIn` over this
        walk, so a walk that found nothing would pass them all. Eleven
        signals, the pixel and its rail."""
        found = driven()
        self.assertEqual(
            set(found), {int(FS3_SUBS[s]) for s in SUB_NET} | set(FEATHER_ONBOARD)
        )
        for zone in ("towerL", "towerR", "door"):
            self.assertIn("light", found[int(FS3_SUBS[f"pin_{zone}"])], zone)


class TestForbiddenPins(unittest.TestCase):
    """The S3's own rules, which the S2 build never had to satisfy."""

    def test_no_strapping_pin_is_driven(self) -> None:
        """GPIO3 is an S3 strapping pin and was the S2 build's free I2C pad;
        v3.3a leaves it unconnected, and so must this build."""
        for pin, what in driven().items():
            self.assertNotIn(pin, STRAP_PINS, f"{what} drives strapping GPIO{pin}")

    def test_the_usb_pair_is_left_to_the_console(self) -> None:
        for pin, what in driven().items():
            self.assertNotIn(pin, USB_PINS, f"{what} takes USB GPIO{pin}")

    def test_nothing_names_the_flash_or_psram_pins(self) -> None:
        for pin, what in driven().items():
            self.assertNotIn(pin, FLASH_PSRAM_PINS, f"{what} names GPIO{pin}")

    def test_no_i2c_bus(self) -> None:
        """v3.3a has no J15 and no INA219: SDA/SCL are no-connects. An i2c:
        block here would be castle_s3.yaml's ammeter leaking across."""
        for rel, doc in FS3_TREE.items():
            self.assertNotIn("i2c", doc, rel)


class TestPlatformBlock(unittest.TestCase):
    """The chip facts in castle_feather_s3.yaml's header, one by one."""

    def test_the_board_is_the_psram_feather(self) -> None:
        """#5477 is `adafruit_feather_esp32s3`; #5323 is `_nopsram`, and
        castle.yaml's psram: block would find nothing on it."""
        self.assertEqual(FS3["esp32"]["board"], "adafruit_feather_esp32s3")
        self.assertEqual(FS3["esp32"]["variant"], "esp32s3")
        self.assertEqual(load("castle.yaml")["psram"]["mode"], "quad")

    def test_four_megabytes_like_the_s2(self) -> None:
        self.assertEqual(FS3["esp32"]["flash_size"], "4MB")

    def test_the_console_is_the_usb_serial_jtag_peripheral(self) -> None:
        self.assertEqual(FS3["logger"]["hardware_uart"], "USB_SERIAL_JTAG")
        self.assertEqual(load("castle.yaml")["logger"]["hardware_uart"], "UART0")

    def test_the_dram0_diet_is_not_unwound_here(self) -> None:
        self.assertNotIn("framework", FS3["esp32"])


class TestStatusPixel(unittest.TestCase):
    """The S2 build's pixel comes across; its 64-symbol block cannot."""

    def status_pixel(self, tree: dict[str, dict[str, Any]]) -> dict[str, Any]:
        pixels: list[dict[str, Any]] = [
            item
            for doc in tree.values()
            for item in doc.get("light") or []
            if item.get("id") == "status_pixel"
        ]
        (pixel,) = pixels
        return pixel

    def test_the_pixel_is_the_s2_builds(self) -> None:
        pixel = self.status_pixel(FS3_TREE)
        self.assertEqual(pixel["pin"], "GPIO33")
        self.assertIn("castle_sd.yaml", FS3_TREE)

    def test_its_block_is_re_spent_in_s3_units(self) -> None:
        (ext,) = [
            item
            for item in FS3["light"]
            if isinstance(item["id"], Tag) and item["id"].value == "status_pixel"
        ]
        self.assertEqual(ext["id"].name, "extend")
        self.assertEqual(ext["rmt_symbols"], gen_rig.S3.block)
        # The control: the porch build's own number is untouched.
        self.assertEqual(self.status_pixel(SD_TREE)["rmt_symbols"], 64)

    def test_the_strips_are_the_carrier_builds_override(self) -> None:
        pkg = FS3["packages"]["rmt_s3"]
        self.assertEqual((pkg.name, pkg.value), ("include", "generated/lights_s3.yaml"))
        self.assertEqual(list(FS3["packages"]), ["sd", "rmt_s3"])

    def test_three_strips_and_the_pixel_spend_every_channel(self) -> None:
        """48 x 3 + 48 = 192 of 192. Spending the last one is the budget
        working, not failing; a fourth zone is what would fail."""
        spare = gen_rig.check_rmt_budget(
            ZONES,
            LAYOUTS,
            gen_rig.S3,
            reserved_blocks=gen_rig.STATUS_PIXEL_BLOCKS,
        )
        self.assertEqual(spare, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
