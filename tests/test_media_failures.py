"""The media job paths, at their failure arms.

stems, import_fetch, studio_media and codec_compare shell out to Demucs,
yt-dlp and ffmpeg — the places real-world breakage lives (missing binary,
non-zero exit, partial output) and the places least likely to be noticed,
because they run as background jobs whose errors surface as a stalled
progress bar. The success paths are exercised end-to-end elsewhere; these
tests pin what each failure turns INTO: a sentence, not a traceback.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import import_fetch as imf
import import_reason as ir
import stems


def done(code: int = 0, out: str = "", err: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=code, stdout=out, stderr=err)


class TestFetchUrl(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)

    def test_ytdlp_failure_says_what_it_means_and_logs_its_words(self) -> None:
        err = io.StringIO()
        with (
            mock.patch.object(imf, "_ytdlp", return_value="yt-dlp"),
            mock.patch.object(
                subprocess,
                "run",
                return_value=done(1, err="x\nERROR: [youtube] a: Video unavailable"),
            ),
            contextlib.redirect_stderr(err),
        ):
            with self.assertRaises(SystemExit) as c:
                imf.fetch_url("https://example.test/a", self.tmp)
        self.assertEqual(str(c.exception), ir.UNAVAILABLE)
        # yt-dlp's own line is kept for whoever helps — indented, beneath.
        self.assertIn("    ERROR: [youtube] a: Video unavailable", err.getvalue())

    def test_an_unknown_extractor_error_offers_the_update(self) -> None:
        with (
            mock.patch.object(imf, "_ytdlp", return_value="yt-dlp"),
            mock.patch.object(
                subprocess, "run", return_value=done(1, err="ERROR: [youtube] a: ???")
            ),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(SystemExit) as c:
                imf.fetch_url("https://example.test/a", self.tmp)
        self.assertEqual(str(c.exception), ir.DOWNLOADER_OLD)
        self.assertEqual(ir.action(str(c.exception)), ir.UPDATE_DOWNLOADER)

    def test_a_failure_yt_dlp_explains_nowhere_is_still_a_sentence(self) -> None:
        with (
            mock.patch.object(imf, "_ytdlp", return_value="yt-dlp"),
            mock.patch.object(subprocess, "run", return_value=done(2, out="?")),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(SystemExit) as c:
                imf.fetch_url("https://example.test/a", self.tmp)
        self.assertEqual(str(c.exception), ir.DOWNLOAD_FAILED)

    def test_success_with_no_file_is_still_a_failure(self) -> None:
        with (
            mock.patch.object(imf, "_ytdlp", return_value="yt-dlp"),
            mock.patch.object(subprocess, "run", return_value=done(0)),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(SystemExit) as c:
                imf.fetch_url("https://example.test/a", self.tmp)
        self.assertEqual(str(c.exception), ir.NO_AUDIO)

    def test_timeout_names_the_stall_not_a_traceback(self) -> None:
        with (
            mock.patch.object(imf, "_ytdlp", return_value="yt-dlp"),
            mock.patch.object(
                subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired("yt-dlp", 900),
            ),
        ):
            with self.assertRaises(SystemExit) as c:
                imf.fetch_url("https://example.test/a", self.tmp)
        self.assertEqual(str(c.exception), imf.DOWNLOAD_STALLED)

    def test_non_link_is_refused_before_any_subprocess(self) -> None:
        with (
            mock.patch.object(subprocess, "run") as r,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(SystemExit) as c:
                imf.fetch_url("-not-a-url", self.tmp)
        r.assert_not_called()
        self.assertEqual(str(c.exception), ir.NOT_A_LINK)


class TestStems(unittest.TestCase):
    def test_encode_failure_names_the_file(self) -> None:
        # The owner reads the sentence; the file it was making is in the log.
        log = io.StringIO()
        with (
            mock.patch.object(stems.subprocess, "run", return_value=done(1)),
            contextlib.redirect_stderr(log),
        ):
            with self.assertRaises(SystemExit) as c:
                stems._encode(Path("in.wav"), Path("out.mp3"))
        self.assertEqual(str(c.exception), ir.TOOL_FAILED.format(prog="ffmpeg"))
        self.assertIn("    ffmpeg could not encode out.mp3", log.getvalue())

    def test_separate_refuses_a_missing_track(self) -> None:
        with mock.patch.object(stems, "track_file", return_value=None):
            with self.assertRaises(SystemExit) as c:
                stems.separate("ghost")
        self.assertIn("no such track", str(c.exception))

    def test_missing_demucs_says_how_to_install(self) -> None:
        # The owner is told to reinstall; the pip line is for whoever reads
        # the log.
        log = io.StringIO()
        with (
            mock.patch.object(stems, "track_file", return_value=Path("x.mp3")),
            mock.patch.object(stems, "fresh", return_value=False),
            mock.patch.object(stems.importlib.util, "find_spec", return_value=None),
            contextlib.redirect_stderr(log),
        ):
            with self.assertRaises(SystemExit) as c:
                stems.separate("x")
        self.assertEqual(str(c.exception), ir.DEMUCS_MISSING)
        self.assertIn("python -m pip install demucs", log.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
