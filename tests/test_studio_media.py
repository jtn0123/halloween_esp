"""The waveform cache's second level: decode once, re-detect per knob.

The audition nudges sensitivity a dozen times per track. Each nudge used to
pay for decode + peaks + envelope again (~70% of the 0.77 s) although only
the onset pass depends on the knob. What is asserted here is the contract:
a sensitivity change re-analyses but never re-decodes, a rewritten file
does, the result is byte-for-byte what the one-level cache produced, and
the decoded store is bounded.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import studio_media as sm
from helpers import make_click_track


class TestDecodedCache(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="castle-media-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.track = self.tmp / "_t_media.wav"
        make_click_track(self.track, seconds=2.0)
        for store in (sm._WAVES, sm._DECODED):
            store.clear()
            self.addCleanup(store.clear)

    def counts(self, fn):
        """(result, decodes, onset passes) for one call."""
        real_load, real_full = sm.ana.load_audio, sm.ana.analyze_full
        with (
            mock.patch.object(sm.ana, "load_audio", side_effect=real_load) as ld,
            mock.patch.object(sm.ana, "analyze_full", side_effect=real_full) as an,
        ):
            out = fn()
        return out, ld.call_count, an.call_count

    def test_a_sensitivity_change_reanalyses_but_does_not_redecode(self) -> None:
        _, decodes, passes = self.counts(lambda: sm.waveform(self.track, 1.1))
        self.assertEqual((decodes, passes), (1, 1))
        _, decodes, passes = self.counts(lambda: sm.waveform(self.track, 0.7))
        self.assertEqual((decodes, passes), (0, 1))
        per_band = {"onset_low": 0.9, "onset_mid": 1.1, "onset_high": 1.6}
        _, decodes, passes = self.counts(lambda: sm.waveform(self.track, per_band))
        self.assertEqual((decodes, passes), (0, 1))
        self.assertEqual(len(sm._DECODED), 1)
        self.assertEqual(len(sm._WAVES), 3)

    def test_the_knob_independent_parts_are_shared(self) -> None:
        a = sm.waveform(self.track, 1.1)
        b = sm.waveform(self.track, 0.6)
        self.assertIs(a["peaks"], b["peaks"])
        self.assertIs(a["env"], b["env"])
        self.assertEqual(a["duration"], b["duration"])
        self.assertNotEqual(a["onsets"], b["onsets"])

    def test_shape_matches_the_one_level_answer(self) -> None:
        d = sm.waveform(self.track)
        self.assertEqual(set(d), {"id", "duration", "peaks", "onsets", "env"})
        self.assertEqual(d["id"], "_t_media")
        self.assertAlmostEqual(d["duration"], 2.0, delta=0.05)
        self.assertEqual(len(d["peaks"]), sm.PEAKS)
        self.assertEqual(max(d["peaks"]), 1.0)
        self.assertIn("onset_low", d["onsets"])
        self.assertGreater(len(d["onsets"]["onset_low"]), 2)
        self.assertEqual(len(d["onsets"]["onset_low"][0]), 3)  # t, v, pan
        self.assertGreater(len(d["env"]), 5)

    def test_a_rewritten_file_is_decoded_afresh(self) -> None:
        sm.waveform(self.track)
        make_click_track(self.track, seconds=3.0)
        st = self.track.stat()
        os.utime(self.track, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        d, decodes, _ = self.counts(lambda: sm.waveform(self.track))
        self.assertEqual(decodes, 1)
        self.assertAlmostEqual(d["duration"], 3.0, delta=0.05)
        self.assertEqual(len(sm._DECODED), 2)

    def test_bucket_count_is_part_of_the_decoded_key(self) -> None:
        sm.waveform(self.track, buckets=200)
        d, decodes, _ = self.counts(lambda: sm.waveform(self.track, buckets=50))
        self.assertEqual(decodes, 1)
        self.assertEqual(len(d["peaks"]), 50)

    def test_the_decoded_store_is_bounded_by_total_samples(self) -> None:
        # Counted in floats, not entries: an entry IS a whole song in
        # float64 (mono + both stereo channels), so eight of them was
        # gigabytes (grade report B3).
        sm.waveform(self.track, buckets=10)
        one = sm._samples(next(iter(sm._DECODED.values())))
        with mock.patch.object(sm, "KEEP_SAMPLES", 2 * one):
            for i in range(4):
                sm.waveform(self.track, buckets=20 + i)
        self.assertEqual(len(sm._DECODED), 2)
        self.assertLessEqual(sum(sm._samples(d) for d in sm._DECODED.values()), 2 * one)

    def test_one_track_bigger_than_the_whole_budget_still_caches(self) -> None:
        with mock.patch.object(sm, "KEEP_SAMPLES", 1):
            _, decodes, _ = self.counts(lambda: sm.waveform(self.track))
            self.assertEqual(decodes, 1)
            self.assertEqual(len(sm._DECODED), 1)
            # Evicting it the instant it was built would mean decoding it
            # again for the very next sensitivity nudge.
            _, decodes, _ = self.counts(lambda: sm.waveform(self.track, 0.7))
            self.assertEqual(decodes, 0)

    def test_two_threads_wanting_one_cold_track_decode_it_once(self) -> None:
        real_load = sm.ana.load_audio

        def slow(path: Path):
            # Wide enough that the second thread is certainly asking
            # while the first is still decoding — which is the ordinary
            # case anyway: a real decode is most of a second.
            time.sleep(0.5)
            return real_load(path)

        with mock.patch.object(sm.ana, "load_audio", side_effect=slow) as ld:
            out: list[dict] = []
            threads = [
                threading.Thread(target=lambda: out.append(sm.waveform(self.track)))
                for _ in range(2)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)
        self.assertEqual(ld.call_count, 1)
        self.assertEqual(len(sm._DECODED), 1)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0], out[1])

    def test_a_poisoned_decode_does_not_wedge_the_next_caller(self) -> None:
        # The in-flight marker has to come off however the decode ends.
        # Left behind, every later request for that track waits on a
        # condition nobody will ever notify — one pinned thread each. This
        # side has always had the try/finally; the Rust twin grew a Drop
        # guard for it (grade report 2026-09-01 E1), and the invariant is
        # worth pinning on the reference implementation too.
        with mock.patch.object(
            sm.ana, "load_audio", side_effect=RuntimeError("decode blew up")
        ):
            with self.assertRaises(RuntimeError):
                sm.waveform(self.track)
        self.assertEqual(sm._DEC_INFLIGHT, set(), "the marker outlived the failure")
        self.assertEqual(len(sm._DECODED), 0)

        # And the next caller — on another thread, as the server's would
        # be — gets a real answer instead of waiting forever.
        out: list[dict] = []
        t = threading.Thread(target=lambda: out.append(sm.waveform(self.track)))
        t.start()
        t.join(timeout=30)
        self.assertFalse(t.is_alive(), "the next caller was wedged")
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[0]["peaks"]), sm.PEAKS)

    def test_an_empty_file_is_answered_without_a_stereo_decode(self) -> None:
        with (
            mock.patch.object(sm.ana, "load_audio", return_value=sm.np.zeros(0)),
            mock.patch.object(
                sm.ana, "load_stereo", side_effect=AssertionError("stereo")
            ),
        ):
            d = sm.waveform(self.track)
        self.assertEqual(
            d, {"id": "_t_media", "duration": 0.0, "peaks": [], "onsets": {}}
        )


if __name__ == "__main__":
    unittest.main()
