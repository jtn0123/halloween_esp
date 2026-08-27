"""The Rust studio's media routes against the Python's — B5 pass 2.

The waveform is the crown jewel: decode (identical ffmpeg), peak buckets
(numpy's linspace edges), the full per-band onset analysis with pans, and
the level envelope — compared BYTE for byte, not value for value, because
both sides round with CPython's rules and serialize with json.dumps's.
The stems read routes re-serve their cached JSON the same way.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from studio_rust_case import CARGO, IN_CI, StudioPair


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class MediaRoutes(StudioPair):
    def test_waveform_matches_byte_for_byte(self) -> None:
        a, b = self.both("/studio/waveform/t_alpha")
        self.assertEqual(a[0], 200)
        self.assertEqual(a[2], b[2])
        self.assertEqual(a[1]["content-type"], b[1]["content-type"])
        d = json.loads(a[2])
        assert isinstance(d, dict)
        self.assertLessEqual(len(d["peaks"]), 1000)
        self.assertTrue(d["onsets"], "the click track must have onsets")
        self.assertTrue(
            all(
                len(h) == 3
                for k, hits in d["onsets"].items()
                if k.startswith("onset_")
                for h in hits
            ),
            "stereo was loaded, so every onset hit carries a pan",
        )

    def test_waveform_sensitivity_knobs(self) -> None:
        for q in (
            "?sensitivity=2.5",
            "?sens_low=0.6&sens_high=3.0",
            "?sensitivity=abc",
            "?sensitivity=0.4&sens_mid=1.9",
        ):
            a, b = self.both("/studio/waveform/t_beta" + q)
            self.assertEqual(a[0], 200, q)
            self.assertEqual(a[2], b[2], q)

    def test_waveform_of_zero_frames_keeps_the_short_shape(self) -> None:
        a, b = self.both("/studio/waveform/t_empty")
        self.assertEqual(
            self.parsed(a),
            {"id": "t_empty", "duration": 0.0, "peaks": [], "onsets": {}},
        )
        self.assertEqual(a[2], b[2])

    def test_waveform_unknown_track(self) -> None:
        a, b = self.both("/studio/waveform/nope")
        self.assertEqual(a[0], 404)
        self.assertEqual(self.parsed(a), self.parsed(b))

    def test_stems_analysis_fresh_stale_and_missing(self) -> None:
        a, b = self.both("/studio/stems/t_alpha")
        self.assertEqual(a[0], 200)
        self.assertEqual(a[2], b[2])
        d = json.loads(a[2])
        assert isinstance(d, dict)
        self.assertTrue(d["ok"])
        self.assertFalse(d["stale"])
        a, b = self.both("/studio/stems/t_beta")
        self.assertEqual(a[0], 200)
        self.assertEqual(a[2], b[2])
        d = json.loads(a[2])
        assert isinstance(d, dict)
        self.assertTrue(d["stale"], "split from a different file — stale")
        for path in ("/studio/stems/t_del", "/studio/stems/zzz"):
            a, b = self.both(path)
            self.assertEqual(a[0], 404, path)
            self.assertEqual(self.parsed(a), self.parsed(b))

    def test_stem_streams_and_guards(self) -> None:
        a, b = self.both("/studio/stem/t_alpha/vocals")
        self.assertEqual(a[0], 200)
        self.assertEqual(a[2], b[2])
        self.assertEqual(a[1]["content-type"], b[1]["content-type"])
        for path in (
            "/studio/stem/t_alpha/drums",
            "/studio/stem/none/vocals",
            "/studio/stem/short",
        ):
            a, b = self.both(path)
            self.assertEqual(a[0], 404, path)
            self.assertEqual(self.parsed(a), self.parsed(b))

    def test_compare_has_no_state_yet(self) -> None:
        a, b = self.both("/studio/compare/tok/mp3")
        self.assertEqual(a[0], 404)
        self.assertEqual(self.parsed(a), self.parsed(b))


if __name__ == "__main__":
    unittest.main()
