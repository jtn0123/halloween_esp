"""Does the audio analysis give the right answers?

Onset detection, the beatless-material envelope fallback, density fitting,
and the waveform the clip editor draws. These are the parts where a wrong
number produces lights that simply do not match the music.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import analyze as ana
import import_track as it
from helpers import make_click_track


class TestOnsetDetection(unittest.TestCase):
    """The detector is the reason imported tracks get light at all."""

    tmp: Path
    wav: Path
    beats: list[float]

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        cls.wav = cls.tmp / "click.wav"
        cls.beats = make_click_track(cls.wav, seconds=6.0, bpm=120.0)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_finds_the_kicks(self) -> None:
        marks = ana.analyze_file(self.wav)
        low = marks.get("onset_low", [])
        self.assertEqual(
            len(low),
            len(self.beats),
            f"expected {len(self.beats)} kicks, found {len(low)}",
        )

    def test_onsets_land_on_the_beat(self) -> None:
        """Within 25 ms — tighter than anyone can see in a light cue."""
        low = ana.analyze_file(self.wav)["onset_low"]
        for expected, (got, _vel) in zip(self.beats, low):
            self.assertLess(
                abs(got - expected),
                0.025,
                f"onset at {got:.3f}s, expected {expected:.3f}s",
            )

    def test_downbeat_at_zero_is_not_lost(self) -> None:
        """Frame 0 has nothing to diff against; a loop's downbeat lives there."""
        low = ana.analyze_file(self.wav)["onset_low"]
        self.assertLess(low[0][0], 0.02, "the t=0 onset was dropped")

    def test_velocities_are_normalised(self) -> None:
        for band, hits in ana.analyze_file(self.wav).items():
            for t, v in hits:
                self.assertGreaterEqual(v, 0.0, band)
                self.assertLessEqual(v, 1.0, f"{band} velocity {v} above 1.0")

    def test_bands_are_separated(self) -> None:
        """Kick in the low band, hats in the high — the whole point of banding."""
        marks = ana.analyze_file(self.wav)
        self.assertIn("onset_low", marks)
        self.assertIn("onset_high", marks)

    def test_silence_yields_nothing(self) -> None:
        p = self.tmp / "silence.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(ana.SR)
            w.writeframes((np.zeros(ana.SR * 2)).astype("<i2").tobytes())
        self.assertEqual(
            ana.analyze_file(p),
            {},
            "silence should produce no onsets, not phantom ones",
        )

    def test_too_short_is_handled(self) -> None:
        """A clip shorter than one analysis window must not throw."""
        p = self.tmp / "tiny.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(ana.SR)
            w.writeframes((np.zeros(64)).astype("<i2").tobytes())
        self.assertEqual(ana.analyze_file(p), {})

    def test_sensitivity_changes_the_count(self) -> None:
        loose = ana.analyze_file(self.wav, sensitivity=0.3)
        tight = ana.analyze_file(self.wav, sensitivity=3.0)
        self.assertGreaterEqual(
            sum(len(v) for v in loose.values()),
            sum(len(v) for v in tight.values()),
            "lower sensitivity should find at least as many onsets",
        )


class TestEnvelopeFallback(unittest.TestCase):
    """Beatless material must still move the lights.

    Drones, pads and wind never "start", so onset detection correctly finds
    nothing — and the scene sits on a static base doing nothing. Most of this
    castle's material is atmospheric, so that gap mattered.
    """

    tmp: Path
    drone: Path

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp())
        cls.drone = cls.tmp / "drone.wav"
        sr = ana.SR
        t = np.arange(int(12.0 * sr)) / sr
        tone = 0.5 * np.sin(2 * np.pi * 73.4 * t) + 0.3 * np.sin(2 * np.pi * 110 * t)
        swell = 0.25 + 0.75 * (0.5 + 0.5 * np.sin(2 * np.pi * 0.12 * t))
        x = tone * swell * 0.6
        with wave.open(str(cls.drone), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(sr)
            w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_a_drone_has_no_usable_onsets(self) -> None:
        """The premise. If this ever fails, the fallback is not needed."""
        marks = ana.analyze_file(self.drone)
        self.assertLess(len(marks.get("onset_low", [])), ana.BEATLESS)

    def test_envelope_is_produced_instead(self) -> None:
        full = ana.analyze_full(ana.load_audio(self.drone))
        self.assertIn("level_low", full)
        self.assertGreater(len(full["level_low"]), 20)

    def test_envelope_follows_the_swell(self) -> None:
        levels = [
            v for _t, v in ana.analyze_full(ana.load_audio(self.drone))["level_low"]
        ]
        self.assertGreater(
            max(levels) - min(levels),
            0.4,
            "envelope is flat — it is not tracking anything",
        )

    def test_envelope_is_normalised(self) -> None:
        for _t, v in ana.analyze_full(ana.load_audio(self.drone))["level_low"]:
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)

    def test_beaty_material_gets_no_envelope(self) -> None:
        """A track with a clear beat should use onsets, not loudness."""
        click = self.tmp / "click.wav"
        make_click_track(click, seconds=8.0, bpm=120.0)
        full = ana.analyze_full(ana.load_audio(click))
        self.assertNotIn(
            "level_low", full, "envelope applied to material that has real onsets"
        )

    def test_scene_block_uses_gliding_decay_for_envelopes(self) -> None:
        """An envelope must glide. Beat decay would chop a swell into steps."""
        full = ana.analyze_full(ana.load_audio(self.drone))
        block = it.scene_block("drone", 12.0, full)
        line = next(ln for ln in block.splitlines() if "level_low" in ln)
        self.assertIn("decay: 0.9", line)

    def test_silence_produces_no_envelope(self) -> None:
        p = self.tmp / "silent.wav"
        with wave.open(str(p), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(ana.SR)
            w.writeframes(np.zeros(ana.SR * 3).astype("<i2").tobytes())
        self.assertEqual(ana.analyze_full(ana.load_audio(p)), {})


class TestPerBandSensitivity(unittest.TestCase):
    """One threshold cannot serve three bands.

    Real material routinely has a crisp kick under a wash of cymbals: a value
    that finds the bass cleanly buries the top end in false hits, and one that
    cleans up the top throws the bass away. The detector takes a scalar or a
    per-band map, and a scalar has to keep meaning exactly what it did.
    """

    def test_a_scalar_applies_to_every_band(self) -> None:
        for name in ("onset_low", "onset_mid", "onset_high"):
            self.assertEqual(ana.band_sensitivity(1.7, name), 1.7)

    def test_a_map_is_read_per_band(self) -> None:
        s = {"onset_low": 0.6, "onset_mid": 1.1, "onset_high": 2.4}
        self.assertEqual(ana.band_sensitivity(s, "onset_low"), 0.6)
        self.assertEqual(ana.band_sensitivity(s, "onset_high"), 2.4)

    def test_short_band_names_work_too(self) -> None:
        """The CLI and the query string both spell them without the prefix."""
        s = {"low": 0.6, "high": 2.4}
        self.assertEqual(ana.band_sensitivity(s, "onset_low"), 0.6)
        self.assertEqual(ana.band_sensitivity(s, "onset_high"), 2.4)

    def test_an_unnamed_band_falls_back_rather_than_failing(self) -> None:
        self.assertEqual(ana.band_sensitivity({"low": 0.6}, "onset_mid"), 1.1)
        self.assertEqual(ana.band_sensitivity(None, "onset_mid"), 1.1)

    def test_lowering_one_band_only_moves_that_band(self) -> None:
        """The point of the feature: tuning the top end must not silently
        re-detect the bass underneath it."""
        tmp = Path(tempfile.mkdtemp())
        try:
            wav = tmp / "click.wav"
            make_click_track(wav, seconds=4.0)
            x = ana.load_audio(wav)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        base = ana.analyze(x, sensitivity=1.1)
        loosened = ana.analyze(x, sensitivity={"low": 1.1, "mid": 1.1, "high": 0.4})
        self.assertEqual(
            len(base.get("onset_low", [])),
            len(loosened.get("onset_low", [])),
            "the low band moved when only high was changed",
        )
        self.assertGreaterEqual(
            len(loosened.get("onset_high", [])), len(base.get("onset_high", []))
        )


class TestDensityFitting(unittest.TestCase):
    """Pulse settings must suit the track, not Crypt's 48 bpm heartbeat.

    A real import fired 212 times a minute. At the built-in decay of 0.92 each
    flash was still at 28% when the next arrived, so the zone saturated and
    read as a smear instead of pulses — the exact way an imported track stops
    looking like it follows the music.
    """

    @staticmethod
    def hits(gap: float, n: int = 40) -> list[tuple[float, float]]:
        return [(i * gap, 1.0) for i in range(n)]

    def residual(self, decay: float, gap: float) -> float:
        """How much of a flash survives to the next hit."""
        return float(decay ** (gap / it.FRAME))

    def test_dense_material_decays_faster(self) -> None:
        decay, _ = it.fit_to_density(self.hits(0.24), 0.92)
        self.assertLess(decay, 0.92)
        self.assertLess(
            self.residual(decay, 0.24), 0.15, "a dense band still saturates"
        )

    def test_sparse_material_keeps_its_bloom(self) -> None:
        """A slow bell toll should not be sped up into a blink."""
        decay, scale = it.fit_to_density(self.hits(3.0), 0.972)
        self.assertEqual(decay, 0.972)
        self.assertEqual(scale, 1.0)

    def test_dense_material_is_eased_back(self) -> None:
        _, dense = it.fit_to_density(self.hits(0.2), 0.92)
        _, sparse = it.fit_to_density(self.hits(1.0), 0.92)
        self.assertLess(dense, sparse)
        self.assertGreaterEqual(dense, 0.45, "never eased into invisibility")

    def test_decay_has_a_floor(self) -> None:
        """Faster than ~0.78 per frame is a blink nobody perceives."""
        decay, _ = it.fit_to_density(self.hits(0.02), 0.92)
        self.assertGreaterEqual(decay, 0.78)

    def test_too_few_hits_falls_back(self) -> None:
        self.assertEqual(it.fit_to_density([(0.0, 1.0)], 0.9), (0.9, 1.0))
        self.assertEqual(it.fit_to_density([], 0.9), (0.9, 1.0))

    def test_identical_timestamps_do_not_divide_by_zero(self) -> None:
        same = [(1.0, 1.0)] * 5
        self.assertEqual(it.fit_to_density(same, 0.9), (0.9, 1.0))


class TestStudioMedia(unittest.TestCase):
    """Probe and waveform, without standing up a server."""

    tmp: Path
    wav: Path

    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(ROOT / "tools"))
        cls.tmp = Path(tempfile.mkdtemp())
        cls.wav = cls.tmp / "w.wav"
        make_click_track(cls.wav, seconds=5.0)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_waveform_shape(self) -> None:
        import studio_media as sm

        d = sm.waveform(self.wav, buckets=200)
        self.assertAlmostEqual(d["duration"], 5.0, delta=0.1)
        self.assertEqual(len(d["peaks"]), 200)
        self.assertLessEqual(max(d["peaks"]), 1.0)
        self.assertAlmostEqual(
            max(d["peaks"]),
            1.0,
            delta=1e-6,
            msg="peaks should be normalised to the loudest",
        )

    def test_waveform_includes_onsets(self) -> None:
        import studio_media as sm

        d = sm.waveform(self.wav)
        self.assertIn("onset_low", d["onsets"])
        for t, v, *rest in d["onsets"]["onset_low"]:
            self.assertGreaterEqual(t, 0.0)
            self.assertLessEqual(v, 1.0)
            # The optional third element is the hit's pan, -1..1. The test
            # fixture is mono, so any pan present must read centre.
            for pan in rest:
                self.assertEqual(pan, 0.0)

    def test_probe_rejects_non_links_without_touching_the_network(self) -> None:
        import studio_media as sm

        r = sm.probe("not a url")
        self.assertFalse(r["ok"])
        self.assertIn("link", r["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
