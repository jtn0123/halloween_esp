"""The owner's half of the contract (v5.75), held by reading the firmware.

tests/test_firmware_contract.py holds the emulator to sd_web.h by parsing
it; this is the same discipline for what v5.75 added beside it, in a module
of its own because that one is at the line cap:

  * the reset words — castle_health.h's reason_str() and was_crash(), the
    shim's copy of IDF's enum, the emulator's table and the owner page's
    plain-English one must all name the same reasons, and the C's /api/health
    must equal the emulator's for every reason the chip can report;
  * the owner's page — every route it calls is one the castle registers,
    and every setting it saves is one /api/settings takes;
  * a card the castle cannot read is never reformatted, and the page that
    used to splice in the compiled-in scene ids is gone with its splice.
"""

from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

import castle_emu_health as health
import castle_emu_wire as wire
from castle_emu_flash import OWNER_PAGE
from firmware_source import FUNCS, FW, HEALTH
from firmware_web_harness import COMPILER, IN_CI, CastleC, compiled

SHIM = (ROOT / "tests" / "cxx" / "shim" / "esp_system.h").read_text(encoding="utf-8")


def shim_enum() -> dict[str, int]:
    return {n: int(v) for n, v in re.findall(r"ESP_RST_(\w+) = (\d+),", SHIM)}


def c_reasons() -> dict[int, str]:
    body = HEALTH[HEALTH.index("inline const char *reason_str()") :]
    body = body[: body.index("\n}\n")]
    enum = shim_enum()
    return {
        enum[name]: word
        for name, word in re.findall(r'case ESP_RST_(\w+): return "([^"]+)";', body)
    }


def c_crashes() -> set[str]:
    body = HEALTH[HEALTH.index("inline bool was_crash()") :]
    body = body[: body.index("\n}\n")]
    words = c_reasons()
    enum = shim_enum()
    return {words[enum[n]] for n in re.findall(r"g_reason == ESP_RST_(\w+)", body)}


class TestResetWords(unittest.TestCase):
    def test_the_shim_is_idfs_enum(self) -> None:
        # ESP-IDF 5.5's esp_reset_reason_t, in its own order. The shim is
        # what makes CASTLE_RESET=9 a brownout, so it must be the real list.
        self.assertEqual(
            list(shim_enum()),
            ["UNKNOWN", "POWERON", "EXT", "SW", "PANIC", "INT_WDT", "TASK_WDT",
             "WDT", "DEEPSLEEP", "BROWNOUT", "SDIO", "USB", "JTAG", "EFUSE",
             "PWR_GLITCH", "CPU_LOCKUP"],
        )  # fmt: skip
        self.assertEqual(list(shim_enum().values()), list(range(16)))

    def test_the_emulator_names_every_reason_the_firmware_does(self) -> None:
        self.assertEqual(health.REASONS, c_reasons())
        self.assertEqual(health.CRASHES, c_crashes())
        # Every reason IDF has, apart from "I do not know", has a word.
        self.assertEqual(set(c_reasons()), set(range(1, 16)))

    def test_the_owner_page_words_every_reason(self) -> None:
        m = re.search(r"const W=\{(.*?)\};", OWNER_PAGE, re.DOTALL)
        assert m is not None, "the owner page has no reason table"
        worded = set(re.findall(r"'([\w-]+)':'", m.group(1)))
        self.assertEqual(worded, {*health.REASONS.values(), "unknown"})

    @unittest.skipIf(COMPILER is None and not IN_CI, "no host C++ compiler")
    def test_health_is_byte_identical_for_every_reason(self) -> None:
        binary, built = compiled()
        self.assertEqual(built.returncode, 0, built.stderr)
        with tempfile.TemporaryDirectory() as card:
            for code in range(17):  # 16 is past IDF's list: "unknown"
                c = CastleC(binary, Path(card), (), CASTLE_RESET=str(code),
                            CASTLE_BOOTS="7", CASTLE_CRASHES="1")  # fmt: skip
                try:
                    got = c.http("GET", b"/api/health")
                finally:
                    c.close()
                emu: Any = SimpleNamespace(
                    boots=7,
                    crashes=1,
                    reset_reason=code,
                    health={
                        "sd_read_errors": 0,
                        "heap_min_kb": health.HEAP_MIN_KB,
                        "sd_last_error": "",
                    },
                )
                self.assertEqual(got.body.decode(), health.health_text(emu), code)


class TestOwnerPageContract(unittest.TestCase):
    def test_every_route_the_page_calls_is_registered(self) -> None:
        called = set(re.findall(r"'(/(?:api|owner|remote)[\w/-]*)[?']", OWNER_PAGE))
        self.assertGreaterEqual(len(called), 14, called)
        paths = {p for p, _m, _h in wire.ROUTES}
        for path in called:
            self.assertIn(path, paths)

    def test_every_setting_the_page_saves_is_one_the_castle_takes(self) -> None:
        saved = set(re.findall(r"set\('(\w+)=", OWNER_PAGE))
        taken = re.search(r"query_ok\(req, \{([^}]*)\}", FUNCS["h_settings"])
        assert taken is not None
        self.assertEqual(saved, set(re.findall(r'"(\w+)"', taken.group(1))))

    def test_the_page_offers_only_zones_the_castle_accepts(self) -> None:
        import castle_emu_tz as tz

        zones = re.findall(r"\['[^']+','([^']+)'\]", OWNER_PAGE)
        self.assertEqual(len(zones), 15)
        for z in zones:
            self.assertIsNotNone(tz.parse(z), z)


class TestCardSafety(unittest.TestCase):
    def test_a_card_that_will_not_mount_is_never_reformatted(self) -> None:
        sd = (FW / "sd_audio.h").read_text(encoding="utf-8")
        self.assertIn("mcfg.format_if_mount_failed = false;", sd)
        self.assertNotIn("format_if_mount_failed = true", sd)

    def test_the_spliced_fallback_page_is_gone_with_its_splice(self) -> None:
        fallback = (FW / "generated" / "fallback_scenes.h").read_text(encoding="utf-8")
        self.assertNotIn("kFallbackSceneIds[]", fallback)
        self.assertIn("kFallbackSceneIdsCsv[]", fallback)
        site = (FW / "sd_web_site.h").read_text(encoding="utf-8")
        self.assertNotIn("kFallbackPage", site)


if __name__ == "__main__":
    unittest.main()
