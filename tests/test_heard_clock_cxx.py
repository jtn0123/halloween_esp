"""The heard clock (firmware/castle_heard.h, v5.72), run on this machine.

A card show used to walk a stopwatch started when the speaker said it was
running, which is before anything is heard and does not stop when the
decoder starves. tests/cxx/heard_check.cpp drives the real header and the
real cue walker with a fake speaker that reports its DMA buffers the way
ESPHome's I2S task does, and asserts on which tick each light fires: after
the warm-up, through a stall, on the stopwatch fall-back for a speaker that
never reports, and with the speaker's task writing while the loop reads.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

import cue_file
import cxx_compiler

SRC = ROOT / "tests" / "cxx" / "heard_check.cpp"
COMPILER = cxx_compiler.COMPILER  # g++ first on Windows; see the module
FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", "-pthread",
         "-I", str(ROOT / "tests" / "cxx" / "shim"), "-I", str(ROOT / "firmware")]  # fmt: skip
IN_CI = bool(os.environ.get("CI"))
SCENE = {"id": "song", "duration_ms": 3000}
CUES = [{"t": t, "op": "strike", "intensity": 0.5} for t in (0, 100, 1000, 2000)]


@unittest.skipIf(COMPILER is None and not IN_CI, "no host C++ compiler")
class TestHeardClock(unittest.TestCase):
    def test_the_lights_follow_what_the_speaker_played(self) -> None:
        if COMPILER is None:
            raise AssertionError("CI is set and no host C++ compiler is on PATH")
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "heard_check"
            built = subprocess.run([COMPILER, *FLAGS, str(SRC), "-o", str(exe)],
                                   capture_output=True, text=True, check=False)  # fmt: skip
            self.assertEqual(built.returncode, 0, built.stderr)
            (Path(tmp) / "song.cue").write_bytes(
                cue_file.encode(SCENE, CUES, ["towerL", "towerR", "door"])
            )
            run = subprocess.run([str(exe), tmp], capture_output=True, text=True,
                                 check=False, timeout=60)  # fmt: skip
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertEqual(run.stdout.strip(), "heard clock OK")


if __name__ == "__main__":
    unittest.main()
