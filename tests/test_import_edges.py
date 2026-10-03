"""The imports an owner will try that a developer would not: a full disk, a
document instead of a song, a two-hour file, a name in any alphabet, a song
on a share or a CD. Each ends in one sentence the owner can act on — and
none leaves a half-imported track behind for the desk to offer.

Portable on purpose (the Windows CI job runs this file): paths are pathlib,
the disk is "filled" by making a write fail rather than by filling it, and a
read-only file is the one attribute every platform honours.
"""

from __future__ import annotations

import contextlib
import errno
import io
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import import_args as ia
import import_convert as ic
import import_fetch as imf
import import_reason as ir
import import_track as it
import manifest as mf
from helpers import make_click_track

NO_SPACE = OSError(errno.ENOSPC, "No space left on device")


def done(code: int = 0, out: str = "", err: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=code, stdout=out, stderr=err)


def writable(p: Path) -> None:
    os.chmod(p, stat.S_IWRITE | stat.S_IREAD | (stat.S_IEXEC if p.is_dir() else 0))


class EdgeCase(unittest.TestCase):
    """The library, its manifest and the kept-sources folder in a tempdir —
    all three bindings, or a test writes into a real library."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.lib = self.tmp / "lib"
        self.lib.mkdir()
        self.src = self.tmp / "src.wav"
        make_click_track(self.src, seconds=2.0)
        for target, name, value in (
            (it, "TRACKS", self.lib),
            (ic, "TRACKS", self.lib),
            (mf, "PATH", self.lib / "tracks.json"),
        ):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        for p in [self.tmp, *self.tmp.rglob("*")]:
            with contextlib.suppress(OSError):
                writable(p)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *args: str) -> tuple[int, str]:
        """main() as the studio runs it; stderr (the detail lines) kept."""
        err = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["import_track.py", *args]),
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(err),
        ):
            try:
                return it.main(), err.getvalue()
            except SystemExit as e:
                self.said, self.detail = str(e.code), err.getvalue()
                raise

    def refused(self, *args: str) -> str:
        with self.assertRaises(SystemExit):
            self.run_cli(*args)
        return self.said

    def fetch(self, run: Any, dest: Path, whole: bool = True) -> tuple[Any, str]:
        """fetch_url against a fake yt-dlp (`run` is subprocess.run's
        stand-in); returns the fake, for its argv, and the title."""
        with (
            mock.patch.object(imf, "_ytdlp", return_value="yt-dlp"),
            mock.patch.object(subprocess, "run", side_effect=run) as fake,
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            try:
                return fake, imf.fetch_url("https://example.test/a", dest, whole)[1]
            except SystemExit as e:
                self.fake, self.said = fake, str(e.code)
                raise

    def library(self) -> list[str]:
        return sorted(p.name for p in self.lib.iterdir() if p.suffix != ".lock")


class TestDiskFull(EdgeCase):
    def test_a_full_disk_at_the_last_step_says_so_and_leaves_nothing(self) -> None:
        with mock.patch.object(mf, "record", side_effect=NO_SPACE):
            self.assertEqual(self.refused(str(self.src), "--id", "full"), ir.DISK_FULL)
        self.assertIn("No space left on device", self.detail)
        self.assertNotIn("full.mp3", self.library())
        self.assertIsNone(mf.get("full"))

    def test_a_full_disk_while_keeping_the_source(self) -> None:
        win = OSError(errno.ENOSPC, "There is not enough space on the disk")
        win.winerror = 112  # type: ignore[attr-defined]
        with mock.patch.object(shutil, "copyfile", side_effect=win):
            said = self.refused(str(self.src), "--id", "kept", "--keep-source")
        self.assertEqual(said, ir.DISK_FULL)
        self.assertNotIn("kept.mp3", self.library())

    def test_ffmpeg_running_out_of_room_is_not_called_bad_audio(self) -> None:
        out = self.lib / "x.mp3"
        opts: dict[str, Any] = {
            "normalize": False,
            "gain_db": None,
            "fade_in": None,
            "fade_out": None,
            "take": None,
            "start": 0,
            "channels": 2,
            "sample_rate": 44100,
            "bitrate": 128,
            "format": "mp3",
        }
        tail = "[out#0/mp3 @ 0x1] Error writing trailer: No space left on device"
        with (
            mock.patch.object(subprocess, "run", return_value=done(1, err=tail)),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as cm,
        ):
            ic.convert(self.src, out, opts)
        self.assertEqual(str(cm.exception), ir.DISK_FULL)
        self.assertEqual(self.library(), [])  # no .part left either

    def test_the_downloader_running_out_of_room(self) -> None:
        err = "ERROR: unable to write data: [Errno 28] No space left on device"
        with self.assertRaises(SystemExit):
            self.fetch(lambda *a, **k: done(1, err=err), self.tmp)
        self.assertEqual(self.said, ir.DISK_FULL)


class TestNotASong(EdgeCase):
    def test_a_document_is_named_and_refused(self) -> None:
        doc = self.tmp / "letter.docx"
        doc.write_bytes(b"PK\x03\x04 this is a letter, not a song" * 50)
        self.assertEqual(self.refused(str(doc)), ic.not_audio("letter.docx"))
        self.assertEqual(self.library(), [])


class TestLength(EdgeCase):
    def probe(self, src: float | None, cut: float | None = None) -> Any:
        real = ic.probe_duration

        def fake(p: Path) -> float | None:
            if p == self.src:
                return src
            return cut if cut is not None else real(p)

        return mock.patch.object(it, "probe_duration", side_effect=fake)

    def test_a_two_hour_file_is_refused_before_anything_converts(self) -> None:
        with self.probe(7200.0), mock.patch.object(it, "convert") as convert:
            said = self.refused(str(self.src), "--id", "long")
        convert.assert_not_called()
        self.assertEqual(said, ic.too_long("src.wav", 7200.0))
        self.assertIn("2:00:00", said)
        self.assertIn("15 minutes", said)

    def test_a_short_take_from_a_two_hour_file_imports(self) -> None:
        with self.probe(7200.0):
            code, _ = self.run_cli(str(self.src), "--id", "part", "--take", "1")
        self.assertEqual(code, 0)
        self.assertIn("part.mp3", self.library())

    def test_a_long_cut_is_caught_when_the_source_could_not_be_measured(self) -> None:
        with self.probe(None, cut=7200.0):
            said = self.refused(str(self.src), "--id", "unmeasured")
        self.assertEqual(said, ic.too_long("src.wav", 7200.0))
        self.assertNotIn("unmeasured.mp3", self.library())

    def test_a_link_asks_the_downloader_to_refuse_a_long_video(self) -> None:
        skipped = "[download] Long Thing does not pass filter (...), skipping .."
        with self.assertRaises(SystemExit):
            self.fetch(lambda *a, **k: done(0, out=skipped), self.tmp)
        self.assertEqual(self.said, imf.TOO_LONG_LINK)
        argv = self.fake.call_args.args[0]
        limit = argv[argv.index("--match-filters") + 1]
        self.assertEqual(limit, f"!is_live & duration <=? {ic.MAX_IMPORT_SECONDS}")
        self.assertLess(argv.index("--match-filters"), argv.index("--"))

    def test_with_a_take_only_live_streams_are_refused_up_front(self) -> None:
        with self.assertRaises(SystemExit):
            self.fetch(lambda *a, **k: done(0), self.tmp, whole=False)
        self.assertEqual(self.said, ir.NO_AUDIO)
        argv = self.fake.call_args.args[0]
        self.assertEqual(argv[argv.index("--match-filters") + 1], "!is_live")

    def test_lengths_read_the_way_a_player_shows_them(self) -> None:
        self.assertEqual(ic.clock(7200), "2:00:00")
        self.assertEqual(ic.clock(65.4), "1:05")
        self.assertEqual(ic.longest(), "15 minutes")


class TestNames(EdgeCase):
    def copy_as(self, name: str, folder: str) -> Path:
        (self.tmp / folder).mkdir()
        dest = self.tmp / folder / name
        shutil.copyfile(self.src, dest)
        return dest

    def test_a_decomposed_and_a_composed_name_are_one_track(self) -> None:
        mac = self.copy_as("Cafe\u0301 Noir.wav", "mac")  # NFD, as a Mac may hand it
        win = self.copy_as("Caf\u00e9 Noir.wav", "win")  # NFC, as Windows does
        for src in (mac, win):
            self.assertEqual(self.run_cli(str(src))[0], 0)
        self.assertEqual(self.library(), ["cafe_noir.mp3", "tracks.json"])
        self.assertEqual(list(mf.load()), ["cafe_noir"])

    def test_a_name_with_no_ascii_letters_gets_a_stable_id(self) -> None:
        src = self.copy_as("\u30cf\u30ed\u30a6\u30a3\u30f3.wav", "jp")
        tid = ia.track_slug(src.stem)
        self.assertRegex(tid, r"^song_[0-9a-f]{8}$")
        self.assertEqual(ia.track_slug("\u30cf\u30ed\u30a6\u30a3\u30f3"), tid)
        self.assertEqual(self.run_cli(str(src))[0], 0)
        self.assertIn(f"{tid}.mp3", self.library())

    def test_slugs(self) -> None:
        for stem, want in (
            ("Caf\u00e9 Noir", "cafe_noir"),
            ("Cafe\u0301 Noir", "cafe_noir"),
            ("Stra\u00dfe", "stra_e"),
            ("\uff21\uff22\uff23", "abc"),  # fullwidth letters fold too
            (
                "the citizens of halloween - this is halloween",
                "the_citizens_of_halloween",
            ),
        ):
            with self.subTest(stem=stem):
                self.assertEqual(ia.track_slug(stem), want)
                self.assertTrue(ia.valid_track_id(want))

    def test_an_explicit_id_outside_the_alphabet_is_refused(self) -> None:
        for bad in ("caf\u00e9", "a" * 65, "a b"):
            with self.subTest(bad=bad):
                said = self.refused(str(self.src), "--id", bad)
                self.assertIn("letters, digits and _", said)
        self.assertEqual(self.library(), [])

    def test_a_windows_path_that_is_gone_reads_as_its_name(self) -> None:
        gone = "C:\\Users\\you\\Zo\u00eb\\Caf\u00e9.mp3"
        said = self.refused(gone)
        self.assertEqual(
            ir.reason(said),
            "Castle Tools could not find Caf\u00e9.mp3 — choose the song again "
            "from wherever it is now.",
        )

    def test_a_links_title_is_composed(self) -> None:
        def ytdlp(argv: list[str], **_: Any) -> SimpleNamespace:
            (self.tmp / "dl" / "Cafe\u0301.mp3").write_bytes(b"x")
            return done(0)

        (self.tmp / "dl").mkdir()
        self.assertEqual(self.fetch(ytdlp, self.tmp / "dl")[1], "Caf\u00e9")


class TestReadOnlyPlaces(EdgeCase):
    def test_a_read_only_song_imports_and_its_kept_copy_stays_writable(self) -> None:
        share = self.tmp / "share"
        share.mkdir()
        song = share / "song.wav"
        shutil.copyfile(self.src, song)
        os.chmod(song, stat.S_IREAD)
        os.chmod(share, stat.S_IREAD | stat.S_IEXEC)
        for _ in range(2):  # the second import replaces the first kept copy
            code, _ = self.run_cli(str(song), "--id", "ro", "--keep-source")
            self.assertEqual(code, 0)
        kept = self.lib / "_src" / "ro.wav"
        self.assertTrue(os.access(kept, os.W_OK), "the read-only bit came along")

    def test_an_old_read_only_kept_copy_does_not_block_a_reimport(self) -> None:
        (self.lib / "_src").mkdir()
        old = self.lib / "_src" / "ro.wav"
        old.write_bytes(b"an older copy")
        os.chmod(old, stat.S_IREAD)
        kept = ic.keep_source(self.src, "ro")
        self.assertEqual(kept.read_bytes(), self.src.read_bytes())

    def unreadable(self, exc: OSError) -> str:
        real = Path.open

        def fake(p: Path, *a: Any, **k: Any) -> Any:
            if p == self.src:
                raise exc
            return real(p, *a, **k)

        with (
            mock.patch.object(Path, "open", autospec=True, side_effect=fake),
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as cm,
        ):
            it._source_file(str(self.src), False, self.tmp, None, whole=True)
        return str(cm.exception)

    def test_a_song_this_user_may_not_read(self) -> None:
        denied = PermissionError(errno.EACCES, "Permission denied")
        self.assertEqual(self.unreadable(denied), ir.NO_ACCESS)

    def test_a_share_that_dropped_mid_read(self) -> None:
        dropped = OSError(errno.EIO, "Input/output error")
        self.assertEqual(self.unreadable(dropped), ir.NO_ACCESS)


if __name__ == "__main__":
    unittest.main()
