"""The analyze_track bin against analyze.analyze_full, value for value —
the importer's ears, crossed to the crate.

import_track spawns castle-core's analyze_track now; analyze.py remains
as the parity reference. Unlike the synth path there are no kernel-mode
probes here: the onset detector's arithmetic was pinned unconditionally
(defined-order FFT, pairwise sums, the scaled-hypot |z|), so the same
answer is expected on every machine, exactly.

Skipped, not failed, without cargo — except in CI.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import analyze as ana
import helpers  # noqa: F401  (hermetic env)
import import_track as it
from helpers import make_click_track

CARGO = shutil.which("cargo")
IN_CI = bool(os.environ.get("CI"))


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class TestAnalyzeTrackParity(unittest.TestCase):
    tmp: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="analyze-track-"))
        make_click_track(cls.tmp / "click.wav", seconds=2.0)
        make_click_track(cls.tmp / "slow.wav", seconds=3.0, bpm=90.0, hats=False)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def reference(
        self, path: Path, sensitivity: float | dict[str, float], stereo: bool
    ) -> tuple[int, dict[str, list[list[float]]]]:
        x = ana.load_audio(path)
        marks = ana.analyze_full(
            x,
            sensitivity=sensitivity,
            stereo=ana.load_stereo(path) if stereo else None,
        )
        # The bin answers JSON, so tuples become lists — normalise the
        # reference the same way before comparing.
        return len(x), json.loads(json.dumps(marks))

    def held_equal(
        self, path: Path, sensitivity: float | dict[str, float], stereo: bool
    ) -> None:
        samples, bands = it.crate_analysis(path, sensitivity, stereo=stereo)
        want_samples, want = self.reference(path, sensitivity, stereo)
        self.assertEqual(samples, want_samples)
        self.assertEqual(bands, want, path.name)
        # Key order too: the manifest's onset counts and the pasteable
        # scene block walk the dict in this order.
        self.assertEqual(list(bands), list(want), path.name)

    def test_stereo_with_a_per_band_map(self) -> None:
        self.held_equal(
            self.tmp / "click.wav", {"onset_low": 0.8, "mid": 1.1}, stereo=True
        )

    def test_mono_scalar_the_analyze_only_shape(self) -> None:
        self.held_equal(self.tmp / "slow.wav", 1.1, stereo=False)

    def test_an_undecodable_file_is_a_valueerror_not_a_row(self) -> None:
        junk = self.tmp / "junk.mp3"
        junk.write_bytes(b"not audio at all")
        with self.assertRaises(ValueError) as cm:
            it.crate_analysis(junk, 1.1, stereo=True)
        self.assertIn("junk.mp3", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
