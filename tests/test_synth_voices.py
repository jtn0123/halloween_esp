"""Do the individual synth voices produce audio we can ship?

These files are baked once and then live in flash, so a bad render is not
something anyone notices until the castle is running. The assertions here are
the ones that would have caught the failures we have actually had: a tremulant
summed into the gain envelope, which drove gain negative on release and buzzed;
and silence out of a voice whose envelope knees had gone non-monotonic.

Pitch, filters, the three note voices, then every entry of the registry rendered
once and checked for the things that make a file unusable. The pieces that
assemble these voices into a scene — placement, markers, the room and the
master limiter — are in test_synth_pieces.py.

Everything renders short: the point is the shape of the signal, not its length.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import synth as S              # noqa: E402
from helpers import (          # noqa: E402
    SYNTH_DUR, TONAL_SYNTHS, peak, render_synth, window_peaks)


def organ_carrier(f: float, dur: float) -> np.ndarray:
    """The un-enveloped pipe sum, rebuilt from the RANKS table.

    Dividing pipe() by this recovers the gain stage, which is the only way to
    inspect the envelope from outside the module.
    """
    t = np.arange(int(dur * S.SR)) / S.SR
    out = np.zeros_like(t)
    for m, amp, upper in S.RANKS:
        fr = f * m * (1.0 if m == 1.0 else 1.0012)
        if fr < 24 or fr > 11000:
            continue
        a = amp * (S.STOPS if upper else 1.0)
        if a < 0.012:
            continue
        out += a * np.sin(2 * np.pi * fr * t)
    return out


class TestNoteHelper(unittest.TestCase):
    """nt() is the only thing standing between a written score and a pitch."""

    def test_zero_is_a3(self) -> None:
        self.assertAlmostEqual(S.nt(0), 220.0)

    def test_octaves_double_and_halve(self) -> None:
        self.assertAlmostEqual(S.nt(12), 440.0)
        self.assertAlmostEqual(S.nt(-12), 110.0)
        self.assertAlmostEqual(S.nt(-24), 55.0)

    def test_a_semitone_is_the_twelfth_root(self) -> None:
        """If this drifts, every chord in organ() and waltz() drifts with it."""
        self.assertAlmostEqual(S.nt(1) / S.nt(0), 2 ** (1 / 12))

    def test_the_scored_pedals_are_where_the_docstrings_claim(self) -> None:
        """organ() calls nt(-19) 'D minor'; descent()'s 32' pedal is nt(-31)."""
        self.assertAlmostEqual(S.nt(-19), 73.416, places=2)     # D2
        self.assertAlmostEqual(S.nt(-31), 36.708, places=2)     # D1


class TestFilters(unittest.TestCase):
    """The filters are shared by every noise voice; a NaN here poisons the mix.

    Their corner frequencies are computed, not written down — whispers draws
    formants at random and thunder sweeps — so the clamping matters as much as
    the response.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.t = np.arange(2 * S.SR) / S.SR
        cls.low = np.sin(2 * np.pi * 200 * cls.t)
        cls.high = np.sin(2 * np.pi * 8000 * cls.t)

    def test_lowpass_passes_low_and_stops_high(self) -> None:
        self.assertGreater(float(np.sqrt(np.mean(S._lp(self.low, 1000) ** 2))), 0.6)
        self.assertLess(float(np.sqrt(np.mean(S._lp(self.high, 1000) ** 2))), 0.05)

    def test_lowpass_clamps_above_nyquist(self) -> None:
        """An out-of-range corner must clamp, not fail the filter design."""
        self.assertTrue(np.all(np.isfinite(S._lp(self.low, 40000.0))))

    def test_bandpass_passes_its_centre(self) -> None:
        band = S._bp(np.sin(2 * np.pi * 1000 * self.t), 1000, q=2)
        self.assertGreater(float(np.sqrt(np.mean(band ** 2))), 0.5)

    def test_bandpass_rejects_out_of_band(self) -> None:
        band = S._bp(np.sin(2 * np.pi * 80 * self.t), 1000, q=2)
        self.assertLess(float(np.sqrt(np.mean(band ** 2))), 0.05)

    def test_bandpass_survives_absurd_centres(self) -> None:
        for hz in (1.0, 5.0, 30000.0, 1e6):
            with self.subTest(hz=hz):
                self.assertTrue(np.all(np.isfinite(S._bp(self.low, hz))))

    def test_sweep_darkens_over_time(self) -> None:
        """thunder rolls 900 Hz down to 70 Hz — the tail must be duller."""
        x = np.random.default_rng(0).uniform(-1, 1, S.SR)
        out = S._sweep_lp(x, 900, 70)
        self.assertEqual(len(out), len(x))
        self.assertTrue(np.all(np.isfinite(out)))
        self.assertLess(float(np.sqrt(np.mean(out[-4000:] ** 2))),
                        float(np.sqrt(np.mean(out[:4000] ** 2))))


class TestTremulant(unittest.TestCase):
    """The organ's tremulant, which is where the buzz came from.

    Adding the LFO to the gain envelope gives gain = env + depth*sin(...), and
    at the ends of a 7.5 s chord env falls below the depth — so the gain crosses
    zero and the whole pipe sum inverts. That reads as a hard buzz on every
    chord release. The fix was a separate multiplicative stage; these tests
    check the property that tells the two apart.
    """

    F = S.nt(-19)
    D = 7.5                      # exactly what organ() and descent() ask for

    @classmethod
    def setUpClass(cls) -> None:
        cls.out = S.pipe(cls.F, cls.D, 1.0)
        cls.carrier = organ_carrier(cls.F, cls.D)

    def gain(self) -> np.ndarray:
        """The gain stage, recovered by dividing out the raw rank sum. Only
        sampled away from the carrier's zero crossings, where it is stable."""
        keep = np.abs(self.carrier) > 0.3 * peak(self.carrier)
        return self.out[keep] / self.carrier[keep]

    def test_the_recovered_gain_is_never_negative(self) -> None:
        self.assertGreaterEqual(self.gain().min(), -1e-9,
                                "gain went negative — the tremulant is summed, not multiplied")

    def test_the_recovered_gain_stays_within_the_trem_depth(self) -> None:
        self.assertLessEqual(self.gain().max(), 1.0 + S.TREM_DEPTH + 1e-6)

    def test_a_summed_lfo_would_actually_have_gone_negative(self) -> None:
        """Guards the test above: if the additive form happened to be harmless
        at this duration, that assertion would be proving nothing."""
        t = np.arange(int(self.D * S.SR)) / S.SR
        atk, rel = min(1.7, self.D * 0.20), min(2.6, self.D * 0.34)
        env = np.interp(t, [0, atk, max(atk + 0.05, self.D - rel), self.D], [0, 1, 1, 0])
        self.assertLess((env + S.TREM_DEPTH * np.sin(2 * np.pi * S.TREM_HZ * t)).min(), 0.0)

    def test_the_note_actually_dies_away(self) -> None:
        """A summed LFO leaves a residual ripple of TREM_DEPTH times the rank
        sum ringing on after the release, instead of silence."""
        env = window_peaks(self.out)
        self.assertLess(env[-1], 0.02 * peak(self.out), "the release never reaches silence")

    def test_the_trem_depth_cannot_invert_a_multiplicative_gain(self) -> None:
        self.assertLess(S.TREM_DEPTH, 1.0)
        self.assertGreater(S.TREM_DEPTH, 0.0)

    def test_the_swell_rises_before_it_falls(self) -> None:
        env = window_peaks(self.out)
        self.assertLess(env[0], env[len(env) // 2])
        self.assertLess(env[-1], env[len(env) // 2])

    def test_short_notes_still_get_a_whole_envelope(self) -> None:
        """The knees are all fractions of dur, and a short note squeezes them
        together — they must stay in order or np.interp returns nonsense."""
        for dur in (0.3, 1.0, 3.0):
            with self.subTest(dur=dur):
                env = window_peaks(S.pipe(self.F, dur, 1.0), 10.0)
                self.assertTrue(np.all(np.isfinite(env)))
                self.assertGreater(env.max(), 0.5)
                self.assertLess(env[-1], 0.25 * env.max())

    def test_quiet_stops_drop_the_upper_chorus(self) -> None:
        """With the stops nearly closed the upper ranks fall under the 0.012
        floor and are skipped entirely — the note must still sound."""
        soft = S.pipe(self.F, 1.0, 0.5, stops=0.01)
        self.assertTrue(np.all(np.isfinite(soft)))
        self.assertGreater(peak(soft), 0.01)

    def test_a_pedal_below_hearing_is_skipped_not_rendered(self) -> None:
        """descent() draws a 32' rank on nt(-31), whose 0.25 multiple is 9 Hz."""
        out = S.pipe(S.nt(-31), 2.0, 0.5)
        self.assertTrue(np.all(np.isfinite(out)))
        self.assertGreater(peak(out), 0.01)


class TestKeyboardVoices(unittest.TestCase):
    """piano() and box() are only reached through waltz(), so their edges —
    partials running off the top of the range — need direct exercise."""

    def test_piano_is_finite_and_decays(self) -> None:
        out = S.piano(S.nt(0), 1.5, 0.5)
        self.assertTrue(np.all(np.isfinite(out)))
        env = window_peaks(out)
        self.assertLess(env[-1], 0.2 * env.max())

    def test_piano_starts_from_zero(self) -> None:
        """Without the 6 ms attack ramp every note begins with a click."""
        self.assertEqual(S.piano(S.nt(0), 1.0, 0.5)[0], 0.0)

    def test_piano_stops_adding_partials_above_the_range(self) -> None:
        out = S.piano(S.nt(36), 0.5, 0.5)
        self.assertTrue(np.all(np.isfinite(out)))
        self.assertGreater(peak(out), 0.0)

    def test_box_rings_longer_for_longer_notes(self) -> None:
        """The decay is scaled by dur, so a 3 s strike must not be a 1 s one."""
        short = window_peaks(S.box(S.nt(12), 1.0, 0.5))
        long = window_peaks(S.box(S.nt(12), 3.0, 0.5))
        self.assertGreater(len(long[long > 0.02]), len(short[short > 0.02]))

    def test_box_skips_partials_off_the_top(self) -> None:
        out = S.box(S.nt(48), 0.5, 0.5)
        self.assertTrue(np.all(np.isfinite(out)))
        self.assertGreater(peak(out), 0.0)

    def test_velocity_scales_linearly(self) -> None:
        self.assertAlmostEqual(peak(S.box(S.nt(12), 1.0, 0.2)) * 2,
                               peak(S.box(S.nt(12), 1.0, 0.4)), places=6)


class TestEveryVoice(unittest.TestCase):
    """One render of each registry entry, checked for the things that make a
    file unusable: NaN, silence, a wrong length, or a peak that would slam the
    master limiter into pumping."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.rendered = {name: render_synth(name) for name in S.SYNTHS}

    def test_the_registry_is_complete(self) -> None:
        """render_audio.py looks scenes up by name; a rename is a broken build."""
        self.assertEqual(set(S.SYNTHS), {
            "wind", "heartbeat", "drone", "whispers", "thunder", "creak",
            "shriek", "toll", "organ", "descent", "waltz", "musicbox"})

    def test_output_is_finite(self) -> None:
        """The single assertion most worth having: one NaN and the whole mixed
        scene is NaN, and the wav writer produces noise or nothing."""
        for name, (buf, _m) in self.rendered.items():
            with self.subTest(name):
                self.assertTrue(np.all(np.isfinite(buf)), f"{name} produced NaN/inf")

    def test_output_is_not_silent(self) -> None:
        for name, (buf, _m) in self.rendered.items():
            with self.subTest(name):
                self.assertGreater(peak(buf), 0.01, f"{name} rendered near-silence")

    def test_peaks_are_in_a_sane_range(self) -> None:
        """These are limited downstream, so a little over 1.0 is fine — but a
        voice handing back 100.0 has a runaway envelope somewhere."""
        for name, (buf, _m) in self.rendered.items():
            with self.subTest(name):
                self.assertLess(peak(buf), 2.0, f"{name} peaks at {peak(buf):.2f}")

    def test_lengths_are_sane(self) -> None:
        for name, (buf, _m) in self.rendered.items():
            with self.subTest(name):
                self.assertGreater(len(buf), S.SR // 2)
                self.assertLess(len(buf), 40 * S.SR)

    def test_duration_argument_is_honoured(self) -> None:
        """wind/heartbeat/drone/whispers take the scene's `dur`."""
        for name in ("wind", "heartbeat", "drone", "whispers"):
            for dur in (2.0, 5.0):
                with self.subTest(name=name, dur=dur):
                    buf, _m = render_synth(name, dur=dur)
                    self.assertEqual(len(buf), int(dur * S.SR))

    def test_fixed_length_voices_ignore_dur(self) -> None:
        """The rest are one-shots of a written length, and render_audio passes
        `dur` whenever the scene has one — so they must swallow it."""
        for name in ("thunder", "creak", "shriek", "toll", "organ",
                     "descent", "waltz", "musicbox"):
            with self.subTest(name):
                self.assertEqual(len(render_synth(name, dur=2.0)[0]),
                                 len(render_synth(name, dur=9.0)[0]))

    def test_no_dc_offset(self) -> None:
        """A voice sitting off zero eats headroom and thumps at the splice."""
        for name, (buf, _m) in self.rendered.items():
            with self.subTest(name):
                self.assertLess(abs(float(np.mean(buf))), 0.05 * peak(buf) + 1e-4)

    def test_tonal_voices_have_no_sample_level_discontinuity(self) -> None:
        """A gain sign-flip shows up as a jump of roughly twice the amplitude
        inside one sample. Oscillator voices slew far slower than that."""
        for name in TONAL_SYNTHS:
            buf = self.rendered[name][0]
            jump = float(np.max(np.abs(np.diff(buf)))) / peak(buf)
            with self.subTest(name):
                self.assertLess(jump, 0.35, f"{name} jumps {jump:.2f}x peak in one sample")

    def test_voices_start_near_zero(self) -> None:
        """Files butt against silence, or against their own loop point."""
        for name, (buf, _m) in self.rendered.items():
            with self.subTest(name):
                self.assertLess(abs(buf[0]), 0.05 * peak(buf) + 1e-6)

    def test_atmosphere_voices_fade_in_and_out(self) -> None:
        """wind and drone are the long beds; an abrupt end is a click at the
        scene boundary, and they are the ones that get looped."""
        for name in ("wind", "drone"):
            env = window_peaks(self.rendered[name][0], 50.0)
            with self.subTest(name):
                self.assertLess(env[0], 0.3 * env.max())
                self.assertLess(env[-1], 0.3 * env.max())


class TestDeterminism(unittest.TestCase):
    """The render is meant to be reproducible: a diff in the output files
    should mean a real change, not a reseeded RNG. Half of these voices draw
    noise, so this is not free."""

    def test_same_seed_gives_identical_audio(self) -> None:
        for name in S.SYNTHS:
            with self.subTest(name):
                a, _ = render_synth(name, dur=4.0, seed=99)
                b, _ = render_synth(name, dur=4.0, seed=99)
                self.assertTrue(np.array_equal(a, b), f"{name} is not reproducible")

    def test_same_seed_gives_identical_markers(self) -> None:
        """Markers ride in the manifest; churn there rebuilds the firmware."""
        for name in ("heartbeat", "whispers", "toll", "organ", "waltz"):
            with self.subTest(name):
                self.assertEqual(render_synth(name, dur=4.0, seed=99)[1],
                                 render_synth(name, dur=4.0, seed=99)[1])

    def test_different_seeds_change_the_noise_voices(self) -> None:
        """The counterpart: if these matched, the seed would be doing nothing
        and every render of a scene would repeat the same gust of wind."""
        for name in ("wind", "whispers", "thunder", "creak", "shriek", "heartbeat"):
            with self.subTest(name):
                a, _ = render_synth(name, dur=4.0, seed=1)
                b, _ = render_synth(name, dur=4.0, seed=2)
                self.assertFalse(np.array_equal(a, b), f"{name} ignores its seed")

    def test_written_pieces_do_not_depend_on_the_seed(self) -> None:
        for name in ("drone", "toll", "organ", "descent", "waltz", "musicbox"):
            with self.subTest(name):
                a, _ = render_synth(name, dur=4.0, seed=1)
                b, _ = render_synth(name, dur=4.0, seed=2)
                self.assertTrue(np.array_equal(a, b))

    def test_whispers_markers_do_not_perturb_the_audio(self) -> None:
        """The word velocity is derived from the word length rather than a
        fresh rng draw, precisely so that reporting markers cannot shift the
        stream of random numbers underneath the audio."""
        audio, words = render_synth("whispers", dur=SYNTH_DUR, seed=5)
        again, again_words = render_synth("whispers", dur=SYNTH_DUR, seed=5)
        self.assertTrue(np.array_equal(audio, again))
        self.assertEqual(words, again_words)


if __name__ == "__main__":
    unittest.main(verbosity=2)
