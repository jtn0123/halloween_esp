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

    def test_the_suffixes_the_upload_makes_itself_cannot_be_uploaded(self) -> None:
        """J5 (grade report 2026-09-17): a PUT of `X` writes `X.part` and, on
        success, unlinks `X.old`. So `song.mp3.part` and `song.mp3.old` are
        names this route will destroy on somebody else's behalf — the first
        while an upload of `song.mp3` is in flight, the second the next time
        one finishes. Refused at the door on every route, and the REAL files
        of those names are still there afterwards, which is the whole claim.
        """
        for card in (self.pair.card_c, self.pair.card_e):
            (card / "song.mp3.part").write_bytes(b"somebody else's bytes")
            (card / "song.mp3.old").write_bytes(b"the previous release")
        for enc in (b"song.mp3.part", b"song.mp3.old", b"x.part", b"x.old"):
            for prefix in (b"/api/files/", b"/api/site/", b"/api/scenes/"):
                with self.subTest(name=enc, prefix=prefix):
                    r = self.same("PUT", prefix + enc, b"clobber")
                    self.assertEqual((r.status, r.body), (400, b"reserved suffix"))
        for card in (self.pair.card_c, self.pair.card_e):
            self.assertEqual(
                (card / "song.mp3.part").read_bytes(), b"somebody else's bytes"
            )
            self.assertEqual(
                (card / "song.mp3.old").read_bytes(), b"the previous release"
            )
        # DELETE is NOT refused: a file of either name that is already on the
        # card is exactly what an operator needs to be able to remove.
        r = self.same("DELETE", b"/api/files/song.mp3.old")
        self.assertEqual(json.loads(r.body), {"deleted": True})
        self.assertEqual(*self.pair.cards())

    def test_a_name_fat_would_alter_or_refuse_is_refused_at_the_door(self) -> None:
        """v5.75 (PR #65's Windows run, seed 1): FatFs strips a trailing space
        or dot and refuses * : < > ? |, so `PUT /api/files/%20` reached the
        rename and failed there — a 500 "rename failed" on the board and on
        an NTFS host, a 200 and a file called " " on a Mac — and
        `song.mp3.` replaced `song.mp3` on the card. Both castles now refuse
        all of these with the 400 every other bad name gets, before a byte is
        read, on every route; and `x.mp3.part.` no longer walks past the
        reserved-suffix check to land on the sidecar it names."""
        for card in (self.pair.card_c, self.pair.card_e):
            (card / "song.mp3").write_bytes(b"the real song")
            (card / "x.mp3.part").write_bytes(b"an upload in flight")
        for enc in (b"%20", b"%20%20%20", b"+", b"song.mp3.", b"song.mp3%20",
                    b"song.mp3.%20.", b"x.mp3.part.", b"a%3Ab.mp3", b"a*b",
                    b"a%3Cb%3E", b"a%7Cb"):  # fmt: skip
            for prefix in (b"/api/files/", b"/api/site/", b"/api/scenes/"):
                with self.subTest(name=enc, prefix=prefix):
                    r = self.same("PUT", prefix + enc, b"clobber")
                    self.assertEqual((r.status, r.body), (400, b"bad filename"))
                    r = self.same("DELETE", prefix + enc)
                    self.assertEqual((r.status, r.body), (400, b"bad filename"))
            r = self.same("POST", b"/api/play?f=" + enc)
            self.assertEqual((r.status, r.body), (400, b"need ?f=<file>"), enc)
        for card in (self.pair.card_c, self.pair.card_e):
            self.assertEqual((card / "song.mp3").read_bytes(), b"the real song")
            self.assertEqual((card / "x.mp3.part").read_bytes(), b"an upload in flight")
        # Inner spaces and dots are FAT's to keep, and are kept.
        r = self.same("PUT", b"/api/files/a%20b.c.mp3", b"kept")
        self.assertEqual(json.loads(r.body)["path"], "/sd/a b.c.mp3")
        self.assertEqual(*self.pair.cards())

    def test_a_folder_is_not_a_file_to_replace_or_remove(self) -> None:
        """v5.75: an upload moves whatever holds its name aside as `.old`,
        and FatFs renames a directory as readily as a file — so
        `PUT /api/files/scenes` answered 200 on the board with the whole
        show parked as `scenes.old` and one file in its place (the emulator
        did the same and then said 500). DELETE of a folder was 200 on the
        board when it was empty and "no such file" when it was not, and 404
        on both host castles either way. Both verbs are 409 "is a folder"
        now, on every route, before a byte of the body is read — and the
        show is exactly where it was."""
        for card in (self.pair.card_c, self.pair.card_e):
            (card / "scenes" / "show.man").write_bytes(b"the show")
            (card / "scenes" / "spare").mkdir()
            (card / "site" / "fonts").mkdir()
        before = self.pair.cards()
        for target in (
            b"/api/files/scenes",  # the show itself
            b"/api/files/site",  # the desk
            b"/api/files/logs",  # empty: the board's f_unlink took these
            b"/api/scenes/spare",
            b"/api/site/fonts",
        ):
            with self.subTest(target=target):
                r = self.same("PUT", target, b"not a show")
                self.assertEqual((r.status, r.body), (409, b"is a folder"))
                # Refused on the headers: a body that never comes is not
                # waited for, so no byte of it was read.
                r = self.same("PUT", target, b"", declared=4 * 1024 * 1024)
                self.assertEqual((r.status, r.body), (409, b"is a folder"))
                r = self.same("DELETE", target)
                self.assertEqual((r.status, r.body), (409, b"is a folder"))
        self.assertEqual(self.pair.cards(), before)
        for card in (self.pair.card_c, self.pair.card_e):
            self.assertEqual((card / "scenes" / "show.man").read_bytes(), b"the show")
            self.assertTrue((card / "scenes" / "vigil.mp3").is_file())
            self.assertTrue((card / "logs").is_dir())
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

    def test_the_feather_slot_refuses_what_the_carrier_slot_takes(self) -> None:
        image = b"\xe9" + b"\x00" * (2 * 1024 * 1024 - 1)
        r = self.same("PUT", b"/api/ota", image)
        self.assertEqual((r.status, r.body), (400, b"implausible image size"))
        tmp = Path(self.tmp)
        wide = Pair(tmp, "carrierslot", CASTLE_OTA_SLOT="0x3C0000")
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
