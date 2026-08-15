"""The codec comparison, and what its number is allowed to claim.

A quality score nobody can check is worse than no score, because it looks
authoritative. These pin the properties the number actually has: it is zero
against itself, it ranks lossless above lossy, it is reproducible, and it does
not fall over on the degenerate inputs.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import analyze as ana  # noqa: E402
import codec_compare as cc  # noqa: E402
from helpers import make_click_track  # noqa: E402

OPTS = {"start": 0, "take": 3.0, "fade_in": None, "fade_out": None,
        "normalize": False, "gain_db": None, "bitrate": 96,
        "channels": 1, "sample_rate": 44100}


class TestSpectralDistance(unittest.TestCase):
    """The metric on its own, without ffmpeg in the way."""

    def setUp(self) -> None:
        rng = np.random.default_rng(1234)
        self.x = rng.standard_normal(ana.SR * 2).astype(np.float32) * 0.2

    def test_a_signal_is_identical_to_itself(self) -> None:
        self.assertEqual(cc.spectral_db(self.x, self.x), 0.0)

    def test_noise_scores_worse_than_a_light_change(self) -> None:
        rng = np.random.default_rng(99)
        nudged = self.x + rng.standard_normal(len(self.x)).astype(np.float32) * 0.001
        wrecked = rng.standard_normal(len(self.x)).astype(np.float32) * 0.2
        self.assertLess(cc.spectral_db(self.x, nudged),
                        cc.spectral_db(self.x, wrecked))

    def test_it_is_symmetric(self) -> None:
        y = self.x * 0.5
        self.assertAlmostEqual(cc.spectral_db(self.x, y),
                               cc.spectral_db(y, self.x), places=6)

    def test_a_buffer_too_short_to_analyse_scores_zero_rather_than_throwing(self) -> None:
        tiny = np.zeros(16, dtype=np.float32)
        self.assertEqual(cc.spectral_db(tiny, tiny), 0.0)

    def test_lengths_need_not_match(self) -> None:
        """Every encoder returns a slightly different number of samples."""
        self.assertIsInstance(cc.spectral_db(self.x, self.x[:-1000]), float)


@unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
class TestEncodeSet(unittest.TestCase):
    """The whole pass, against real encoders."""

    tmp: Path
    src: Path
    rows: list[dict]
    by: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        cls.src = cls.tmp / "src.wav"
        make_click_track(cls.src, seconds=3.0)
        cls.rows = cc.encode_set(cls.src, cls.tmp / "out", dict(OPTS))
        cls.by = {r["codec"]: r for r in cls.rows}

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_every_codec_produced_a_file(self) -> None:
        self.assertEqual({r["codec"] for r in self.rows}, set(cc.CODECS))
        for r in self.rows:
            self.assertGreater(r["bytes"], 0, r["codec"])

    def test_the_reference_scores_zero_against_itself(self) -> None:
        self.assertEqual(self.by[cc.REFERENCE]["db"], 0.0)

    def test_lossless_is_closer_to_the_source_than_lossy(self) -> None:
        """The one ordering the number has to get right, or it is noise."""
        self.assertLess(self.by["flac"]["db"], self.by["mp3"]["db"])
        self.assertLess(self.by["flac"]["db"], self.by["opus"]["db"])

    def test_lossy_is_smaller_than_the_reference(self) -> None:
        self.assertLess(self.by["mp3"]["bytes"], self.by[cc.REFERENCE]["bytes"])
        self.assertLess(self.by["opus"]["bytes"], self.by[cc.REFERENCE]["bytes"])

    def test_the_rows_come_back_in_the_order_asked_for(self) -> None:
        """WAV is encoded first whatever the caller wants, since everything is
        measured against it — but that must not reorder the answer."""
        self.assertEqual([r["codec"] for r in self.rows], list(cc.CODECS))

    def test_the_score_is_reproducible(self) -> None:
        """A number that moves between runs cannot be used to choose."""
        again = cc.encode_set(self.src, self.tmp / "out2", dict(OPTS))
        for a, b in zip(self.rows, again):
            self.assertEqual(a["codec"], b["codec"])
            self.assertAlmostEqual(a["db"], b["db"], places=2, msg=a["codec"])

    def test_a_lower_bitrate_scores_worse(self) -> None:
        """The question the panel exists to answer — 'is 96k enough here' —
        needs the number to move in the right direction when the bitrate does."""
        low = cc.encode_set(self.src, self.tmp / "low",
                            dict(OPTS, bitrate=32), codecs=("wav", "mp3"))
        low_mp3 = next(r for r in low if r["codec"] == "mp3")
        self.assertGreater(low_mp3["db"], self.by["mp3"]["db"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
