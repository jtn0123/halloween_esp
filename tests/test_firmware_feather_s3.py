"""THE production build against the v3.3a carrier it plugs into.

`firmware/castle_feather_s3.yaml` is the castle in the yard: an Adafruit
ESP32-S3 Feather #5477 in castle-carrier v3.3a, running since 2026-09-17
(v5.64, OTA'd over Wi-Fi, 10.27.27.81). It was the third of three builds for
three days before that, written as "the S2 porch build with the chip swapped";
the S2 was retired and the shape inverted, so this file is now the NATIVE one
and castle.yaml describes its chip.

What is checked here is therefore what is LEFT in this file — the Feather's own
status pixel and the playback codecs — plus the carrier's pin table, which the
build satisfies by not overriding a single pin of castle.yaml's.

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
    include_tree,
    load,
    subs,
)

# ── The v3.3a pin table, castle-carrier-v3 gen/design.py ─────────────
# GPIO -> the carrier net its Feather pad lands on. Every pad v3.3a wires
# carries the same GPIO on the S2 (#5000) and S3 (#5477) Feathers — the board
# was drawn for either, which is the whole reason the chip could be swapped
# without moving a wire. SDA/SCL (GPIO3/4), D6, D9, D13 and TX (GPIO39) carry
# no net on this board.
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
#: build's pixel survived the chip swap untouched, and the reason this build
#: is the one that declares it (the WROOM carrier has neither pad).
FEATHER_ONBOARD = {33: "status_pixel", 21: "neopixel_power"}

#: In-package flash and PSRAM on the S3 chip; not on the Feather's header.
FLASH_PSRAM_PINS = set(range(26, 33))

FS3 = load("castle_feather_s3.yaml")
FS3_TREE = include_tree("castle_feather_s3.yaml")
FS3_SUBS = subs(FS3_TREE)
#: The shared core, which since 2026-09-17 is where the chip is described.
CORE = load("castle.yaml")


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

    def test_this_build_overrides_no_pin_of_the_cores(self) -> None:
        """The premise, in the only form that still exists. Until 2026-09-17
        this compared the S2 build's substitutions against this one's, to prove
        the chip swap moved nothing; there is one build now, so the guard is
        that the pin map has ONE home per signal. Every net above resolves out
        of a SHARED file — castle.yaml for the rig and the amps,
        castle_sd_common.yaml for the card's SPI — and this file's own
        `substitutions:` says nothing about a pin, so there is no merge order
        for two files to disagree under."""
        declared = {
            k: rel
            for rel in ("castle.yaml", "castle_sd_common.yaml")
            for k in (load(rel).get("substitutions") or {})
            if k.startswith(("pin_", "sd_"))
        }
        for sub in SUB_NET:
            self.assertIn(sub, declared, f"{sub} left the shared files")
            self.assertEqual(load(declared[sub])["substitutions"][sub], FS3_SUBS[sub])
        self.assertEqual(
            [k for k in FS3["substitutions"] if k.startswith(("pin_", "sd_"))], []
        )

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
    """The S3's own rules, which the retired S2 build never had to satisfy —
    and which were the reason to check this build's pins at all, back when it
    was a chip swap of a config written for another chip."""

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
    """The chip facts — which live in castle.yaml now, and that inversion is
    itself the thing worth pinning: this build writes over NOTHING."""

    def test_this_build_says_nothing_about_the_platform(self) -> None:
        """The shape of the retirement (2026-09-17). While the S2 was the base
        this file carried an `esp32:` block, a `logger:` and an `!extend` of
        the status pixel's RMT block, all four of them overrides. An override
        that agrees with its base is invisible when the base changes, which is
        how a build ends up describing a chip nobody is holding."""
        for key in ("esp32", "logger", "psram"):
            self.assertNotIn(key, FS3, f"{key} belongs in castle.yaml now")

    def test_the_board_is_the_psram_feather(self) -> None:
        """#5477 is `adafruit_feather_esp32s3`; #5323 is `_nopsram`, and the
        psram: block would find nothing on it."""
        self.assertEqual(CORE["esp32"]["board"], "adafruit_feather_esp32s3")
        self.assertEqual(CORE["esp32"]["variant"], "esp32s3")
        self.assertEqual(CORE["psram"]["mode"], "quad")

    def test_four_megabytes_means_the_1_75_mb_ota_slot(self) -> None:
        """The tight resource on this board: 71.8% of 1,835,008 B at v5.64.
        tools/check_image.py reads the slot out of the partition table ESPHome
        writes, so this is the input that decides it."""
        self.assertEqual(CORE["esp32"]["flash_size"], "4MB")

    def test_the_console_is_the_usb_serial_jtag_peripheral(self) -> None:
        """The S3's hardware peripheral, on the USB-C the board is flashed
        over. The S2 had UART0 here and no usable console at all."""
        self.assertEqual(CORE["logger"]["hardware_uart"], "USB_SERIAL_JTAG")

    def test_the_dram0_diet_is_still_in_the_core_and_untouched(self) -> None:
        """The retirement changed the build's shape and was not allowed to
        change its behaviour: every sdkconfig line the S2 bought is still
        there, to be given back one at a time and measured on hardware."""
        self.assertNotIn("esp32", FS3)
        opts = CORE["esp32"]["framework"]["sdkconfig_options"]
        self.assertEqual(opts["CONFIG_ESP_WIFI_SOFTAP_SUPPORT"], "n")
        self.assertEqual(opts["CONFIG_ESP_WIFI_ENABLE_WPS"], "n")
        self.assertEqual(opts["CONFIG_LWIP_DNS_SUPPORT_MDNS_QUERIES"], "n")
        self.assertEqual(opts["CONFIG_LWIP_MAX_SOCKETS"], "16")
        # mDNS is the one line that HAS been given back — v5.66, the first of
        # them, measured on the board at +2,080 B of RAM
        # (firmware/pending/README.md). The lwIP option above is a different
        # thing and stays off: it is about resolving somebody ELSE'S .local
        # name, which nothing on this castle does, where `mdns:` is about
        # answering for its own.
        self.assertIs(CORE["mdns"]["disabled"], False)

    def test_the_playback_codecs_are_this_builds_and_not_the_cores(self) -> None:
        """FLAC is deliberately absent, and the list is here rather than in
        castle.yaml so the carrier build — which has no board to flash — does
        not carry flash cost for decoders nobody has asked it to prove."""
        self.assertEqual(sorted(FS3["audio"]["codecs"]), ["mp3", "opus", "wav"])
        self.assertNotIn("audio", CORE)


class TestStatusPixel(unittest.TestCase):
    """The Feather's own LED, declared HERE because this is the only build with
    the hardware for it — ESPHome packages append lists, so a build cannot
    subtract a light its base declared, and the WROOM carrier has none."""

    def status_pixel(self, tree: dict[str, dict[str, Any]]) -> dict[str, Any]:
        pixels: list[dict[str, Any]] = [
            item
            for doc in tree.values()
            for item in doc.get("light") or []
            if item.get("id") == "status_pixel"
        ]
        (pixel,) = pixels
        return pixel

    def test_the_pixel_is_declared_here_and_only_here(self) -> None:
        pixel = self.status_pixel(FS3_TREE)
        self.assertEqual(pixel["pin"], "GPIO33")
        self.assertEqual(pixel["id"], "status_pixel")
        # Plain, not `!extend`: this build declares the pixel rather than
        # amending one an included S2 build declared with a 64-symbol block.
        self.assertIn(pixel, FS3["light"])
        # Its rail, and the interval that writes status to it, come with it.
        (rail,) = [sw for sw in FS3["switch"] if sw["id"] == "neopixel_power"]
        self.assertEqual(rail["pin"], "GPIO21")
        self.assertEqual(len(FS3["interval"]), 1)

    def test_its_block_is_one_whole_s3_channel(self) -> None:
        """48, not 64. On this chip 64 is not a block at all, and a strip that
        asks for one gets no channel and stays dark without a word."""
        self.assertEqual(self.status_pixel(FS3_TREE)["rmt_symbols"], gen_rig.S3.block)

    def test_the_packages_are_the_core_and_the_show_and_nothing_else(self) -> None:
        """No chip-override package any more: generated/lights_s3.yaml went
        with the S2, because the strips are written natively at 48 now."""
        self.assertEqual(list(FS3["packages"]), ["base", "sd"])
        self.assertEqual(
            {p.value for p in FS3["packages"].values()},
            {"castle.yaml", "castle_sd_common.yaml"},
        )

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

    def test_the_generator_is_told_about_this_line(self) -> None:
        """The reservation is the ONE thing the generated strips file cannot
        see, and an uncounted reservation is worse than no budget at all: the
        banner would read "1 block spare" with the peripheral already
        oversubscribed (grade report 2026-09-06 J2). So the hand-written number
        here and gen_rig's constant have to agree, and the banner has to name
        this file (grade report 2026-09-17 J6)."""
        self.assertEqual(gen_rig.STATUS_PIXEL_BLOCKS, 1)
        banner = gen_rig.emit_lights(LAYOUTS, ZONES, 7)
        self.assertIn("castle_feather_s3.yaml", banner)
        self.assertIn("192 of 192 symbols spent", banner)
        self.assertIn("status pixel (1 block)", banner)


if __name__ == "__main__":
    unittest.main(verbosity=2)
