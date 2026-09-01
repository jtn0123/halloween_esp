"""What an import PRODUCES: the provenance record, the scene block, the
render that consumes it.

Split from tests/test_import.py at the 500-line cap (grade report
2026-09-01 I1), on the seam the file already had: next door is getting the
audio in — time strings, the ffmpeg convert, the tools we do not ship —
and here is everything downstream of a file that already landed. The two
halves share no fixtures, which is what made this the seam and not the
midpoint.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import import_track as it
import manifest as mf
import render_audio as ra
import yaml
from helpers import make_click_track


class TestManifest(unittest.TestCase):
    """Provenance is what makes --refresh possible."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._real = mf.PATH
        mf.PATH = self.tmp / "tracks.json"

    def tearDown(self) -> None:
        mf.PATH = self._real
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_round_trip(self) -> None:
        mf.record(
            "x",
            source="https://example.com/a",
            title="A Title",
            opts={"take": 20},
            audio={"duration": 20.0},
            onsets={"onset_low": 5},
        )
        got = mf.get("x")
        assert got is not None
        self.assertEqual(got["source"], "https://example.com/a")
        self.assertEqual(got["title"], "A Title")
        self.assertEqual(got["opts"]["take"], 20)

    def test_refresh_keeps_user_notes(self) -> None:
        """Notes are the user's; a re-import must not wipe them."""
        mf.record("x", source="s", notes="keep me")
        mf.record("x", source="s")  # re-import, no notes given
        got = mf.get("x")
        assert got is not None
        self.assertEqual(got["notes"], "keep me")

    def test_forget(self) -> None:
        mf.record("x", source="s")
        mf.forget("x")
        self.assertIsNone(mf.get("x"))

    def test_missing_file_is_empty_not_a_crash(self) -> None:
        self.assertEqual(mf.load(), {})

    def test_corrupt_file_is_survivable(self) -> None:
        mf.PATH.parent.mkdir(parents=True, exist_ok=True)
        mf.PATH.write_text("{not json")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(mf.load(), {})
        self.assertIn("WARNING: tracks.json was not valid JSON", out.getvalue())


class TestSceneBlock(unittest.TestCase):
    """The generated block has to be valid YAML that the real loader accepts."""

    def setUp(self) -> None:
        self.marks = {
            "onset_low": [(0.0, 1.0), (0.5, 0.8)],
            "onset_mid": [(0.25, 0.6)],
            "onset_high": [(0.75, 0.4)],
        }

    def parse(self, block: str) -> dict[str, Any]:
        doc = yaml.safe_load("scenes:\n" + block)
        return dict(doc["scenes"][0])

    def test_is_valid_yaml(self) -> None:
        sc = self.parse(it.scene_block("my_track", 20.0, self.marks))
        self.assertEqual(sc["id"], "my_track")
        self.assertEqual(sc["duration_ms"], 20000)

    def test_pulse_streams_match_detected_bands(self) -> None:
        sc = self.parse(it.scene_block("t", 10.0, self.marks))
        synths = {p["synth"] for p in sc["pulse"]}
        self.assertEqual(synths, {"onset_low", "onset_mid", "onset_high"})

    def test_bass_drives_the_door(self) -> None:
        """The whole point of banding: low frequencies get their own zone."""
        sc = self.parse(it.scene_block("t", 10.0, self.marks))
        low = next(p for p in sc["pulse"] if p["synth"] == "onset_low")
        self.assertEqual(low["zone"], "door")

    def test_empty_bands_are_omitted(self) -> None:
        sc = self.parse(it.scene_block("t", 10.0, {"onset_low": [(0.0, 1.0)]}))
        self.assertEqual(len(sc["pulse"]), 1)

    def test_effects_are_known_to_the_firmware(self) -> None:
        """A scene referencing an effect the firmware lacks fails to build."""
        sys.path.insert(0, str(ROOT / "tools"))
        import gen_esphome as ge

        sc = self.parse(it.scene_block("t", 10.0, self.marks))
        for eff in sc["base"].values():
            self.assertIn(eff, ge.EFFECT_IDS, f"unknown effect {eff!r}")

    def test_no_onsets_still_yields_loadable_yaml(self) -> None:
        """A track with no detectable beats must not produce a broken scene."""
        block = it.scene_block("quiet", 12.0, {})
        sc = self.parse(block)
        self.assertEqual(sc["id"], "quiet")
        self.assertIn("pulse", sc)


class TestRenderIntegration(unittest.TestCase):
    """End to end: an imported file becomes a scene with light cues on it."""

    tmp: Path
    track: Path

    @classmethod
    def setUpClass(cls) -> None:
        # Rendered INTO the tempdir, never the user's tracks/: the scene
        # below points at the absolute path, so nothing here needs the real
        # library and nothing is left behind if the class dies mid-run.
        cls.tmp = Path(tempfile.mkdtemp())
        cls.track = cls.tmp / "_test_integration.mp3"
        src = cls.tmp / "src.wav"
        make_click_track(src, seconds=6.0, bpm=120.0)
        it.convert(
            src,
            cls.track,
            {
                "start": 0,
                "take": None,
                "fade_in": None,
                "fade_out": None,
                "bitrate": 96,
                "channels": 1,
                "sample_rate": 44100,
                "normalize": False,
                "gain_db": None,
            },
        )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.track.unlink(missing_ok=True)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def scene(self) -> dict[str, Any]:
        return {
            "id": "_test_integration",
            "duration_ms": 6000,
            "loop": True,
            "audio_file": str(self.track),
            "pulse": [{"synth": "onset_low", "zone": "door", "intensity": 0.55}],
        }

    def test_render_produces_audio_and_markers(self) -> None:
        cfg = {"sample_rate": 44100, "bitrate": 96, "channels": 1}
        buf, marks = ra.render_scene_py(self.scene(), cfg)
        self.assertAlmostEqual(len(buf) / 44100, 6.0, delta=0.1)
        self.assertIn("onset_low", marks)
        self.assertGreater(len(marks["onset_low"]), 5)

    def test_markers_become_light_cues(self) -> None:
        """The contract that makes imported audio drive the lights at all."""
        import gen_esphome as ge

        cfg = {"sample_rate": 44100, "bitrate": 96, "channels": 1}
        _buf, marks = ra.render_scene_py(self.scene(), cfg)
        cues = ge.pulse_cues(self.scene(), {"_test_integration": marks})
        self.assertGreater(len(cues), 5)
        self.assertEqual(cues[0]["op"], "strike")
        self.assertEqual(cues[0]["targets"], ["door"])

    def test_render_normalises(self) -> None:
        cfg = {"sample_rate": 44100, "bitrate": 96, "channels": 1}
        buf, _ = ra.render_scene_py(self.scene(), cfg)
        self.assertAlmostEqual(float(np.abs(buf).max()), ra.TARGET_PEAK, delta=0.02)

    def test_missing_file_fails_loudly(self) -> None:
        sc = self.scene()
        sc["audio_file"] = "tracks/does_not_exist.mp3"
        cfg = {"sample_rate": 44100, "bitrate": 96, "channels": 1}
        with self.assertRaises(SystemExit):
            ra.render_scene_py(sc, cfg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
