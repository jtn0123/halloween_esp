"""tools/unit_label.py — the label says what the castle will say.

The two names on a label are computed in two places that never meet: the
castle derives them from its MAC at boot, in C (firmware/castle_buyer.h for
the hotspot, ESPHome's own name_add_mac_suffix for the hostname), and the
label tool derives them in Python on the seller's desk. TestFirmwareSource
reads the firmware's C and YAML and the installed ESPHome's source — the
pinned version that builds the image — and holds the tool to each, so a
change to either derivation fails here rather than on a buyer's phone. The
rest drives the tool against castle_emu and a fake mDNS responder on
loopback: nothing here leaves the machine.

No MAC address is written out in this file (tools/ship_guard.py scans the
tree for them): the examples are built from hex at run time.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import castle_emu
import esphome
import helpers  # noqa: F401  (the sandbox scrub)
import qr_code
import unit_label as ul
from esphome.const import __version__ as ESPHOME_VERSION
from test_castle_find import FakeResponder, castle_answer
from test_firmware_s3 import load

MAC = bytes.fromhex("a4cf12b2c3d4")
COLONS = ":".join(f"{b:02x}" for b in MAC)
ESPHOME = Path(esphome.__file__).resolve().parent


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestFirmwareSource(unittest.TestCase):
    def test_the_hotspot_is_castle_buyer_hs_snprintf(self) -> None:
        src = read(ROOT / "firmware" / "castle_buyer.h")
        self.assertIn("esphome::get_mac_address_raw(mac);", src)
        m = re.search(
            r'snprintf\(ssid,\s*sizeof\(ssid\),\s*"([^"]*)",\s*mac\[(\d)\],\s*mac\[(\d)\]\);',
            src,
        )
        assert m is not None, "castle_buyer.h no longer names the AP by snprintf"
        fmt, a, b = m.group(1), int(m.group(2)), int(m.group(3))
        self.assertEqual(fmt, ul.AP_PREFIX + "%02X%02X")
        self.assertEqual((a, b), ul.AP_BYTES)
        # The C format, run: Python's % is printf's for %02X.
        self.assertEqual(fmt % (MAC[a], MAC[b]), ul.from_mac(MAC).hotspot)
        self.assertEqual(ul.from_mac(MAC).hotspot, "Castle-C3D4")

    def test_the_hotspot_has_no_password(self) -> None:
        ap = load("castle_buyer.yaml")["wifi"]["ap"]
        self.assertNotIn("password", ap)
        self.assertIn("No password", ul.page(ul.from_mac(MAC)))

    def test_the_name_is_the_buyer_builds_stem_with_esphomes_suffix(self) -> None:
        buyer = load("castle_buyer.yaml")
        self.assertEqual(buyer["substitutions"]["name"], ul.NAME_STEM)
        self.assertIs(buyer["esphome"]["name_add_mac_suffix"], True)
        self.assertEqual(load("castle.yaml")["esphome"]["name"], "${name}")

    def test_esphome_appends_the_macs_last_six_lowercase_hex_digits(self) -> None:
        config = read(ESPHOME / "core" / "config.py")
        self.assertIn(f'_APP_NAME_MAC_SEP = "{ul.NAME_SEP}"', config)
        self.assertIn('_MAC_SUFFIX_PLACEHOLDER = "XXXXXX"', config)
        app = read(ESPHOME / "core" / "application.h")
        self.assertIn("constexpr size_t mac_address_suffix_len = 6;", app)
        self.assertIn("get_mac_address_into_buffer(mac_addr);", app)
        self.assertIn(
            "memcpy(name + name_len - mac_address_suffix_len, "
            "mac_addr + mac_address_suffix_len, mac_address_suffix_len);",
            app,
        )
        helpers_cpp = read(ESPHOME / "core" / "helpers.cpp")
        body = helpers_cpp.split("void get_mac_address_into_buffer", 1)[1][:300]
        self.assertIn("format_mac_addr_lower_no_sep(mac, buf.data());", body)
        self.assertIn(
            "const char *hostname = App.get_name().c_str();",
            read(ESPHOME / "components" / "mdns" / "mdns_esp32.cpp"),
        )
        # The algorithm those lines are: the placeholder overwritten by
        # characters 6..11 of the twelve-digit lower-case MAC.
        twelve = MAC.hex()
        name = ul.NAME_STEM + ul.NAME_SEP + "XXXXXX"
        self.assertEqual(name[:-6] + twelve[6:12], ul.from_mac(MAC).name)
        self.assertEqual(ul.from_mac(MAC).name, "castle-b2c3d4")

    def test_that_esphome_is_the_one_that_builds_the_firmware(self) -> None:
        pin = re.search(
            r"^esphome==(\S+)$", read(ROOT / "requirements.txt"), re.MULTILINE
        )
        assert pin is not None
        self.assertEqual(ESPHOME_VERSION, pin.group(1))

    def test_the_urls_are_the_guides_and_the_flashers(self) -> None:
        guide = read(ROOT / "docs" / "OWNER-GUIDE.md")
        self.assertIn(ul.FLASHER_URL, guide)
        self.assertIn(ul.APP_URL, guide)
        self.assertTrue(ul.GUIDE_URL.endswith("/blob/main/docs/OWNER-GUIDE.md"))


class TestMac(unittest.TestCase):
    def test_every_way_esptool_or_a_person_writes_one(self) -> None:
        for text in (COLONS, COLONS.upper(), COLONS.replace(":", "-"), MAC.hex(),
                     f" {MAC.hex()[:4]}.{MAC.hex()[4:8]}.{MAC.hex()[8:]} "):  # fmt: skip
            self.assertEqual(ul.parse_mac(text), MAC, text)

    def test_anything_else_is_refused_in_words(self) -> None:
        for text in ("", MAC.hex()[:10], MAC.hex() + "00", "zz" * 6):
            with self.assertRaisesRegex(ul.LabelError, "twelve hex digits"):
                ul.parse_mac(text)


class TestPage(unittest.TestCase):
    def test_the_sheet_says_both_names_both_urls_and_carries_the_qr(self) -> None:
        unit = ul.from_mac(MAC, "CASTLE-2026-001")
        out = ul.page(unit)
        for want in ("Castle-C3D4", "http://castle-b2c3d4.local", "/owner",
                     ul.FLASHER_URL, "CASTLE-2026-001", "@page sticker",
                     "size: 4in 6in"):  # fmt: skip
            self.assertIn(want, out)
        self.assertEqual(out.count("<svg"), 2)  # the card's and the sticker's
        self.assertIn(qr_code.svg(qr_code.encode(ul.GUIDE_URL.encode(), "M"),
                                  label="Owner's guide"), out)  # fmt: skip
        self.assertNotIn(
            "http", re.sub(r"https?://[^\s<\"]+", "", out).split("<style>")[0]
        )

    def test_a_serial_is_escaped_and_a_missing_one_leaves_no_line(self) -> None:
        out = ul.page(ul.from_mac(MAC, "<b>&"))
        self.assertIn("&lt;b&gt;&amp;", out)
        self.assertNotIn('class="serial"', ul.page(ul.from_mac(MAC)))

    def test_the_record_lines_are_the_support_templates(self) -> None:
        text = ul.record(ul.Unit(MAC[3:], MAC, "5.76", "feather-s3-4m2p", "CASTLE-1"))
        self.assertIn("Unit:            CASTLE-1", text)
        self.assertIn("MAC:             " + COLONS.upper(), text)
        self.assertIn("Setup hotspot:   Castle-C3D4   (open — no password)", text)
        self.assertIn("Castle name:     castle-b2c3d4.local", text)
        self.assertIn("Board / carrier: feather-s3-4m2p", text)
        self.assertIn("Firmware:        castle v5.76", text)
        self.assertEqual(ul.record(ul.Unit(MAC[3:])).count("\n"), 1)

    def test_every_record_line_is_a_line_of_supports_template(self) -> None:
        section = read(ROOT / "docs" / "SUPPORT.md").split("## The per-unit record", 1)[
            1
        ]
        template = next(
            b for b in section.split("```")[1::2] if b.startswith("\nUnit:")
        )
        full = ul.record(ul.Unit(MAC[3:], MAC, "5.76", "feather-s3-4m2p", "CASTLE-1"))
        for line in full.splitlines():
            self.assertIn("\n" + line[:17], template, line)
        self.assertIn("unit_label.py", read(ROOT / "docs" / "SUPPORT.md"))


class TestHost(unittest.TestCase):
    def emulator(self, variant: str = "buyer") -> str:
        card = tempfile.TemporaryDirectory()
        self.addCleanup(card.cleanup)
        emu = castle_emu.CastleEmu(port=0, sd_dir=Path(card.name), fw_variant=variant)
        emu.start()
        self.addCleanup(emu.server_close)
        self.addCleanup(emu.shutdown)
        return f"127.0.0.1:{emu.server_address[1]}"

    def responder(self, host: str) -> FakeResponder:
        port = int(host.rsplit(":", 1)[1])
        fake = FakeResponder(
            lambda _q: [castle_answer("castle-b2c3d4", port, "127.0.0.1")]
        )
        self.addCleanup(fake.close)
        return fake

    def test_a_buyer_castle_by_address_is_named_by_its_mdns_answer(self) -> None:
        host = self.emulator()
        fake = self.responder(host)
        unit = ul.from_host(host, "CASTLE-7", group=fake.addr, timeout=0.5)
        self.assertEqual((unit.hotspot, unit.name), ("Castle-C3D4", "castle-b2c3d4"))
        self.assertTrue(unit.version)
        self.assertEqual(unit.board, "feather-s3-4m2p")
        self.assertEqual(unit.serial, "CASTLE-7")
        self.assertIsNone(unit.mac)

    def test_a_castle_asked_by_its_name_needs_no_browse(self) -> None:
        status = {"version": "5.76", "scenes": "", "fw_variant": "buyer"}
        with mock.patch.object(ul.castle_find, "browse") as browse:
            unit = ul.from_host("castle-B2C3D4.local", ask=lambda _h: status)
        browse.assert_not_called()
        self.assertEqual(unit.hotspot, "Castle-C3D4")

    def test_the_yard_build_is_refused(self) -> None:
        with self.assertRaisesRegex(ul.LabelError, "runs the yard build"):
            ul.from_host(self.emulator("yard"))

    def test_nothing_answering_is_said(self) -> None:
        with self.assertRaisesRegex(ul.LabelError, "Nothing at 10.0.0.9 answered"):
            ul.from_host("10.0.0.9", ask=lambda _h: None)

    def test_a_castle_whose_name_mdns_does_not_give_asks_for_the_mac(self) -> None:
        host = self.emulator()
        fake = FakeResponder(lambda _q: [])
        self.addCleanup(fake.close)
        with self.assertRaisesRegex(ul.LabelError, "give --mac"):
            ul.from_host(host, group=fake.addr, timeout=0.3)


class TestMain(unittest.TestCase):
    def run_main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = ul.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_a_mac_makes_the_sheet_and_prints_the_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "label.html"
            code, out, _ = self.run_main(
                "--mac", COLONS, "--unit", "U1", "-o", str(target)
            )
            self.assertEqual(code, 0)
            self.assertIn("Castle-C3D4", target.read_text(encoding="utf-8"))
        self.assertIn("Castle name:     castle-b2c3d4.local", out)
        self.assertIn("2.25x1.25 in sticker", out)

    def test_the_default_file_is_named_after_the_castle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp):
            self.assertEqual(self.run_main("--mac", MAC.hex())[0], 0)
            self.assertTrue((Path(tmp) / "castle-label-castle-b2c3d4.html").is_file())

    def test_a_host_is_asked_through_the_same_door(self) -> None:
        case = TestHost("test_the_yard_build_is_refused")
        host = case.emulator()
        fake = case.responder(host)
        self.addCleanup(case.doCleanups)
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(ul, "MDNS_GROUP", fake.addr):  # fmt: skip
            code, out, _ = self.run_main("--host", host, "-o", f"{tmp}/l.html")
        self.assertEqual(code, 0)
        self.assertIn("Firmware:        castle v", out)

    def test_a_failure_is_one_line_and_exit_1(self) -> None:
        code, out, err = self.run_main("--mac", "nope")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("unit_label: 'nope' is not a MAC address", err)


if __name__ == "__main__":
    unittest.main()
