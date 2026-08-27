"""Tests for the stems module — the parts that need no Demucs.

The separation itself is a 25-second GPU job and is not worth a unit test;
what IS worth testing is everything around it: the traversal guards, the
freshness check that stops stale stems validating audio that no longer
exists, and — the point of the whole feature — that per-channel analysis
actually hears a hard-panned sound in its channel and nothing in the other.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np
import stems


def write_left_clicks(path: Path, seconds: float = 6.0, sr: int = 44100) -> int:
    """A stereo WAV whose LEFT channel clicks twice a second and whose right
    channel is silence — the exact material a mono downmix half-swallows."""
    n = int(seconds * sr)
    left = np.zeros(n)
    clicks = 0
    t = 0.5
    while t < seconds - 0.5:
        a = int(t * sr)
        left[a : a + int(0.005 * sr)] = 0.9
        clicks += 1
        t += 0.5
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        frames = bytearray()
        for v in left:
            frames += struct.pack("<hh", int(v * 32000), 0)
        w.writeframes(bytes(frames))
    return clicks


class StemsCase(unittest.TestCase):
    sandbox: Path
    _old: tuple[Path, Path]

    @classmethod
    def setUpClass(cls) -> None:
        cls.sandbox = Path(tempfile.mkdtemp(prefix="castle-stems-test-"))
        cls._old = (stems.TRACKS, stems.STEMS)
        stems.TRACKS = cls.sandbox
        stems.STEMS = cls.sandbox / "stems"

    @classmethod
    def tearDownClass(cls) -> None:
        stems.TRACKS, stems.STEMS = cls._old
        shutil.rmtree(cls.sandbox, ignore_errors=True)


class TestGuards(StemsCase):
    def test_track_file_strips_traversal(self) -> None:
        (self.sandbox / "passwd.mp3").write_bytes(b"x")
        p = stems.track_file("../../passwd")
        self.assertEqual(
            p,
            self.sandbox / "passwd.mp3",
            "the directory part must be stripped, not resolved",
        )

    def test_stem_file_serves_only_the_two_stem_layers(self) -> None:
        d = self.sandbox / "stems" / "song"
        d.mkdir(parents=True, exist_ok=True)
        (d / "vocals.mp3").write_bytes(b"x")
        (d / "analysis.json").write_text("{}")
        self.assertIsNotNone(stems.stem_file("song", "vocals"))
        # `combined` streams via /api/track; anything else is not a layer.
        self.assertIsNone(stems.stem_file("song", "combined"))
        self.assertIsNone(stems.stem_file("song", "analysis.json"))
        # Name-stripped, same as /api/track: the directory part is discarded,
        # so a traversal can only ever land back inside the stems dir.
        self.assertEqual(
            stems.stem_file("../../../song", "vocals"),
            stems.stem_file("song", "vocals"),
        )

    def test_analysis_names_each_missing_state(self) -> None:
        self.assertEqual(stems.analysis("nope")["error"], "no such track")
        (self.sandbox / "raw.mp3").write_bytes(b"x")
        self.assertEqual(stems.analysis("raw")["error"], "not split yet")


class TestFreshness(StemsCase):
    def test_stems_go_stale_when_the_track_is_reimported(self) -> None:
        src = self.sandbox / "tune.mp3"
        src.write_bytes(b"abc")
        d = self.sandbox / "stems" / "tune"
        d.mkdir(parents=True, exist_ok=True)
        st = src.stat()
        (d / "analysis.json").write_text(
            json.dumps({"src_bytes": st.st_size, "src_mtime": int(st.st_mtime)})
        )
        self.assertTrue(stems.fresh("tune"))
        # A re-import rewrites the file; the old split must stop counting.
        src.write_bytes(b"different content")
        self.assertFalse(stems.fresh("tune"))


class TestChannelAnalysis(StemsCase):
    def test_a_hard_left_sound_is_heard_left_and_not_right(self) -> None:
        wav = self.sandbox / "leftclicks.wav"
        clicks = write_left_clicks(wav)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            data = stems.analyse_layers({"combined": wav})
        self.assertIn("combined  left", out.getvalue())
        chans = data["layers"]["combined"]

        left_hits = sum(len(v) for v in chans["left"]["onsets"].values())
        right_hits = sum(len(v) for v in chans["right"]["onsets"].values())
        self.assertGreaterEqual(
            left_hits,
            clicks,
            "the left channel must hear its own clicks "
            "(broadband, so several bands fire)",
        )
        self.assertEqual(right_hits, 0, "silence has no onsets")
        # The raw levels are the tell the normalised strips would hide.
        self.assertGreater(chans["left"]["level"], 0.5)
        self.assertEqual(chans["right"]["level"], 0.0)
        self.assertEqual(len(chans["left"]["peaks"]), stems.PEAKS)
        self.assertAlmostEqual(data["duration"], 6.0, places=1)


if __name__ == "__main__":
    unittest.main()
