"""Colour from harmony (option 12) on hand-built chroma. No library track,
audio or device — the one decode is mocked."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import harmony
import numpy as np
from harmony import Chord, Chroma

C, D, E, F, G, A, B = 0, 2, 4, 5, 7, 9, 11
HOP_MS = 100.0


def triad(root, minor=False):
    row = [0.0] * 12
    for step in (0, 3 if minor else 4, 7):
        row[(root + step) % 12] = 1 / 3
    return tuple(row)


def note(pc):
    return tuple(1.0 if i == pc else 0.0 for i in range(12))


def played(chords, frames=10, loud=1.0):
    """A Chroma of `chords` ((root, minor) or None for silence), `frames` a
    chord, the bass on each root."""
    bass, other, level = [], [], []
    for ch in chords:
        for _ in range(frames):
            if ch is None:
                bass.append(tuple([1 / 12] * 12))
                other.append(tuple([1 / 12] * 12))
                level.append(0.0)
            else:
                bass.append(note(ch[0]))
                other.append(triad(*ch))
                level.append(loud)
    return Chroma(HOP_MS, tuple(bass), tuple(other), tuple(level))


def bars(n, frames=10):
    ms = int(frames * HOP_MS)
    return [(i * ms, (i + 1) * ms) for i in range(n)]


class Harmony(unittest.TestCase):
    def test_a_sine_lands_in_its_pitch_class(self):
        t = np.arange(harmony.WINDOW * 3) / harmony.RATE
        rows = harmony.chroma(np.sin(2 * np.pi * 440.0 * t), harmony.OTHER_HZ)
        self.assertEqual(int(rows.sum(axis=0).argmax()), A)
        self.assertEqual(harmony.chroma(np.zeros(10), harmony.OTHER_HZ).shape, (0, 12))

    def test_a_chord_is_its_triad_and_the_bass_names_the_root(self):
        c = played([(G, False), (E, True), None])
        self.assertEqual(harmony.chord(c, 0, 1000), Chord(G, False))
        self.assertEqual(harmony.chord(c, 1000, 2000), Chord(E, True))
        self.assertIsNone(harmony.chord(c, 2000, 3000))  # quiet
        self.assertIsNone(harmony.chord(c, 9000, 9500))  # past the end

    def test_a_chords_mood_is_its_place_in_the_key(self):
        g, am = Chord(G, False), Chord(A, True)
        self.assertEqual(harmony.mood(Chord(G, False), g), "home")
        self.assertEqual(harmony.mood(Chord(C, False), g), "away")
        self.assertEqual(harmony.mood(Chord(D, False), g), "away")
        self.assertEqual(harmony.mood(Chord(E, True), g), "shadow")
        self.assertEqual(harmony.mood(Chord(10, False), g), "strange")  # Bb
        self.assertEqual(harmony.mood(Chord(C, True), g), "strange")  # iv minor
        self.assertEqual(harmony.mood(Chord(A, False), am), "home")  # a Picardy
        self.assertEqual(harmony.mood(Chord(E, False), am), "away")  # harmonic V
        self.assertEqual(harmony.mood(Chord(D, True), am), "shadow")

    def test_the_key_from_the_profile_then_its_resolutions(self):
        scale = [(G, False), (C, False), (D, False), (E, True)]
        c = played(scale * 4)
        found = harmony.key(c)
        self.assertIn(found, (Chord(G, False), Chord(E, True)))
        to_g = [Chord(D, False), Chord(G, False)] * 3
        to_em = [Chord(B, False), Chord(E, True)] * 3
        self.assertEqual(harmony.key(c, to_g), Chord(G, False))
        self.assertEqual(harmony.key(c, to_em), Chord(E, True))

    def test_a_tie_goes_to_the_chord_that_opens_the_phrases(self):
        c = played([(G, False), (C, False), (D, False), (E, True)] * 4)
        em_first = [Chord(E, True), Chord(C, False), Chord(A, True), Chord(C, False)]
        g_first = [Chord(G, False), Chord(C, False), Chord(A, True), Chord(C, False)]
        self.assertEqual(harmony.key(c, em_first * 3), Chord(E, True))
        self.assertEqual(harmony.key(c, g_first * 3), Chord(G, False))

    def test_a_lone_passing_bar_takes_its_neighbours_mood(self):
        # Db, a tritone from G, is outside G major and G minor alike
        chords = [(G, False), (D, False), (G, False), (G, False), (1, False),
                  (1, False), None, (D, False), (G, False)]  # fmt: skip
        moods = harmony.bar_moods(played(chords), bars(len(chords)))
        self.assertEqual(moods[:4], ["home"] * 4)  # D between two Gs
        self.assertEqual(moods[4:6], ["strange", "strange"])
        self.assertIsNone(moods[6])
        self.assertEqual(moods[7:], ["away", "home"])

    def test_the_stems_are_measured_once_and_then_read_from_the_cache(self):
        t = np.arange(harmony.WINDOW * 4) / harmony.RATE
        tone = np.sin(2 * np.pi * 196.0 * t)  # G3
        with tempfile.TemporaryDirectory() as tmp:
            stems, cache = Path(tmp), Path(tmp) / "k.harmony.json"
            self.assertIsNone(harmony.track(stems, cache))  # no stems
            (stems / "bass.mp3").write_bytes(b"")
            (stems / "other.mp3").write_bytes(b"")
            with mock.patch.object(harmony, "decode", return_value=tone) as decode:
                first = harmony.track(stems, cache)
                again = harmony.track(stems, cache)
            self.assertEqual(decode.call_count, 2)  # bass and other, once
            self.assertEqual(first, again)
            assert first is not None
            self.assertEqual(max(first.loud), 1.0)
            self.assertEqual(int(np.array(first.other).sum(axis=0).argmax()), G)


if __name__ == "__main__":
    unittest.main()
