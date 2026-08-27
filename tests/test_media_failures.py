"""The media job paths, at their failure arms.

stems, import_fetch, studio_media and codec_compare shell out to Demucs,
yt-dlp and ffmpeg — the places real-world breakage lives (missing binary,
non-zero exit, partial output) and the places least likely to be noticed,
because they run as background jobs whose errors surface as a stalled
progress bar. The success paths are exercised end-to-end elsewhere; these
tests pin what each failure turns INTO: a sentence, not a traceback.
"""

from __future__ import annotations

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
import stems
import studio_media as sm


def done(code: int = 0, out: str = "", err: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=code, stdout=out, stderr=err)


class TestFetchUrl(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)

    def test_ytdlp_failure_surfaces_its_own_last_lines(self) -> None:
        with (
            mock.patch.object(imf, "_ytdlp", return_value="yt-dlp"),
            mock.patch.object(
                imf.subprocess,
                "run",
                return_value=done(1, err="x\nERROR: Video unavailable"),
            ),
        ):
            with self.assertRaises(SystemExit) as c:
                imf.fetch_url("https://example.test/a", self.tmp)
        self.assertIn("Video unavailable", str(c.exception))
        self.assertIn("could not fetch", str(c.exception))

    def test_success_with_no_file_is_still_a_failure(self) -> None:
        with (
            mock.patch.object(imf, "_ytdlp", return_value="yt-dlp"),
            mock.patch.object(imf.subprocess, "run", return_value=done(0)),
        ):
            with self.assertRaises(SystemExit) as c:
                imf.fetch_url("https://example.test/a", self.tmp)
        self.assertIn("no audio file", str(c.exception))

    def test_timeout_names_the_stall_not_a_traceback(self) -> None:
        with (
            mock.patch.object(imf, "_ytdlp", return_value="yt-dlp"),
            mock.patch.object(
                imf.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired("yt-dlp", 900),
            ),
        ):
            with self.assertRaises(SystemExit) as c:
                imf.fetch_url("https://example.test/a", self.tmp)
        self.assertIn("stalled", str(c.exception))

    def test_non_link_is_refused_before_any_subprocess(self) -> None:
        with mock.patch.object(imf.subprocess, "run") as r:
            with self.assertRaises(SystemExit):
                imf.fetch_url("-not-a-url", self.tmp)
        r.assert_not_called()


class TestProbe(unittest.TestCase):
    def test_missing_ytdlp_says_how_to_install(self) -> None:
        with mock.patch.object(sm.shutil, "which", return_value=None):
            out = sm.probe("https://example.test/a")
        self.assertFalse(out["ok"])
        self.assertIn("yt-dlp", out["error"])

    def test_non_http_is_not_a_link(self) -> None:
        with mock.patch.object(sm.shutil, "which", return_value="yt-dlp"):
            out = sm.probe("ftp://example.test/a")
        self.assertFalse(out["ok"])

    def test_timeout_reports_the_budget(self) -> None:
        with (
            mock.patch.object(sm.shutil, "which", return_value="yt-dlp"),
            mock.patch.object(
                sm.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired("yt-dlp", 60),
            ),
        ):
            out = sm.probe("https://example.test/a")
        self.assertFalse(out["ok"])
        self.assertIn("timed out", out["error"])

    def test_failure_surfaces_ytdlps_last_line(self) -> None:
        with (
            mock.patch.object(sm.shutil, "which", return_value="yt-dlp"),
            mock.patch.object(
                sm.subprocess,
                "run",
                return_value=done(1, err="warn\nERROR: Private video"),
            ),
        ):
            out = sm.probe("https://example.test/a")
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "ERROR: Private video")

    def test_unparseable_answer_is_a_sentence(self) -> None:
        with (
            mock.patch.object(sm.shutil, "which", return_value="yt-dlp"),
            mock.patch.object(
                sm.subprocess, "run", return_value=done(0, out="not json")
            ),
        ):
            out = sm.probe("https://example.test/a")
        self.assertFalse(out["ok"])
        self.assertIn("parse", out["error"])

    def test_ok_path_shapes_duration_text(self) -> None:
        with (
            mock.patch.object(sm.shutil, "which", return_value="yt-dlp"),
            mock.patch.object(
                sm.subprocess,
                "run",
                return_value=done(0, out='{"title":"T","duration":125}'),
            ),
        ):
            out = sm.probe("https://example.test/a")
        self.assertTrue(out["ok"])
        self.assertEqual(out["duration_text"], "2:05")


class TestStems(unittest.TestCase):
    def test_encode_failure_names_the_file(self) -> None:
        with mock.patch.object(stems.subprocess, "run", return_value=done(1)):
            with self.assertRaises(SystemExit) as c:
                stems._encode(Path("in.wav"), Path("out.mp3"))
        self.assertIn("out.mp3", str(c.exception))

    def test_separate_refuses_a_missing_track(self) -> None:
        with mock.patch.object(stems, "track_file", return_value=None):
            with self.assertRaises(SystemExit) as c:
                stems.separate("ghost")
        self.assertIn("no such track", str(c.exception))

    def test_missing_demucs_says_how_to_install(self) -> None:
        with (
            mock.patch.object(stems, "track_file", return_value=Path("x.mp3")),
            mock.patch.object(stems, "fresh", return_value=False),
            mock.patch.object(stems.importlib.util, "find_spec", return_value=None),
        ):
            with self.assertRaises(SystemExit) as c:
                stems.separate("x")
        self.assertIn("demucs", str(c.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
