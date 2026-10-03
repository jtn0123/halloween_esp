"""A castle with no card, a card it cannot read, or a card with no show on it
still boots, still answers, and says what is wrong (v5.75, PRODUCTION-TODO
§1.3) — proved on the real C, under AddressSanitizer and UBSan where the
compiler has them.

tests/cxx/web_check.cpp `--boot` runs castle_sd_common.yaml's on_boot in its
own order before it serves: mount, seed the scene ids, castle_web::start(),
the manifest check, the opening scene. A boot loop on the board is a fault in
that path — a null pointer, a read past a buffer, an abort — and the
sanitizers turn every one of those into a dead process here, which the first
request would then report. So each case below boots a castle over a card in
one bad state and asks it the questions the owner's page asks.

What the shim cannot tell apart, said once: "no card" and "a card FATFS
cannot mount" are the same call failing on the board (esp_vfs_fat_sdspi_mount
with format_if_mount_failed false — test_firmware_contract_owner.py holds that
flag), so they are the same case here.
"""

from __future__ import annotations

import atexit
import functools
import json
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

import cue_file
import scene_manifest
from firmware_web_harness import COMPILER, IN_CI, CastleC, Reply, build, compiled

FALLBACK = (ROOT / "firmware" / "generated" / "fallback_scenes.h").read_text(
    encoding="utf-8"
)
FALLBACK_IDS = FALLBACK.split('kFallbackSceneIdsCsv[] = "')[1].split('"')[0].split(",")
SANITIZE = ("-fsanitize=address,undefined", "-fno-sanitize-recover=all",
            "-fno-omit-frame-pointer")  # fmt: skip
#: The scene a healthy card opens with, and its files.
STORM = {
    "id": "storm",
    "duration_ms": 8000,
    "volume": 0.9,
    "base": {"towerL": "candle"},
}


@functools.cache
def binary() -> Path:
    """web_check, sanitized where the toolchain can (clang and gcc on macOS
    and Linux; MinGW on the Windows runner cannot), else the plain build."""
    if sys.platform != "win32":
        tmp = tempfile.mkdtemp(prefix="castle-boot-")
        atexit.register(shutil.rmtree, tmp, True)
        out = Path(tmp) / "web_check_asan"
        if build(out, *SANITIZE).returncode == 0:
            return out
    plain, built = compiled()
    assert built.returncode == 0, built.stderr
    return plain


def manifest_cases() -> dict[str, bytes | None]:
    """A card's /scenes/show.man in each state worth booting over. None is
    "a directory where the file should be"."""
    good = scene_manifest.encode([STORM])
    unterminated = scene_manifest.HEADER.pack(
        scene_manifest.MAGIC, scene_manifest.VERSION, 1, 0, scene_manifest.ENTRY.size, 0
    ) + scene_manifest.ENTRY.pack(b"A" * 40, b"B" * 48, 8000, 90, 0, 0)
    return {
        "empty": b"",
        "noise": bytes((i * 131 + 7) % 256 for i in range(1024)),
        "truncated": good[:-1],
        "a byte too long": good + b"\x00",
        "255 scenes claimed": good[:5] + struct.pack("<B", 255) + good[6:16],
        "an id with no end": unterminated,
        "a directory": None,
    }


@unittest.skipIf(COMPILER is None and not IN_CI, "no host C++ compiler")
class TestBootOverABadCard(unittest.TestCase):
    def setUp(self) -> None:
        if COMPILER is None:
            raise AssertionError("CI is set and no host C++ compiler is on PATH")
        self.tmp = Path(tempfile.mkdtemp(prefix="castle-boot-card-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def boot(self, card: Path, **env: str) -> CastleC:
        c = CastleC(binary(), card, ("--boot",), CASTLE_SCENE_IDS="", **env)
        self.addCleanup(c.close)
        return c

    def ask(self, c: CastleC, method: str, target: bytes) -> Reply:
        r = c.http(method, target)  # raises if the castle died booting
        self.assertIsNone(c.proc.poll(), f"web_check exited on {target!r}")
        return r

    def status(self, c: CastleC) -> dict[str, object]:
        r = self.ask(c, "GET", b"/api/status")
        self.assertEqual(r.status, 200)
        body: dict[str, object] = json.loads(r.body)
        return body

    def answers_the_owner(self, c: CastleC) -> None:
        """What the owner's page asks, every one answered."""
        for target in (b"/owner", b"/api/health", b"/api/events", b"/api/bootlog"):
            self.assertEqual(self.ask(c, "GET", target).status, 200, target)
        self.assertEqual(self.ask(c, "GET", b"/").status, 200)
        self.assertEqual(self.ask(c, "POST", b"/api/blackout").status, 200)
        c.tick(2_000_000)
        self.assertEqual(
            self.ask(c, "POST", b"/api/scene?s=" + FALLBACK_IDS[0].encode()).status, 200
        )
        self.assertEqual(c.tick(2_200_000)[0], "SCENE")
        self.status(c)

    def test_no_card_boots_on_the_built_in_show(self) -> None:
        empty = self.tmp / "nothing"
        empty.mkdir()  # no card: nothing under /sd at all
        c = self.boot(empty, CASTLE_MOUNTED="0")
        st = self.status(c)
        self.assertFalse(st["sd_mounted"])
        self.assertEqual(st["scenes"], ",".join([*FALLBACK_IDS, "stop"]))
        # The opening scene ran — on the built-in look — and says it had no
        # card to read: the castle glows rather than sits dark.
        self.assertEqual(
            (st["scene"], st["missing"]), (FALLBACK_IDS[0], FALLBACK_IDS[0])
        )
        self.assertEqual(self.ask(c, "GET", b"/api/files").body, b"no SD card")
        page = self.ask(c, "GET", b"/")
        self.assertIn(b"No SD card</b> \xe2\x80\x94 the show is on the card", page.body)
        self.answers_the_owner(c)
        self.assertEqual(list(empty.iterdir()), [])  # nothing was written

    def test_a_software_restart_with_no_card_stays_quiet(self) -> None:
        empty = self.tmp / "nothing"
        empty.mkdir()
        st = self.status(self.boot(empty, CASTLE_MOUNTED="0", CASTLE_RESET="3"))
        self.assertEqual((st["scene"], st["missing"]), ("stop", ""))

    def test_a_card_with_no_show_on_it_says_so(self) -> None:
        card = self.tmp / "songs-only"
        card.mkdir()
        (card / "wicked_winds.mp3").write_bytes(b"\xff\xfb" + b"\x00" * 4094)
        c = self.boot(card, CASTLE_MOUNTED="1")
        st = self.status(c)
        self.assertTrue(st["sd_mounted"])
        self.assertEqual(str(st["missing"]).split(","), ["show.man", FALLBACK_IDS[0]])
        self.assertIn(b"wicked_winds.mp3", self.ask(c, "GET", b"/api/files").body)
        self.assertTrue((card / "logs" / "castle.log").is_file())  # the boot line
        self.answers_the_owner(c)

    def test_every_bad_manifest_boots_and_says_so(self) -> None:
        for name, blob in manifest_cases().items():
            with self.subTest(name):
                card = self.tmp / name.replace(" ", "_")
                (card / "scenes").mkdir(parents=True)
                if blob is None:
                    (card / "scenes" / "show.man").mkdir()
                else:
                    (card / "scenes" / "show.man").write_bytes(blob)
                c = self.boot(card, CASTLE_MOUNTED="1")
                st = self.status(c)
                self.assertEqual(st["scenes"], ",".join([*FALLBACK_IDS, "stop"]))
                self.assertIn(FALLBACK_IDS[0], str(st["missing"]).split(","))
                self.answers_the_owner(c)

    def test_a_good_card_boots_clean(self) -> None:
        """The control: the same boot over a whole show says nothing is
        missing — so the cases above fail for their card, not the path."""
        card = self.tmp / "good"
        (card / "scenes").mkdir(parents=True)
        (card / "scenes" / "show.man").write_bytes(scene_manifest.encode([STORM]))
        (card / "scenes" / "01_storm.mp3").write_bytes(b"\xff\xfb" + b"\x00" * 2046)
        (card / "scenes" / "storm.cue").write_bytes(
            cue_file.encode(STORM, [], ["towerL", "towerR", "door"])
        )
        st = self.status(self.boot(card, CASTLE_MOUNTED="1"))
        self.assertEqual(
            (st["scenes"], st["scene"], st["missing"]), ("storm,stop", "storm", "")
        )


if __name__ == "__main__":
    unittest.main()
