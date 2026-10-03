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
        # Built from a base with every CASTLE_* removed, so the line below
        # measures helpers' scrub of the sandbox knobs and nothing else:
        # CASTLE_PY and CASTLE_E2E_PORT are not sandbox knobs, and a
        # worktree or runner that exports one (as CLAUDE.md says to) must
        # not redden this test. The one CASTLE_* left is the managed
        # yt-dlp's home, which helpers pins set-but-empty ("none") so a
        # developer's own downloaded copy never runs under a test.
        base = {k: v for k, v in os.environ.items() if not k.startswith("CASTLE_")}
        env = {
            **base,
            "CASTLE_HOST": "127.0.0.1:9",
            "CASTLE_TRACKS": "/tmp/castle-hermetic-x",
            "CASTLE_SCENES": "/tmp/castle-hermetic-x/scenes.yaml",
            "CASTLE_BUILD": "/tmp/castle-hermetic-x/build",
            "CASTLE_DOWNLOADER_DIR": "/tmp/castle-hermetic-x/downloader",
        }
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import os, helpers, track_lib, build_paths;"
                    "print(sorted((k, v) for k, v in os.environ.items()"
                    " if k.startswith('CASTLE_')));"
                    "print(track_lib.TRACKS);"
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
        self.assertEqual(lines[0], "[('CASTLE_DOWNLOADER_DIR', '')]")
        self.assertEqual(lines[1], str(ROOT / "tracks"))
        self.assertEqual(lines[2], str(ROOT / "scenes" / "scenes.yaml"))


if __name__ == "__main__":
    unittest.main()
