"""Where a READ path lands — /sd/, /site/ and /api/files?d= — on every host.

safe_subpath (firmware/sd_web_util.h) judges a read path by its '/'
segments and hands the rest to FatFs, which has TWO separators, '/' and
'\\', and refuses * : < > ? | " and DEL in any name. Until v5.75 neither
host castle knew either thing: a POSIX host read "scenes\\vigil.mp3" as
one long file name and never found it (the board serves the scene track),
and a Windows host read "x\\..\\..\\outside.txt" as two steps up and out of
the card directory, and "C:\\..." as another drive altogether. The
emulator's fat_path (tools/castle_emu_wire.py) and the C harness's
fat_missing/map_path (tests/cxx/shim/castle_shim_fs.h) model FatFs now.
The board needed nothing: FF_FS_RPATH is 0 in ESP-IDF's ffconf.h, so ".."
is never a step up there, only a name create_name() refuses.

Two halves, and neither needs Windows to catch the old code. TestFatPath
holds fat_path to the rule with no compiler at all, through both of
pathlib's flavours. TestReadPaths puts the reads to the C and to the
emulator over identical cards that sit beside a file neither may ever
serve; a POSIX host could not walk out to it, so the half of that class
that bites everywhere is the other direction — a track reached through a
backslash, and a name the board can never find that a POSIX disk can hold.
"""

from __future__ import annotations

import itertools
import os
import sys
import unittest
import urllib.parse
from pathlib import Path, PurePosixPath, PureWindowsPath

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

import castle_emu_wire as wire
from firmware_web_harness import WebPairCase

OUTSIDE = b"not the castle's"


class TestFatPath(unittest.TestCase):
    """castle_emu_wire.fat_path alone: what the emulator looks up."""

    def test_a_backslash_is_a_separator(self) -> None:
        self.assertEqual(wire.fat_path(b"scenes\\vigil.mp3"), "scenes/vigil.mp3")
        # follow_path() skips leading separators; so does the lookup.
        self.assertEqual(wire.fat_path(b"\\wicked_winds.mp3"), "wicked_winds.mp3")
        self.assertEqual(wire.fat_path(b"\\\\server\\share\\x"), "server/share/x")

    def test_what_fatfs_never_finds_is_never_looked_up(self) -> None:
        for n in (
            b"x\\..\\..\\outside.txt",
            b"scenes\\..\\wicked_winds.mp3",  # ".." is no step even inside
            b"a\\.\\b",
            b"a/..\\b",
            b"a\\",  # a trailing separator, as "a/" already was
            b"c:x",  # a drive to Windows, a refused byte to FatFs
            b"C:\\Windows\\win.ini",
            b"wicked_winds.mp3:stream",
            b"a*b",
            b"a<b>",
            b"a|b",
            b'a"b',
            b"a\x7fb",
        ):
            with self.subTest(n=n):
                self.assertIsNone(wire.fat_path(n))

    def test_whatever_it_returns_stays_under_the_card(self) -> None:
        """Every arrangement of the bytes that could climb or re-root a
        path, read the way each host would read the answer: no anchor, no
        drive and no ".." in it, on either flavour."""
        atoms = (b"a", b"\\", b"/", b".", b"..", b":", b"C:", b"\\\\", b"x.mp3")
        for parts in itertools.product(atoms, repeat=4):
            n = b"".join(parts)
            got = wire.fat_path(n)
            if got is None:
                continue
            for flavour in (PurePosixPath, PureWindowsPath):
                p = flavour(got)
                self.assertFalse(p.anchor, (n, got, flavour.__name__))
                self.assertNotIn("..", p.parts, (n, got, flavour.__name__))


class TestReadPaths(WebPairCase):
    """The same reads to both castles, beside a file neither may serve."""

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        # Pair puts both cards in cls.tmp, so this is one step up from
        # either card root — where "x\\..\\..\\" from the root lands.
        (Path(cls.tmp) / "outside.txt").write_bytes(OUTSIDE)
        if os.name != "nt":  # NTFS reads "a:b.mp3" as file a, stream b.mp3
            for card in (cls.pair.card_c, cls.pair.card_e):
                (card / "a:b.mp3").write_bytes(b"FatFs never finds this")

    def test_a_backslash_is_a_separator_on_every_host(self) -> None:
        """The board serves scenes\\vigil.mp3 as the scene track; a POSIX
        host castle said 404 until v5.75."""
        vigil = (self.pair.card_c / "scenes" / "vigil.mp3").read_bytes()
        for target in (b"/sd/scenes%5Cvigil.mp3", b"/sd/%5Cscenes%5Cvigil.mp3"):
            r = self.same("GET", target)
            self.assertEqual((r.status, r.body), (200, vigil), target)
        r = self.same("GET", b"/site/%5Capp.js")
        self.assertEqual((r.status, r.body), (200, b"console.log(1)"))

    def test_no_backslash_walks_off_the_card(self) -> None:
        outside = str(Path(self.tmp) / "outside.txt").replace("/", "\\")
        for rel in (
            b"x%5C..%5C..%5Coutside.txt",
            b"x%5C..%5C..%5C..%5Coutside.txt",  # /site/ is one deeper
            b"scenes%5C..%5C..%5Coutside.txt",
            b"%5C..%5Coutside.txt",
            b"scenes%5C..%5Cwicked_winds.mp3",
            # The file's own absolute path: "C:\\...\\outside.txt" on Windows.
            urllib.parse.quote(outside, safe="").encode(),
        ):
            for route in (b"/sd/", b"/site/"):
                with self.subTest(target=route + rel):
                    r = self.same("GET", route + rel)
                    self.assertEqual(r.status, 404)
                    self.assertNotEqual(r.body, OUTSIDE)
        for d in (b"scenes%5C..%5C..", b"x%5C..", b"scenes%5C"):
            with self.subTest(d=d):
                r = self.same("GET", b"/api/files?d=" + d)
                self.assertEqual((r.status, r.body), (404, b"no such directory"))

    def test_a_name_fatfs_refuses_is_never_found(self) -> None:
        """On a POSIX host the cards hold a file called `a:b.mp3`, which a
        host castle served and the board can never open."""
        for rel in (
            b"a%3Ab.mp3",
            b"C%3Aoutside.txt",
            b"wicked_winds.mp3%3Astream",
            b"a%2Ab",
            b"a%3Cb%3E",
            b"a%7Cb",
            b"a%22b",
        ):
            for route in (b"/sd/", b"/site/"):
                with self.subTest(target=route + rel):
                    self.assertEqual(self.same("GET", route + rel).status, 404)
        self.assertEqual(*self.pair.cards())


if __name__ == "__main__":
    unittest.main()
