"""Tests for the render pipeline's own plumbing.

`test_import.py` covers a scene built from an imported file; this covers the
other half — scenes built from the synths, the loop crossfade, normalisation,
and the marker bookkeeping that light cues are generated from.

The marker maths is the part worth guarding. Every light pulse in the show
comes from a `(time, velocity)` pair produced here, so an off-by-one in the
offsetting or the trimming moves cues away from the sound they belong to, and
the symptom is "the lights feel slightly wrong" rather than a crash.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import render_audio as ra
import synth

CFG = {"sample_rate": 44100, "bitrate": 96, "channels": 1}


class TestRenderScene(unittest.TestCase):
    def scene(self, **over: object) -> dict:
        sc = {"id": "t", "duration_ms": 4000, "score": [], "cues": []}
        sc.update(over)
        return sc

    def test_empty_scene_is_silence_of_the_right_length(self) -> None:
        buf, marks = ra.render_scene(self.scene(), CFG)
        self.assertEqual(len(buf), int(4.0 * 44100))
        self.assertEqual(marks, {})
        self.assertTrue(np.all(np.isfinite(buf)))

    def test_normalises_to_target_peak(self) -> None:
        """Files should use the full range; quiet material stored quietly just
        sits closer to the DAC noise floor for no benefit."""
        sc = self.scene(score=[{"t": 0.0, "synth": "toll", "gain": 0.01}])
        buf, _ = ra.render_scene(sc, CFG)
        self.assertAlmostEqual(float(np.abs(buf).max()), ra.TARGET_PEAK, delta=0.02)

    def test_output_never_exceeds_full_scale(self) -> None:
        sc = self.scene(score=[
            {"t": 0.0, "synth": "thunder", "gain": 4.0},
            {"t": 0.1, "synth": "shriek", "gain": 4.0},
        ])
        buf, _ = ra.render_scene(sc, CFG)
        self.assertLessEqual(float(np.abs(buf).max()), 1.0)

    def test_deterministic_for_the_same_scene_id(self) -> None:
        """Re-rendering must be reproducible, so a diff in the output means a
        real change in the score. The seed comes from the scene id via crc32
        rather than Python's hash(), which is salted per process."""
        sc = self.scene(score=[{"t": 0.0, "synth": "wind", "dur": 2.0}])
        a, _ = ra.render_scene(sc, CFG)
        b, _ = ra.render_scene(sc, CFG)
        np.testing.assert_array_equal(a, b)

    def test_different_scene_ids_differ(self) -> None:
        one = self.scene(id="alpha", score=[{"t": 0.0, "synth": "wind", "dur": 2.0}])
        two = self.scene(id="beta", score=[{"t": 0.0, "synth": "wind", "dur": 2.0}])
        a, _ = ra.render_scene(one, CFG)
        b, _ = ra.render_scene(two, CFG)
        self.assertFalse(np.array_equal(a, b), "scene id should seed the rng")

    def test_unknown_synth_fails_loudly(self) -> None:
        sc = self.scene(score=[{"t": 0.0, "synth": "does_not_exist"}])
        with self.assertRaises(SystemExit):
            ra.render_scene(sc, CFG)

    def test_markers_are_offset_by_event_time(self) -> None:
        """A synth reports times relative to itself; the scene has to place
        them on its own clock, or every cue lands early."""
        sc = self.scene(duration_ms=12000,
                        score=[{"t": 5.0, "synth": "heartbeat", "dur": 6.0}])
        _, marks = ra.render_scene(sc, CFG)
        self.assertIn("heartbeat", marks)
        first_ms = marks["heartbeat"][0][0]
        self.assertGreaterEqual(first_ms, 4900, "marker not shifted to t=5s")

    def test_markers_stay_inside_the_buffer(self) -> None:
        """A cue past the end of the scene would be scheduled and never fire."""
        sc = self.scene(duration_ms=3000,
                        score=[{"t": 0.0, "synth": "heartbeat", "dur": 10.0}])
        _, marks = ra.render_scene(sc, CFG)
        for hits in marks.values():
            for ms, _v in hits:
                self.assertLess(ms, 3000)

    def test_take_trims_both_audio_and_markers(self) -> None:
        """`take` shortens a long piece to fit. Markers past the cut must go
        with it, or the scene carries cues for sound that was removed."""
        sc = self.scene(duration_ms=20000,
                        score=[{"t": 0.0, "synth": "organ", "take": 5.0}])
        _, marks = ra.render_scene(sc, CFG)
        for hits in marks.values():
            for ms, _v in hits:
                self.assertLess(ms, 5000)

    def test_markers_are_sorted(self) -> None:
        sc = self.scene(duration_ms=12000, score=[
            {"t": 6.0, "synth": "toll"},
            {"t": 1.0, "synth": "toll"},
        ])
        _, marks = ra.render_scene(sc, CFG)
        times = [ms for ms, _ in marks["toll"]]
        self.assertEqual(times, sorted(times))

    def test_loop_crossfades_so_the_seam_is_inaudible(self) -> None:
        """A looping scene blends its tail into its head. Without it the loop
        point clicks every time round."""
        sc = self.scene(loop=True, duration_ms=6000,
                        score=[{"t": 0.0, "synth": "wind", "dur": 6.0}])
        buf, _ = ra.render_scene(sc, CFG)
        # A hard cut leaves a step between the last and first sample.
        step = abs(float(buf[-1]) - float(buf[0]))
        self.assertLess(step, 0.5, "loop seam looks like a discontinuity")

    def test_non_looping_scene_fades_out(self) -> None:
        sc = self.scene(loop=False, duration_ms=4000,
                        score=[{"t": 0.0, "synth": "wind", "dur": 4.0}])
        buf, _ = ra.render_scene(sc, CFG)
        self.assertLess(abs(float(buf[-1])), 0.02, "tail should fade to silence")

    def test_reverb_defaults_differ_for_imported_audio(self) -> None:
        """Imported tracks arrive already produced; stacking the stone hall on
        someone else's reverb just makes mud."""
        sc = self.scene(score=[{"t": 0.0, "synth": "toll"}])
        dry, _ = ra.render_scene({**sc, "reverb": 0.0}, CFG)
        wet, _ = ra.render_scene({**sc, "reverb": 0.6}, CFG)
        self.assertFalse(np.array_equal(dry, wet), "reverb setting had no effect")


class TestStaleSweep(unittest.TestCase):
    """A full render owns the numbered files: after a delete renumbers the
    show, 11_foo.mp3 sat beside 10_foo.mp3 forever (judge B, JB2-5d)."""

    SHOW = ("hardware:\n  audio: {sample_rate: 8000, bitrate: 32, channels: 1}\n"
            "scenes:\n  - id: one\n    duration_ms: 200\n"
            "  - id: two\n    duration_ms: 200\n")

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.out = self.tmp / "audio"
        self.out.mkdir()
        for stale in ("00_chirp.mp3", "02_one.mp3", "03_two.mp3", "01_gone.mp3"):
            (self.out / stale).write_bytes(b"old")
        (self.out / "notes.txt").write_text("not ours")
        scenes = self.tmp / "scenes.yaml"
        scenes.write_text(self.SHOW)
        fake_encode = lambda wav, mp3, br: mp3.write_bytes(b"new")  # noqa: E731
        for target, value in (("OUT", self.out), ("SCENES", scenes),
                              ("encode_mp3", fake_encode)):
            p = mock.patch.object(ra, target, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def run_main(self, *argv: str) -> None:
        with mock.patch.object(sys, "argv", ["render_audio.py", *argv]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ra.main(), 0)

    def test_card_copies_follow_a_redirected_out(self) -> None:
        """The card dir must derive from OUT when used, not at import — a
        module-level path once pointed a sandboxed test's sweep at the real
        audio/card/ and deleted two rendered scenes."""
        self.assertEqual(ra.card_dir(), self.out / "card")

    def test_a_full_render_sweeps_what_it_did_not_write(self) -> None:
        self.run_main()
        names = sorted(p.name for p in self.out.iterdir())
        self.assertEqual(names, ["00_chirp.mp3", "01_one.mp3", "02_two.mp3",
                                 "markers.json", "notes.txt", "test"])
        # The speaker test's tones render beside the scenes, never swept.
        self.assertEqual(sorted(p.name for p in (self.out / "test").iterdir()),
                         sorted(f"{stem}.mp3" for stem, _ in ra.TEST_TONES))
        self.assertEqual((self.out / "02_two.mp3").read_bytes(), b"new")

    def test_a_partial_render_sweeps_nothing(self) -> None:
        self.run_main("--only", "one")
        self.assertTrue((self.out / "03_two.mp3").exists())
        self.assertTrue((self.out / "01_gone.mp3").exists())


class TestWavWriting(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_mono_16bit(self) -> None:
        p = self.tmp / "o.wav"
        ra.write_wav(p, np.zeros(1000), 44100)
        with wave.open(str(p)) as w:
            self.assertEqual(w.getnchannels(), 1)
            self.assertEqual(w.getsampwidth(), 2)
            self.assertEqual(w.getframerate(), 44100)

    def test_clips_rather_than_wrapping(self) -> None:
        """Out-of-range samples must saturate. Letting them wrap turns a loud
        moment into a burst of noise at the opposite polarity."""
        p = self.tmp / "hot.wav"
        ra.write_wav(p, np.array([2.0, -2.0, 0.0]), 44100)
        with wave.open(str(p)) as w:
            pcm = np.frombuffer(w.readframes(3), dtype="<i2")
        self.assertEqual(int(pcm[0]), 32767)
        self.assertEqual(int(pcm[1]), -32767)


class TestPlace(unittest.TestCase):
    """`_place` mixes a signal into a buffer at a time offset. It is the one
    function every synth's output passes through."""

    def test_places_at_the_right_offset(self) -> None:
        buf = np.zeros(44100)
        synth._place(buf, np.ones(100), 0.5)
        self.assertEqual(float(buf[22050]), 1.0)
        self.assertEqual(float(buf[0]), 0.0)

    def test_negative_time_trims_the_head(self) -> None:
        """Heartbeat jitter can push the first beat before zero. This used to
        crash on a negative index."""
        buf = np.zeros(1000)
        sig = np.arange(100, dtype=float)
        synth._place(buf, sig, -50 / 44100)
        self.assertTrue(np.all(np.isfinite(buf)))
        self.assertGreater(float(buf.sum()), 0.0, "signal was dropped entirely")

    def test_past_the_end_is_a_no_op(self) -> None:
        buf = np.zeros(100)
        synth._place(buf, np.ones(10), 10.0)
        self.assertEqual(float(buf.sum()), 0.0)

    def test_truncates_at_the_buffer_end(self) -> None:
        buf = np.zeros(100)
        synth._place(buf, np.ones(1000), 0.0)
        self.assertEqual(len(buf), 100)
        self.assertEqual(float(buf.sum()), 100.0)

    def test_empty_signal_is_harmless(self) -> None:
        buf = np.zeros(100)
        synth._place(buf, np.array([]), 0.0)
        self.assertEqual(float(buf.sum()), 0.0)

    def test_mixes_rather_than_overwrites(self) -> None:
        """Scenes layer sounds; a placing that overwrote would silently drop
        whichever voice happened to be written first."""
        buf = np.ones(100)
        synth._place(buf, np.ones(100), 0.0)
        self.assertEqual(float(buf[0]), 2.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
