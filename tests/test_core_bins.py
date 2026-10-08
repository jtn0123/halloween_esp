"""tools/core_bins.py: a packaged install's CASTLE_CORE_BIN_DIR wins over
core/target and never reaches for cargo."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import core_bins
import exe_paths


class BinDirTest(unittest.TestCase):
    def setUp(self) -> None:
        core_bins.core_bin.cache_clear()
        self.addCleanup(core_bins.core_bin.cache_clear)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)

    def _env(self) -> Any:
        return mock.patch.dict(os.environ, {core_bins.BIN_DIR_ENV: str(self.folder)})

    def test_the_named_folder_is_used_without_cargo(self) -> None:
        exe = self.folder / exe_paths.exe("scene_render")
        exe.write_bytes(b"")
        with self._env(), mock.patch("subprocess.run") as run:
            self.assertEqual(core_bins.core_bin("scene_render"), exe)
        run.assert_not_called()

    def test_a_folder_without_the_binary_is_a_hard_stop(self) -> None:
        with self._env(), mock.patch("subprocess.run") as run:
            with self.assertRaises(SystemExit) as stop:
                core_bins.core_bin("analyze_track")
        run.assert_not_called()
        self.assertIn(core_bins.BIN_DIR_ENV, str(stop.exception))


if __name__ == "__main__":
    unittest.main()
