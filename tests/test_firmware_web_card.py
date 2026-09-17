"""The writing half of the castle's web API, C against emulator.

Split from tests/test_firmware_web_cxx.py on the firmware's own seam — the
one sd_web_site.h names, "bytes out" against "control in" — when the two
halves together passed the 500-line rule. The reading half (routing, the
served pages, the JSON replies, the validators) is next door; this is
everything that CHANGES the card or the flash: PUT and its sidecar, DELETE,
the free-space precondition, the site cap, the OTA size window, and what all
of them say when there is no card at all.

Both castles get their own copy of the same card and the same request, and
the reply has to be the same bytes — and so does the card afterwards. See
tests/firmware_web_harness.py for the machinery and
tests/test_firmware_web_cxx.py's header for the three exceptions this
comparison makes.
"""

from __future__ import annotations

import json
import sys
import unittest
import zlib
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

from firmware_web_harness import Pair, WebPairCase


class TestCardWrites(WebPairCase):
    """PUT and DELETE: the sidecar, the crc, and what survives a failure."""

    def test_a_put_lands_with_a_zlib_crc(self) -> None:
        for prefix, sub in (
            (b"/api/files/", ""),
            (b"/api/site/", "site/"),
            (b"/api/scenes/", "scenes/"),
        ):
            body = b"the show" * 1024
            r = self.same("PUT", prefix + b"new.mp3", body)
            self.assertEqual(
                json.loads(r.body),
                {
                    "path": f"/sd/{sub}new.mp3",
                    "bytes": len(body),
                    "crc32": "%08x" % zlib.crc32(body),
                },
            )
            self.assertEqual(*self.pair.cards())

    def test_chunk_boundaries(self) -> None:
        """write_body reads 8 KB at a time and yields every fourth chunk."""
        for n in (0, 1, 8191, 8192, 8193, 32768, 32769):
            with self.subTest(n=n):
                self.same("PUT", b"/api/files/chunk.bin", b"z" * n)
                self.assertEqual(*self.pair.cards())

    def test_a_short_upload_costs_only_the_sidecar(self) -> None:
        good = b"the previous good copy"
        self.same("PUT", b"/api/files/keep.mp3", good)
        r = self.same("PUT", b"/api/files/keep.mp3", b"half", declared=9000)
        self.assertEqual((r.status, r.body), (500, b"short write"))
        for card in (self.pair.card_c, self.pair.card_e):
            self.assertEqual((card / "keep.mp3").read_bytes(), good)
            self.assertFalse((card / "keep.mp3.part").exists())
        self.assertEqual(*self.pair.cards())

    def test_the_site_cap_is_refused_before_a_byte_is_read(self) -> None:
        r = self.same("PUT", b"/api/site/huge.html", b"", declared=8 * 1024 * 1024 + 1)
        self.assertEqual((r.status, r.body), (413, b"site file too large"))
        # The cap is the site route's alone.
        r = self.same("PUT", b"/api/files/huge.mp3", b"", declared=8 * 1024 * 1024 + 1)
        self.assertNotEqual(r.status, 413)

    def test_bad_filenames_are_refused_on_both_routes(self) -> None:
        for enc in (
            b"a%22b.mp3",
            b"a%5Cb.mp3",
            b"a%09b.mp3",
            b"a%7Fb.mp3",
            b"a%00b.mp3",
            b"a%80b.mp3",
            b"a%C3%A9.mp3",
            b".hidden",
            b"..",
            b"a%2Fb",
            b"a..b",
            b"",
            b"a" * 100,
            b"a" * 99,
        ):
            for prefix in (b"/api/files/", b"/api/site/", b"/api/scenes/"):
                with self.subTest(name=enc, prefix=prefix):
                    self.same("PUT", prefix + enc, b"x")
                    self.same("DELETE", prefix + enc)
        self.assertEqual(*self.pair.cards())

    def test_delete_reaches_all_three_directories(self) -> None:
        for prefix, sub in (
            (b"/api/files/", ""),
            (b"/api/site/", "site/"),
            (b"/api/scenes/", "scenes/"),
        ):
            self.same("PUT", prefix + b"doomed.mp3", b"x")
            for card in (self.pair.card_c, self.pair.card_e):
                self.assertTrue((card / f"{sub}doomed.mp3").exists())
            r = self.same("DELETE", prefix + b"doomed.mp3")
            self.assertEqual(json.loads(r.body), {"deleted": True})
            r = self.same("DELETE", prefix + b"doomed.mp3")
            self.assertEqual((r.status, r.body), (404, b"no such file"))
        self.assertEqual(*self.pair.cards())

    def test_an_encoded_question_mark_truncates_the_name(self) -> None:
        """name_from_uri decodes the WHOLE remaining target and only THEN
        cuts at the first '?', so an escaped one is not an escape: the
        router matched `/api/files/a%3Fb.mp3` on the undecoded path, and
        the file that lands is `a`. Worth pinning because it is the one
        place where encoding a byte changes what the name means."""
        self.same("PUT", b"/api/files/a%3Fb.mp3", b"x")
        self.same("PUT", b"/api/files/c.mp3?d=e", b"x")
        for card in (self.pair.card_c, self.pair.card_e):
            self.assertTrue((card / "a").is_file())
            self.assertFalse((card / "a?b.mp3").exists())
            self.assertTrue((card / "c.mp3").is_file())
        self.assertEqual(*self.pair.cards())


class TestNoCard(WebPairCase):
    """CASTLE_MOUNTED=0: every route that needs the card says so, and the
    ones that do not still work."""

    env: ClassVar[dict[str, str]] = {"CASTLE_MOUNTED": "0"}

    def test_the_card_routes_are_503(self) -> None:
        for method, target, body in (
            ("GET", b"/api/files", b""),
            ("GET", b"/api/files?d=scenes", b""),
            ("PUT", b"/api/files/x.mp3", b"x"),
            ("PUT", b"/api/site/x.js", b"x"),
            ("PUT", b"/api/scenes/x.mp3", b"x"),
            ("DELETE", b"/api/files/x.mp3", b""),
            ("DELETE", b"/api/site/x.js", b""),
            ("DELETE", b"/api/scenes/x.mp3", b""),
            ("GET", b"/sd/wicked_winds.mp3", b""),
        ):
            with self.subTest(target=target):
                r = self.same(method, target, body)
                self.assertEqual((r.status, r.body), (503, b"no SD card"))

    def test_the_card_free_routes_still_answer(self) -> None:
        for target in (b"/remote", b"/api/bootlog", b"/api/health"):
            self.assertEqual(self.same("GET", target).status, 200)
        # The desk page is on the card; without one, the flash page answers.
        self.assertEqual(self.same("GET", b"/").status, 200)
        self.assertEqual(self.same("GET", b"/site/app.js").status, 404)
        self.assertEqual(self.same("POST", b"/api/stop").status, 200)


class TestFreeSpace(WebPairCase):
    """write_body's 507: refuse what cannot fit, before the first byte
    (B3/E3). 64 KB of slack for FAT metadata and the sidecar."""

    env: ClassVar[dict[str, str]] = {
        "CASTLE_SD_TOTAL_KB": "1000",
        "CASTLE_SD_FREE_KB": "80",
    }

    def test_the_precondition_and_its_boundary(self) -> None:
        # 80 KB free, 64 KB of slack: the refusal starts where the integer
        # division of the declared length by 1024 first exceeds 16.
        for n, want in (
            (1, 200),
            (16 * 1024, 200),
            (17 * 1024 - 1, 200),
            (17 * 1024, 507),
            (64 * 1024, 507),
        ):
            with self.subTest(n=n):
                r = self.same("PUT", b"/api/files/fit.mp3", b"x" * n)
                self.assertEqual(r.status, want, n)
        self.assertEqual(*self.pair.cards())


class TestOta(WebPairCase):
    """PUT /api/ota's size window. The floor is 64 KB and the ceiling is
    the build's own OTA slot, so the same image is a 400 on one board and a
    flash on the other (grade report 2026-09-06 J5)."""

    def test_the_s2_slot_refuses_what_the_s3_slot_takes(self) -> None:
        image = b"\xe9" + b"\x00" * (2 * 1024 * 1024 - 1)
        r = self.same("PUT", b"/api/ota", image)
        self.assertEqual((r.status, r.body), (400, b"implausible image size"))
        tmp = Path(self.tmp)
        wide = Pair(tmp, "s3slot", CASTLE_OTA_SLOT="0x3C0000")
        try:
            c, e = wide.both("PUT", b"/api/ota", image)
            self.assertEqual((c.status, c.body), (e.status, e.body))
            self.assertEqual(json.loads(c.body), {"flashed": True, "rebooting": True})
        finally:
            wide.close()

    def test_the_floor_and_the_shapes_that_fail(self) -> None:
        for body, declared, want in (
            (b"\xe9" + b"\x00" * 65534, None, 400),  # one byte under
            (b"\xe9" + b"\x00" * 65535, None, 200),  # exactly 64 KB
            (b"", None, 400),  # nothing at all
            (b"\x00" * 70000, None, 500),  # no app magic
            (b"\xe9" + b"\x00" * 70000, 200000, 500),  # body stopped short
        ):
            with self.subTest(n=len(body), declared=declared):
                r = self.same("PUT", b"/api/ota", body, declared)
                self.assertEqual(r.status, want)


if __name__ == "__main__":
    unittest.main()
