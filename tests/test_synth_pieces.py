"""Do the assembled pieces line up with the lights, and survive the master?

Everything downstream of a single voice: placing a sound into a scene buffer,
the event markers the pieces hand to the light rig, the stone-hall reverb, and
the limiter the whole mix goes through.

_place() gets the most attention here because it is the only writer into a
scene buffer, and it eats every out-of-range time the pieces can compute — it
used to crash when heartbeat's jitter pulled the first thump to t < 0, where a
negative index silently wrapped to the end of the buffer.

The voices themselves are in test_synth_voices.py.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import synth as S
from helpers import MARKER_SYNTHS, peak, render_synth, rms, window_peaks


class TestPlace(unittest.TestCase):
    """Out-of-range placement must trim, never wrap and never throw."""

    def test_places_at_the_right_sample(self) -> None:
        buf = np.zeros(10)
        S._place(buf, np.ones(3), 4 / S.SR)
        self.assertTrue(np.array_equal(buf, [0, 0, 0, 0, 1, 1, 1, 0, 0, 0]))

    def test_negative_time_trims_the_head(self) -> None:
        """The audible remainder must land at 0, not wrap to the buffer end."""
        buf = np.zeros(10)
        S._place(buf, np.ones(6), -3 / S.SR)
        self.assertTrue(np.array_equal(buf, [1, 1, 1, 0, 0, 0, 0, 0, 0, 0]))

    def test_entirely_negative_time_writes_nothing(self) -> None:
        buf = np.zeros(10)
        S._place(buf, np.ones(6), -100.0)
        self.assertEqual(buf.sum(), 0.0)

    def test_time_past_the_end_writes_nothing(self) -> None:
        buf = np.zeros(10)
        S._place(buf, np.ones(6), 100.0)
        self.assertEqual(buf.sum(), 0.0)

    def test_signal_is_truncated_at_the_end(self) -> None:
        buf = np.zeros(10)
        S._place(buf, np.ones(6), 8 / S.SR)
        self.assertEqual(buf.sum(), 2.0)

    def test_empty_signal_is_a_no_op(self) -> None:
        buf = np.zeros(10)
        S._place(buf, np.array([]), 0.0)
        S._place(buf, np.array([]), -1.0)
        S._place(buf, np.array([]), 100.0)
        self.assertEqual(buf.sum(), 0.0)

    def test_overlapping_placements_sum(self) -> None:
        """The overlapping organ chords rely on this: it mixes, not pastes."""
        buf = np.zeros(10)
        S._place(buf, np.ones(4), 0.0)
        S._place(buf, np.ones(4), 2 / S.SR)
        self.assertEqual(buf[2], 2.0)
        self.assertEqual(buf.sum(), 8.0)

    def test_a_jittered_first_beat_keeps_its_tail(self) -> None:
        """The case that crashed: heartbeat draws +/-30 ms of jitter on every
        beat including the first, so the very first thump can start before the
        file does. What is left of it must still be heard."""
        buf = np.zeros(int(0.5 * S.SR))
        S._place(buf, np.ones(int(0.1 * S.SR)), -0.03)
        self.assertEqual(buf[0], 1.0)
        self.assertAlmostEqual(buf.sum() / S.SR, 0.07, places=3)


class TestMarkers(unittest.TestCase):
    """The (buf, marks) voices drive the lights. A marker outside the audio is
    a flash with nothing under it."""

    rendered: dict[str, tuple]

    @classmethod
    def setUpClass(cls) -> None:
        cls.rendered = {name: render_synth(name) for name in MARKER_SYNTHS}

    def test_only_the_expected_voices_report_markers(self) -> None:
        """render_audio.py branches on the tuple, so this is load-bearing."""
        for name in S.SYNTHS:
            reports = render_synth(name)[1] is not None
            with self.subTest(name):
                self.assertEqual(reports, name in MARKER_SYNTHS)

    def test_markers_fall_inside_the_buffer(self) -> None:
        for name, (buf, marks) in self.rendered.items():
            span = len(buf) / S.SR
            for t, _v in marks:
                with self.subTest(name=name, t=t):
                    self.assertGreaterEqual(t, 0.0)
                    self.assertLess(t, span, f"{name} marker at {t:.2f}s of {span:.2f}s")

    def test_velocities_are_normalised(self) -> None:
        """gen_esphome.py multiplies brightness by these; over 1.0 clips."""
        for name, (_buf, marks) in self.rendered.items():
            for t, v in marks:
                with self.subTest(name=name, t=t):
                    self.assertGreater(v, 0.0)
                    self.assertLessEqual(v, 1.0, f"{name} velocity {v}")

    def test_markers_are_in_time_order(self) -> None:
        """They are emitted as a timeline and nothing downstream sorts them."""
        for name, (_buf, marks) in self.rendered.items():
            times = [t for t, _v in marks]
            with self.subTest(name):
                self.assertEqual(times, sorted(times))

    def test_heartbeat_reports_both_thumps(self) -> None:
        """The light does lub-dub too, so the softer second thump is reported
        with its real loudness rather than folded into the first."""
        _buf, beats = render_synth("heartbeat", dur=5.0)
        self.assertEqual(len(beats), 8)
        self.assertEqual([v for _t, v in beats][:4], [1.00, 0.55, 1.00, 0.55])
        self.assertAlmostEqual(beats[1][0] - beats[0][0], 0.18, places=6)

    def test_heartbeat_markers_land_on_the_audio(self) -> None:
        """Jitter moves the sound; if the marker did not move with it the light
        would drift off the beat by up to 30 ms in either direction."""
        buf, beats = render_synth("heartbeat", dur=5.0)
        env = np.convolve(np.abs(buf), np.ones(441) / 441, mode="same")
        for t, _v in beats:
            i = int(t * S.SR)
            with self.subTest(t=round(t, 3)):
                self.assertGreater(env[i:i + 2205].max(), 0.05 * peak(buf),
                                   f"marker at {t:.2f}s has no thump under it")

    def test_organ_markers_are_the_chord_changes(self) -> None:
        _buf, marks = render_synth("organ")
        for got, want in zip([t for t, _v in marks], [0.0, 6.6, 13.2, 19.8]):
            self.assertAlmostEqual(got, want, places=6)

    def test_waltz_accents_the_phrase_starts(self) -> None:
        """Bars 1 and 5 carry the accent, so the light breathes in fours."""
        _buf, marks = render_synth("waltz")
        self.assertEqual([v for _t, v in marks], [1.0, 0.7, 0.7, 0.7] * 2)

    def test_toll_marks_the_strike(self) -> None:
        buf, marks = render_synth("toll")
        self.assertEqual(marks, [(0.0, 1.0)])
        self.assertGreater(peak(buf[:1000]), 0.0)

    def test_whispers_velocity_tracks_word_length(self) -> None:
        """Longer words are reported louder, which is what makes the light
        flicker in step with the syllables rather than at a constant level."""
        _buf, words = render_synth("whispers", dur=12.0)
        self.assertGreater(len(words), 3)
        lengths = [v for _t, v in words]
        self.assertGreaterEqual(min(lengths), 0.5)
        self.assertGreater(max(lengths) - min(lengths), 0.05, "every word the same")

    def test_whispers_too_short_to_fit_a_word_is_not_an_error(self) -> None:
        """The loop starts at 0.6 s and stops 1.0 s before the end."""
        buf, words = render_synth("whispers", dur=1.0)
        self.assertEqual(words, [])
        self.assertEqual(peak(buf), 0.0)

    def test_heartbeat_dub_marker_stays_inside_the_buffer(self) -> None:
        """Regression. The loop runs while t0 < dur but the dub lands at
        t0 + 0.18, so for any duration where dur % 1.25 < 0.18 — 2.6 s here —
        it used to be reported past the end of the audio. _place() drops the
        sound, so that was a light flash with nothing underneath, and an event
        scheduled past the end of the scene in gen_esphome.py.
        """
        buf, beats = render_synth("heartbeat", dur=2.6)
        span = len(buf) / S.SR
        self.assertTrue(all(t < span for t, _v in beats),
                        f"markers {[round(t, 2) for t, _ in beats]} run past {span}s")


class TestReverb(unittest.TestCase):
    """The stone hall runs over the whole mix, so it must not add energy it
    cannot account for, and must be a true bypass when it is switched off."""

    dry: np.ndarray

    @classmethod
    def setUpClass(cls) -> None:
        cls.dry, _ = render_synth("musicbox")

    def test_wet_zero_is_a_passthrough(self) -> None:
        out = S.apply_reverb(self.dry, 0.0, np.random.default_rng(1))
        self.assertTrue(np.array_equal(out, self.dry))

    def test_negative_wet_is_also_a_passthrough(self) -> None:
        out = S.apply_reverb(self.dry, -1.0, np.random.default_rng(1))
        self.assertTrue(np.array_equal(out, self.dry))

    def test_wet_changes_the_signal_but_stays_finite(self) -> None:
        out = S.apply_reverb(self.dry, 0.3, np.random.default_rng(1))
        self.assertTrue(np.all(np.isfinite(out)))
        self.assertFalse(np.array_equal(out, self.dry))

    def test_length_is_preserved(self) -> None:
        """The tail is trimmed to the input; scenes are cut to a fixed grid."""
        out = S.apply_reverb(self.dry, 0.5, np.random.default_rng(1))
        self.assertEqual(len(out), len(self.dry))

    def test_wet_scales_the_added_tail(self) -> None:
        a = S.apply_reverb(self.dry, 0.1, np.random.default_rng(1))
        b = S.apply_reverb(self.dry, 0.5, np.random.default_rng(1))
        self.assertGreater(rms(b - self.dry), rms(a - self.dry))

    def test_the_tail_cannot_run_away(self) -> None:
        """It is normalised against the dry peak, so the result stays bounded
        however long the impulse response rings."""
        out = S.apply_reverb(self.dry, 0.4, np.random.default_rng(1))
        self.assertLess(peak(out), 1.5 * peak(self.dry))

    def test_it_smears_energy_forwards_in_time(self) -> None:
        """The audible job: sound where there was none, after the last note."""
        gap = int(0.3 * S.SR)
        wet = S.apply_reverb(self.dry, 0.5, np.random.default_rng(1))
        self.assertGreater(rms(wet[-gap:]), rms(self.dry[-gap:]))

    def test_silence_stays_silent(self) -> None:
        out = S.apply_reverb(np.zeros(S.SR), 0.4, np.random.default_rng(1))
        self.assertEqual(peak(out), 0.0)

    def test_it_is_reproducible(self) -> None:
        a = S.apply_reverb(self.dry, 0.3, np.random.default_rng(11))
        b = S.apply_reverb(self.dry, 0.3, np.random.default_rng(11))
        self.assertTrue(np.array_equal(a, b))

    def test_the_impulse_response_decays(self) -> None:
        ir = S.reverb_ir(1.0, 2.4, np.random.default_rng(1))
        self.assertEqual(len(ir), S.SR)
        self.assertGreater(peak(ir[:1000]), 0.5)
        self.assertLess(peak(ir[-1000:]), 0.01)

    def test_a_longer_decay_exponent_shortens_the_tail(self) -> None:
        slow = S.reverb_ir(1.0, 1.0, np.random.default_rng(1))
        fast = S.reverb_ir(1.0, 6.0, np.random.default_rng(1))
        self.assertLess(rms(fast), rms(slow))


class TestLimiter(unittest.TestCase):
    """Two overlapping organ chords clip without it; it must also stay out of
    the way of anything already under the ceiling."""

    loud: np.ndarray
    quiet: np.ndarray

    @classmethod
    def setUpClass(cls) -> None:
        t = np.arange(3 * S.SR) / S.SR
        cls.loud = 2.5 * np.sin(2 * np.pi * 220 * t)
        cls.quiet = 0.2 * np.sin(2 * np.pi * 220 * t)

    def test_loud_material_is_brought_under_the_ceiling(self) -> None:
        out = S.limit(self.loud)
        self.assertLessEqual(peak(out), 1.0)
        self.assertTrue(np.all(np.isfinite(out)))

    def test_a_lower_ceiling_gives_a_quieter_result(self) -> None:
        self.assertLess(rms(S.limit(self.loud, ceiling=0.3)),
                        rms(S.limit(self.loud, ceiling=0.89)))

    def test_quiet_material_passes_through_untouched(self) -> None:
        """Sampled away from the edges — see the taper test at the bottom."""
        out = S.limit(self.quiet)
        w = int(0.02 * S.SR)
        self.assertTrue(np.allclose(out[w:-w], self.quiet[w:-w], atol=1e-12))

    def test_the_gain_ride_is_steady_on_steady_material(self) -> None:
        """The point of smoothing the gain twice. A gain that wobbles from one
        window to the next is the limiter pumping, and you hear that instead
        of the music."""
        ride = window_peaks(S.limit(self.loud), 10.0) / window_peaks(self.loud, 10.0)
        self.assertLess(float(np.max(np.abs(np.diff(ride)))), 1e-3)
        self.assertLess(ride.max(), 1.0, "nothing was limited at all")

    def test_silence_is_returned_unchanged(self) -> None:
        z = np.zeros(1000)
        self.assertTrue(np.array_equal(S.limit(z), z))

    def test_a_real_mix_survives_the_limiter(self) -> None:
        """The actual case it exists for: organ chords stacked on themselves."""
        mix, _ = render_synth("organ")
        out = S.limit(mix + mix)
        self.assertTrue(np.all(np.isfinite(out)))
        self.assertLessEqual(peak(out), 1.0)
        self.assertGreater(rms(out), 0.5 * rms(mix))

    def test_limit_does_not_fade_the_edges_of_quiet_audio(self) -> None:
        """FIXED (regression guard): the gain curve is smoothed with a
        `mode="same"` convolution, which treats off-the-end as zero. So even
        when no limiting is called for, the first and last ~10 ms are faded —
        the very first sample comes out at roughly half gain. Harmless as a
        de-click on a one-shot, but wind and drone are looped, so the seam
        takes a dip every time round. Normalising the smoothing kernel by its
        overlap (or padding with the edge value) would fix it.
        """
        out = S.limit(self.quiet)
        self.assertAlmostEqual(peak(out[:200]), peak(self.quiet[:200]), places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
