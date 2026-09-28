"""Kinds of voice (option 12) on hand-built pitch tracks and synthetic
tones. No library track, audio or device — the one decode is mocked."""

import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import voice_kinds
from voice_kinds import Kind, VoiceTrack
from voice_pitch import RATE, Pitch

GAP = [None] * 5


def pitch(*runs):
    """A pitch track (10 ms frames) of `runs`, each a list of notes, with a
    short silence after each."""
    notes = []
    for run in runs:
        notes += [*run, *GAP]
    return Pitch(10, tuple(notes))


def glide(start, frames=40, step=0.05):
    return [start + step * i for i in range(frames)]


class Spoken(unittest.TestCase):
    def test_a_voice_that_glides_is_speech_and_one_on_notes_is_song(self):
        talk = pitch(glide(60), glide(62), glide(59))
        sung = pitch([60.0] * 40, [62.0] * 40, [64.0] * 40)
        self.assertTrue(voice_kinds.spoken(talk, 0, 1350, 0.0))
        self.assertFalse(voice_kinds.spoken(sung, 0, 1350, 0.0))

    def test_one_run_or_a_breath_of_voice_is_not_enough_to_judge(self):
        self.assertFalse(voice_kinds.spoken(pitch(glide(60)), 0, 450, 0.0))
        short = pitch(glide(60, 10), glide(62, 10))
        self.assertFalse(voice_kinds.spoken(short, 0, 300, 0.0))

    def test_a_half_held_line_is_speech_only_when_it_misses_the_grid(self):
        def line(base):
            run = [base] * 28 + [base + 0.6 + 0.1 * i for i in range(12)]
            return pitch(run, run, run)

        self.assertTrue(voice_kinds.spoken(line(60.45), 0, 1350, 0.0))
        self.assertFalse(voice_kinds.spoken(line(60.0), 0, 1350, 0.0))
        self.assertFalse(voice_kinds.spoken(line(60.45), 0, 1350, 0.45))

    def test_the_songs_tuning_is_its_offset_from_the_grid(self):
        self.assertAlmostEqual(voice_kinds.tuning(pitch([60.3] * 20, [67.3] * 20)), 0.3)
        self.assertEqual(voice_kinds.tuning(Pitch(10, (None, None))), 0.0)


class Vibrato(unittest.TestCase):
    def wobble(self, hz, depth, frames=100):
        return pitch([67 + depth / 2 * math.sin(2 * math.pi * hz * i / 100)
                      for i in range(frames)])  # fmt: skip

    def test_a_held_note_that_swings_six_times_a_second_has_vibrato(self):
        self.assertTrue(voice_kinds.vibrato(self.wobble(6, 0.6), 0, 1000))

    def test_steady_shallow_slow_or_broken_notes_do_not(self):
        self.assertFalse(voice_kinds.vibrato(pitch([67.0] * 100), 0, 1000))
        self.assertFalse(voice_kinds.vibrato(self.wobble(6, 0.1), 0, 1000))
        self.assertFalse(voice_kinds.vibrato(self.wobble(2, 0.6), 0, 1000))
        self.assertFalse(voice_kinds.vibrato(self.wobble(6, 0.6), 0, 200))  # short
        holes = Pitch(10, tuple(None if i % 2 else 67.0 for i in range(100)))
        self.assertFalse(voice_kinds.vibrato(holes, 0, 1000))


def track(frames, rough=0.05, bright=1000, width=0.1, choir=(), bright_at=()):
    """A VoiceTrack whose rough/width jump inside `choir` and whose
    brightness doubles inside `bright_at` (frame ranges)."""
    r = [0.4 if any(a <= i < b for a, b in choir) else rough for i in range(frames)]
    w = [0.3 if any(a <= i < b for a, b in choir) else width for i in range(frames)]
    c = [2 * bright if any(a <= i < b for a, b in bright_at) else bright
         for i in range(frames)]  # fmt: skip
    return VoiceTrack(tuple(r), tuple(c), tuple(w))


class Kinds(unittest.TestCase):
    NOTES = ((1000, 0.8, 300), (3000, 0.8, 300), (6000, 0.8, 300))

    def test_without_a_stem_every_note_is_a_plain_one(self):
        self.assertEqual(voice_kinds.kinds(self.NOTES, None, None), [Kind()] * 3)

    def test_a_rough_wide_stretch_is_a_choir(self):
        voice = track(800, choir=[(250, 350)])
        ks = voice_kinds.kinds(self.NOTES, None, voice)
        self.assertEqual([k.choir for k in ks], [False, True, False])

    def test_a_voice_rough_all_song_is_a_gravelly_lead(self):
        ks = voice_kinds.kinds(self.NOTES, None, track(800, rough=0.4))
        self.assertFalse(any(k.choir for k in ks))

    def test_a_brighter_start_than_usual_is_a_bright_tone(self):
        ks = voice_kinds.kinds(self.NOTES, None, track(800, bright_at=[(600, 620)]))
        self.assertEqual([k.tone for k in ks], [0.0, 0.0, 1.0])

    def test_a_spoken_line_is_never_a_choir_and_a_held_one_may_shimmer(self):
        notes = [(0, 0.8, 300), (450, 0.8, 300), (900, 0.8, 300), (3000, 0.8, 1000)]
        talk = [*glide(60), *GAP, *glide(62), *GAP, *glide(59), *GAP]
        frames = [*talk, *[None] * (300 - len(talk))]
        frames += [67 + 0.3 * math.sin(2 * math.pi * 6 * i / 100) for i in range(100)]
        ks = voice_kinds.kinds(notes, Pitch(10, tuple(frames)),
                               track(400, choir=[(0, 150)]))  # fmt: skip
        self.assertEqual([k.spoken for k in ks], [True, True, True, False])
        self.assertFalse(any(k.choir for k in ks[:3]))
        self.assertEqual([k.vibrato for k in ks], [False, False, False, True])


class Measure(unittest.TestCase):
    def stereo(self, left, right):
        return np.stack([left, right], axis=1)

    def test_a_tone_is_smooth_and_narrow_and_noise_is_rough_and_wide(self):
        t = np.arange(RATE) / RATE
        tone = 0.5 * np.sin(2 * np.pi * 220 * t)
        noise = np.random.default_rng(1).normal(0, 0.3, RATE)
        smooth = voice_kinds.measure(self.stereo(tone, tone))
        rough = voice_kinds.measure(self.stereo(noise, noise[::-1]))
        mid = len(smooth.rough) // 2
        smooth_r, rough_r, bright = (
            smooth.rough[mid],
            rough.rough[mid],
            smooth.bright[mid],
        )
        assert smooth_r is not None and rough_r is not None and bright is not None
        self.assertLess(smooth_r, 0.1)
        self.assertGreater(rough_r, 0.3)
        self.assertLess(smooth.width[mid], 0.01)
        self.assertGreater(rough.width[mid], 0.5)
        self.assertAlmostEqual(bright, 220, delta=40)

    def test_the_stem_is_measured_once_and_then_read_from_the_cache(self):
        t = np.arange(RATE // 2) / RATE
        tone = np.sin(2 * np.pi * 220 * t)
        with tempfile.TemporaryDirectory() as tmp:
            vocals, cache = Path(tmp) / "vocals.mp3", Path(tmp) / "k.voice.json"
            vocals.write_bytes(b"")
            with mock.patch.object(
                voice_kinds, "decode_stereo", return_value=self.stereo(tone, tone)
            ) as decode:
                first = voice_kinds.track(vocals, cache)
                again = voice_kinds.track(vocals, cache)
            self.assertEqual(decode.call_count, 1)
            self.assertEqual(first, again)


if __name__ == "__main__":
    unittest.main()
