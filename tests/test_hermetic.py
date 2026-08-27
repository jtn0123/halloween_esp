"""The suite must not care what CASTLE_* the operator's shell exports.

CLAUDE.md's emulator workflow has you export CASTLE_HOST and CASTLE_TRACKS;
`make test` in that shell used to fail six tests that read the repo's own
scenes file or library through those knobs. helpers.py now clears them at
import, and this pins that — including in a fresh interpreter with the
variables set, which is the case that actually bit.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import helpers


class TestHermeticEnv(unittest.TestCase):
    def test_sandbox_knobs_are_absent_once_helpers_is_imported(self) -> None:
        for k in helpers.SANDBOX_ENV:
            self.assertNotIn(k, os.environ, k)

    def test_a_polluted_shell_is_scrubbed_in_a_fresh_interpreter(self) -> None:
        env = {
            **os.environ,
            "CASTLE_HOST": "127.0.0.1:9",
            "CASTLE_TRACKS": "/tmp/castle-hermetic-x",
            "CASTLE_SCENES": "/tmp/castle-hermetic-x/scenes.yaml",
            "CASTLE_BUILD": "/tmp/castle-hermetic-x/build",
        }
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import os, helpers, studio_tracks, build_paths;"
                    "print(sorted(k for k in os.environ if k.startswith('CASTLE_')));"
                    "print(studio_tracks.TRACKS);"
                    "print(build_paths.scenes_file())"
                ),
            ],
            cwd=str(ROOT / "tests"),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.strip().splitlines()
        self.assertEqual(lines[0], "[]")
        self.assertEqual(lines[1], str(ROOT / "tracks"))
        self.assertEqual(lines[2], str(ROOT / "scenes" / "scenes.yaml"))


if __name__ == "__main__":
    unittest.main()
