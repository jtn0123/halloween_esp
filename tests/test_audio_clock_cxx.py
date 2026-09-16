"""The v5.55 audio clock, executed: tests/cxx/audio_clock_check.cpp compiles
firmware/sd_web_state.h with the host compiler and steps it one mailbox tick
at a time. The clock is what every light frame on the porch is aligned to,
so "position_ms leads the sound" is a bug this suite exists to catch."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tests" / "cxx" / "audio_clock_check.cpp"
COMPILER = shutil.which("clang++") or shutil.which("g++")
FLAGS = [
    "-std=c++17",
    "-O1",
    "-Wall",
    "-Wextra",
    "-Werror",
    "-I",
    str(ROOT / "firmware"),
]
IN_CI = bool(os.environ.get("CI"))


@unittest.skipIf(COMPILER is None and not IN_CI, "no host C++ compiler")
class TestAudioClock(unittest.TestCase):
    def test_clock_starts_on_sound_not_on_the_command(self) -> None:
        if COMPILER is None:
            raise AssertionError("CI is set and no host C++ compiler is on PATH")
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "audio_clock_check"
            built = subprocess.run(
                [COMPILER, *FLAGS, str(SRC), "-o", str(exe)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            run = subprocess.run(
                [str(exe)], capture_output=True, text=True, check=False
            )
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn("audio clock OK", run.stdout)
