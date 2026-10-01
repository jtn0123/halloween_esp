"""The singer's pitch tracker on synthetic tones: no stem, audio or ffmpeg."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import voice_pitch
from voice_pitch import RATE, Pitch


def tone(hz, seconds=0.5, level=0.5):
    t = np.arange(int(RATE * seconds)) / RATE
    return level * np.sin(2 * np.pi * hz * t)


class Yin(unittest.TestCase):
    def median(self, samples):
        found = [f for f in voice_pitch.yin(samples) if f is not None]
        self.assertTrue(found)
        return float(np.median(found))

    def test_a_tone_reads_as_its_own_frequency(self):
        for hz in (110.0, 220.0, 440.0, 880.0):
            self.assertAlmostEqual(self.median(tone(hz)), hz, delta=hz * 0.005)

    def test_silence_and_noise_are_unvoiced(self):
        quiet = np.concatenate([tone(220.0), np.zeros(RATE // 2)])
        frames = voice_pitch.yin(quiet)
        self.assertTrue(all(f is None for f in frames[-10:]))
        noise = np.random.default_rng(1).standard_normal(RATE // 2)
        voiced = [f for f in voice_pitch.yin(noise) if f is not None]
        self.assertLess(len(voiced), len(voice_pitch.yin(noise)) // 10)

    def test_too_short_to_measure_is_nothing(self):
        self.assertEqual(voice_pitch.yin(np.zeros(100)), [])

    def test_a_long_stem_is_read_in_chunks_the_same(self):
        samples = np.concatenate([tone(196.0), tone(330.0)])
        self.assertEqual(voice_pitch.yin(samples, chunk=7), voice_pitch.yin(samples))

    def test_midi(self):
        self.assertEqual(voice_pitch.midi(440.0), 69.0)
        self.assertEqual(voice_pitch.midi(110.0), 45.0)
        self.assertIsNone(voice_pitch.midi(None))


class Track(unittest.TestCase):
    def test_a_span_is_its_median_note_when_mostly_sung(self):
        pitch = Pitch(10, (None, 60.0, 62.0, 61.0, *([None] * 8)))
        self.assertEqual(pitch.at(0, 50), 61.0)
        self.assertEqual(pitch.at(0, 90), 61.0)  # sung for a third: enough
        self.assertIsNone(pitch.at(0, 120))  # a quarter: not
        self.assertIsNone(pitch.at(60, 90))
        self.assertIsNone(pitch.at(500, 600))  # past the end

    def test_the_stem_is_decoded_once_then_read_from_the_cache(self):
        samples = tone(440.0).astype(np.float32).tobytes()
        with tempfile.TemporaryDirectory() as tmp:
            vocals, cache = Path(tmp, "vocals.mp3"), Path(tmp, "song.pitch.json")
            vocals.write_bytes(b"not decoded here")
            ran = mock.Mock(return_value=mock.Mock(stdout=samples))
            with mock.patch("voice_pitch.subprocess.run", ran):
                first = voice_pitch.track(vocals, cache)
                second = voice_pitch.track(vocals, cache)
            self.assertEqual(ran.call_count, 1)
            self.assertEqual(ran.call_args.args[0][0], "ffmpeg")
            self.assertEqual(first, second)
            self.assertAlmostEqual(first.at(0, 500) or 0, 69.0, delta=0.1)
            self.assertEqual(
                json.loads(cache.read_text(encoding="utf-8"))["hop_ms"], 10
            )
            os.utime(cache, (1, 1))  # a stem newer than its cache: read again
            with mock.patch("voice_pitch.subprocess.run", ran):
                voice_pitch.track(vocals, cache)
            self.assertEqual(ran.call_count, 2)


if __name__ == "__main__":
    unittest.main()
