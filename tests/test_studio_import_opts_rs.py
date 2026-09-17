"""The panel's import options, proved by what each one produces.

The continuation of `tests/test_studio_import_rs.py`, which is at the
repo's 500-line cap. The seam is the one the suites already had: that file
is the import ROUTES — what they refuse, what an upload lands, what a
refresh rebuilds, what a job reports — and this one is the OPTION boxes
that ride along on them, each asserted through the file that came out.

These are what the `TestImportOptions` cases of
`tests/test_studio_tracks_api.py` and the argv assertions of
`tests/test_studio_api.py` became: no mocked subprocess to read a command
line out of, so the container, the sample rate, the length and the samples
themselves are the evidence.
"""

from __future__ import annotations

import array
import math
import sys
import unittest
import wave
from pathlib import Path
from typing import Any, ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import make_click_track
from studio_rs_case import CARGO, IN_CI
from test_studio_import_rs import NO_FFMPEG, ImportCase


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
@unittest.skipIf(NO_FFMPEG, "no ffmpeg")
class ImportOptions(ImportCase):
    """The panel's option boxes, proved by what they produce.

    The Python cases these replace asserted the ARGV the server built, by
    mocking the subprocess. Black-box, the question is what the importer
    DID with each one. Two options have no observable effect over HTTP and
    are covered as "recorded and reused" instead: `sensitivity` moves the
    onset counts, but on a click track built for detectability it moves
    them by nothing at either end of its range; and `gain_db`/`normalize`
    are both undone by the encoder's own limiter before the file is
    written. What the panel reads back — the `opts` in tracks.json — is
    asserted for those, because that is what the next refresh reuses.
    """

    clip: ClassVar[Path]

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.clip = cls.tmp / "clip.wav"
        make_click_track(cls.clip, seconds=2.0)

    def imported(self, tid: str, opts: dict[str, object]) -> dict[str, Any]:
        code, body = self.upload(
            "clip.wav", self.clip.read_bytes(), {**opts, "id": tid}
        )
        self.assertEqual(code, 200, body)
        return self.row(body, tid)

    def pcm(self, tid: str) -> tuple[int, int, array.array[int]]:
        """The written WAV as interleaved 16-bit samples."""
        with wave.open(str(self.tracks / f"{tid}.wav")) as w:
            frames = w.readframes(w.getnframes())
            rate, channels = w.getframerate(), w.getnchannels()
        pcm: array.array[int] = array.array("h")
        pcm.frombytes(frames)
        return rate, channels, pcm

    @staticmethod
    def level(pcm: array.array[int], rate: int, ch: int, t0: float, t1: float) -> float:
        """RMS over one window of the file, in sample units."""
        cut = pcm[int(t0 * rate) * ch : int(t1 * rate) * ch]
        return math.sqrt(sum(v * v for v in cut) / len(cut)) if len(cut) else 0.0

    def test_01_format_and_sample_rate_reach_the_written_file(self) -> None:
        row = self.imported(
            "as_wav", {"format": "wav", "sample_rate": 22050, "channels": 1}
        )
        self.assertEqual(row["ext"], "wav")
        self.assertTrue((self.tracks / "as_wav.wav").exists())
        self.assertFalse((self.tracks / "as_wav.mp3").exists())
        rate, channels, pcm = self.pcm("as_wav")
        self.assertEqual((rate, channels), (22050, 1))
        self.assertAlmostEqual(len(pcm) / rate, 2.0, delta=0.1)
        entry = self.entry("as_wav")
        self.assertEqual(entry["audio"]["format"], "wav")
        self.assertEqual(entry["audio"]["sample_rate"], 22050)
        self.assertEqual(entry["audio"]["channels"], 1)
        self.assertEqual(entry["opts"]["format"], "wav")

    def test_02_start_and_take_cut_the_result(self) -> None:
        """m:ss and bare seconds both, since the box accepts either."""
        cases: list[tuple[str, dict[str, object], float]] = [
            ("cut_take", {"take": "0.5"}, 0.5),
            ("cut_start", {"start": "0:01"}, 1.0),
            ("cut_both", {"start": "0.5", "take": "0.75"}, 0.75),
        ]
        for tid, opts, want in cases:
            row = self.imported(tid, opts)
            self.assertAlmostEqual(row["dur"], want, delta=0.1, msg=tid)
            self.assertEqual(self.entry(tid)["audio"]["duration"], row["dur"])
            for key, value in opts.items():
                self.assertEqual(self.entry(tid)["opts"][key], value, (tid, key))

    def test_03_fades_shape_the_head_and_the_tail(self) -> None:
        """A fade is only visible in the samples, so both cuts are written
        as WAV and read back. `--fade-out` is measured against the LENGTH,
        so the importer only applies it when a take is set
        (tools/import_convert.py convert) — the pair travels together."""
        flat: dict[str, object] = {"format": "wav", "take": "1.5"}
        self.imported("no_fade", flat)
        self.imported("fades", {**flat, "fade_in": 0.5, "fade_out": 1.0})
        rate, ch, plain = self.pcm("no_fade")
        _r, _c, faded = self.pcm("fades")
        self.assertEqual(len(plain), len(faded), "a fade must not change the length")
        head_plain = self.level(plain, rate, ch, 0.0, 0.1)
        head_faded = self.level(faded, rate, ch, 0.0, 0.1)
        self.assertGreater(head_plain, 0.0)
        self.assertLess(head_faded, head_plain * 0.25, (head_plain, head_faded))
        tail_plain = self.level(plain, rate, ch, 1.0, 1.5)
        tail_faded = self.level(faded, rate, ch, 1.0, 1.5)
        self.assertGreater(tail_plain, 0.0)
        self.assertLess(tail_faded, tail_plain * 0.6, (tail_plain, tail_faded))
        entry = self.entry("fades")
        self.assertEqual(entry["opts"]["fade_in"], 0.5)
        self.assertEqual(entry["opts"]["fade_out"], 1.0)

    def test_04_normalize_is_recorded_and_then_remembered(self) -> None:
        """All three import paths only ever added --normalize; an unchecked
        box was silently ignored and the row then claimed "normalised". The
        loudness match itself is not observable over HTTP — the encoder's
        limiter caps both versions at the same true peak — so what is
        pinned is the choice the panel reads back and the next refresh
        reuses."""
        self.assertFalse(
            self.imported("quiet", {"normalize": False})["opts"]["normalize"]
        )
        self.assertTrue(self.imported("loud", {"normalize": True})["opts"]["normalize"])
        # An ABSENT normalize leaves the remembered choice alone: a refresh
        # that only changes the length must not silently re-normalise.
        code, body = self.json("/studio/refresh", "POST", {"id": "quiet", "take": "1"})
        self.assertEqual(code, 200, body)
        row = self.row(body, "quiet")
        self.assertFalse(row["opts"]["normalize"], "the unchecked box was forgotten")
        self.assertAlmostEqual(row["dur"], 1.0, delta=0.1)
        # ...and asking for it back turns it on.
        code, body = self.json(
            "/studio/refresh", "POST", {"id": "quiet", "normalize": True}
        )
        self.assertEqual(code, 200, body)
        self.assertTrue(self.row(body, "quiet")["opts"]["normalize"])
        self.assertTrue(self.entry("quiet")["opts"]["normalize"])


if __name__ == "__main__":
    unittest.main()
