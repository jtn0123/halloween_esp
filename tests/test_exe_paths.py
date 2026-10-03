"""exe_paths: what each program is called, and where it is looked for.

The Windows half is the same code with `WINDOWS` switched on — the names
it produces (`.exe`, `Scripts\\`) are the contract, so that is what is
pinned. CASTLE_FFMPEG / CASTLE_YTDLP are the desktop install's bundled
copies and must beat everything else.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import castle_tools_status
import exe_paths
import import_fetch
import import_reason as ir

CLEAN = {"CASTLE_FFMPEG": "", "CASTLE_YTDLP": ""}


class TestNames(unittest.TestCase):
    def test_posix_names_are_bare(self) -> None:
        with mock.patch.object(exe_paths, "WINDOWS", False):
            self.assertEqual(exe_paths.exe("studio"), "studio")
            self.assertEqual(
                exe_paths.npm_bin(Path("b"), "esbuild"), Path("b", "esbuild")
            )
            self.assertEqual(
                exe_paths.venv_python(Path("v")), Path("v", "bin", "python")
            )

    def test_windows_names_carry_exe_and_scripts(self) -> None:
        with mock.patch.object(exe_paths, "WINDOWS", True):
            self.assertEqual(exe_paths.exe("studio"), "studio.exe")
            self.assertEqual(exe_paths.exe("studio.exe"), "studio.exe")
            self.assertEqual(
                exe_paths.npm_bin(Path("b"), "esbuild"), Path("b", "esbuild.cmd")
            )
            self.assertEqual(
                exe_paths.venv_python(Path("v")), Path("v", "Scripts", "python.exe")
            )
            self.assertEqual(
                exe_paths.venv_script(Path("v"), "pip"), Path("v", "Scripts", "pip.exe")
            )


class TestMediaTools(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)

    def test_unset_means_path(self) -> None:
        with mock.patch.dict(os.environ, CLEAN):
            self.assertEqual(exe_paths.ffmpeg(), "ffmpeg")
            self.assertEqual(exe_paths.ffprobe(), "ffprobe")

    def test_castle_ffmpeg_wins_and_brings_its_ffprobe(self) -> None:
        ffmpeg = self.tmp / "ffmpeg.exe"
        ffprobe = self.tmp / "ffprobe.exe"
        ffmpeg.touch()
        ffprobe.touch()
        with mock.patch.dict(os.environ, {"CASTLE_FFMPEG": str(ffmpeg)}):
            self.assertEqual(exe_paths.ffmpeg(), str(ffmpeg))
            self.assertEqual(exe_paths.ffprobe(), str(ffprobe))

    def test_a_lone_bundled_ffmpeg_falls_back_to_paths_ffprobe(self) -> None:
        ffmpeg = self.tmp / "ffmpeg"
        ffmpeg.touch()
        with mock.patch.dict(os.environ, {"CASTLE_FFMPEG": str(ffmpeg)}):
            self.assertEqual(exe_paths.ffprobe(), "ffprobe")

    def test_castle_ytdlp_wins(self) -> None:
        with mock.patch.dict(os.environ, {"CASTLE_YTDLP": "C:\\tools\\yt-dlp.exe"}):
            self.assertEqual(exe_paths.ytdlp(), "C:\\tools\\yt-dlp.exe")

    def test_the_interpreters_own_ytdlp_beats_path(self) -> None:
        python = self.tmp / "python"
        (self.tmp / "yt-dlp").touch()
        with (
            mock.patch.dict(os.environ, CLEAN),
            mock.patch.object(exe_paths.sys, "executable", str(python)),
            mock.patch.object(exe_paths, "WINDOWS", False),
        ):
            self.assertEqual(exe_paths.ytdlp(), str(self.tmp / "yt-dlp"))

    def test_no_ytdlp_anywhere_is_a_sentence(self) -> None:
        with (
            mock.patch.dict(os.environ, CLEAN),
            mock.patch.object(exe_paths.sys, "executable", str(self.tmp / "py")),
            mock.patch.object(exe_paths.shutil, "which", return_value=None),
        ):
            self.assertIsNone(exe_paths.ytdlp())
            err = io.StringIO()
            with self.assertRaises(SystemExit) as cm, contextlib.redirect_stderr(err):
                import_fetch._ytdlp()
        # The owner reads the sentence with the button in it; the places it
        # looked go beneath, for whoever helps.
        self.assertEqual(str(cm.exception), ir.DOWNLOADER_MISSING)
        self.assertIn("CASTLE_YTDLP", err.getvalue())

    def test_which_takes_a_path_or_a_name(self) -> None:
        here = self.tmp / "tool"
        here.touch()
        self.assertEqual(exe_paths.which(str(here)), str(here))
        self.assertIsNone(exe_paths.which(str(self.tmp / "absent")))
        self.assertEqual(exe_paths.which(sys.executable), sys.executable)

    def test_readiness_reports_the_bundled_ffmpeg(self) -> None:
        ffmpeg = self.tmp / "ffmpeg"
        ffmpeg.touch()
        with mock.patch.dict(os.environ, {"CASTLE_FFMPEG": str(ffmpeg)}):
            check = castle_tools_status._command("ffmpeg", command=exe_paths.ffmpeg())
        self.assertEqual(check["name"], "ffmpeg")
        self.assertEqual(check["detail"], str(ffmpeg))
        self.assertTrue(check["ok"])


if __name__ == "__main__":
    unittest.main()
