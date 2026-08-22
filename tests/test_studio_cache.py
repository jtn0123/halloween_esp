"""The studio's two decode caches: /api/tracks via the manifest, waveforms in RAM.

Both exist for the same reason — the panel asks the same questions about the
same files over and over, and each answer used to cost a full decode + STFT.
What is asserted here is the contract, not the speed: a second ask does no
audio work, the JSON is shaped exactly as before, and a changed file is never
answered from a stale copy.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import manifest as mf
import studio_media as sm
import studio_tracks as stt
from helpers import make_click_track
from studio_case import ServerCase, make_mp3


class TestTrackInfoFromManifest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="castle-cache-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        p = mock.patch.object(mf, "PATH", self.tmp / "tracks.json")
        p.start()
        self.addCleanup(p.stop)
        self.track = self.tmp / "_t_cache_a.wav"
        make_click_track(self.track, seconds=2.0)

    def decodes(self, fn):
        """How many times fn() reached for the audio."""
        real = stt.ana.load_audio
        with mock.patch.object(stt.ana, "load_audio", side_effect=real) as m:
            out = fn()
        return out, m.call_count

    def test_second_ask_reads_the_manifest_and_keeps_the_shape(self) -> None:
        first, n1 = self.decodes(lambda: stt.track_info(self.track))
        self.assertEqual(n1, 1)
        second, n2 = self.decodes(lambda: stt.track_info(self.track))
        self.assertEqual(n2, 0)
        self.assertEqual(first, second)
        self.assertEqual(set(first), {"id", "ext", "kb", "bytes", "source",
                                      "source_missing", "title", "imported",
                                      "opts", "notes", "dur", "onsets"})
        self.assertAlmostEqual(first["dur"], 2.0, delta=0.3)
        self.assertIn("onset_low", first["onsets"])
        self.assertTrue(all(isinstance(v, int) for v in first["onsets"].values()))

    def test_track_infos_reads_the_manifest_once_for_the_listing(self) -> None:
        """/api/tracks used to load tracks.json once PER TRACK."""
        b = self.tmp / "_t_cache_b.wav"
        make_click_track(b, seconds=2.0)
        stt.track_info(self.track)
        stt.track_info(b)                     # both cached in the manifest now
        with mock.patch.object(stt.mf, "load", wraps=stt.mf.load) as ld:
            rows, n = self.decodes(lambda: stt.track_infos([self.track, b]))
        self.assertEqual(ld.call_count, 1)
        self.assertEqual(n, 0)
        self.assertEqual([r["id"] for r in rows], ["_t_cache_a", "_t_cache_b"])
        self.assertEqual(rows, [stt.track_info(self.track), stt.track_info(b)])

    def test_track_infos_fills_an_unknown_track_like_track_info(self) -> None:
        rows, n = self.decodes(lambda: stt.track_infos([self.track]))
        self.assertEqual(n, 1)
        self.assertIn("dur", rows[0])
        self.assertEqual(rows[0], stt.track_info(self.track))

    def test_write_back_does_not_clobber_provenance(self) -> None:
        mf.record("_t_cache_a", source="file:/x.wav", title="X",
                  opts={"take": 2}, notes="keep me")
        stt.track_info(self.track)
        entry = mf.get("_t_cache_a") or {}
        self.assertEqual(entry["source"], "file:/x.wav")
        self.assertEqual(entry["notes"], "keep me")
        self.assertEqual(entry["audio"]["bytes"], self.track.stat().st_size)
        self.assertIn("onset_low", entry["onsets"])

    def test_a_replaced_file_is_decoded_afresh(self) -> None:
        stt.track_info(self.track)
        make_click_track(self.track, seconds=4.0)        # same id, new bytes
        info, n = self.decodes(lambda: stt.track_info(self.track))
        self.assertEqual(n, 1)
        self.assertAlmostEqual(info["dur"], 4.0, delta=0.3)

    def test_import_level_entries_are_not_reported_as_onsets(self) -> None:
        mf.record("_t_cache_a", source="file:/x.wav",
                  audio={"duration": 2.0, "bytes": self.track.stat().st_size},
                  onsets={"onset_low": 4, "level_high": 900})
        info, n = self.decodes(lambda: stt.track_info(self.track))
        self.assertEqual(n, 0)
        self.assertEqual(info["onsets"], {"onset_low": 4})
        self.assertEqual(info["dur"], 2.0)


class TestTracksEndpointDecodesOnce(ServerCase):
    def test_second_api_tracks_call_does_no_audio_decode(self) -> None:
        code, d = self.get_json("/api/tracks")          # warm
        self.assertEqual(code, 200)
        real = stt.ana.load_audio
        with mock.patch.object(stt.ana, "load_audio", side_effect=real) as m:
            code, again = self.get_json("/api/tracks")
        self.assertEqual(code, 200)
        self.assertEqual(m.call_count, 0)
        self.assertEqual(d["tracks"], again["tracks"])
        mine = next(t for t in again["tracks"] if t["id"] == self.WAVE_ID)
        self.assertAlmostEqual(mine["dur"], 3.0, delta=0.3)
        self.assertIn("onset_low", mine["onsets"])


class TestWaveformCache(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="castle-wave-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.track = self.tmp / "_t_wave.mp3"
        make_mp3(self.track, seconds=2.0)
        for store in (sm._WAVES, sm._DECODED):
            store.clear()
            self.addCleanup(store.clear)

    def decodes(self, fn):
        real = sm.ana.load_audio
        with mock.patch.object(sm.ana, "load_audio", side_effect=real) as m:
            out = fn()
        return out, m.call_count

    def analyses(self, fn):
        """Onset passes — the part of the answer the knob actually changes."""
        real = sm.ana.analyze_full
        with mock.patch.object(sm.ana, "analyze_full", side_effect=real) as m:
            out = fn()
        return out, m.call_count

    def test_same_file_same_knob_is_answered_from_memory(self) -> None:
        a, n1 = self.decodes(lambda: sm.waveform(self.track))
        b, n2 = self.decodes(lambda: sm.waveform(self.track))
        self.assertGreater(n1, 0)
        self.assertEqual(n2, 0)
        self.assertEqual(a, b)

    def test_sensitivity_is_part_of_the_key(self) -> None:
        """A new knob value is a new onset pass (but no new decode — the
        two-level cache, tests/test_studio_media.py)."""
        sm.waveform(self.track, sensitivity=1.1)
        _, n = self.analyses(lambda: sm.waveform(self.track, sensitivity=0.6))
        self.assertEqual(n, 1)
        _, n = self.analyses(lambda: sm.waveform(
            self.track, sensitivity={"onset_low": 1.1, "onset_mid": 1.1,
                                     "onset_high": 1.1}))
        self.assertEqual(n, 1)
        _, n = self.analyses(lambda: sm.waveform(self.track, sensitivity=0.6))
        self.assertEqual(n, 0)

    def test_a_rewritten_file_misses(self) -> None:
        sm.waveform(self.track)
        make_mp3(self.track, seconds=3.0)
        st = self.track.stat()
        # Guarantee the mtime moves even on a coarse-clock filesystem.
        os.utime(self.track, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        d, n = self.decodes(lambda: sm.waveform(self.track))
        self.assertGreater(n, 0)
        self.assertAlmostEqual(d["duration"], 3.0, delta=0.3)

    def test_the_cache_is_bounded(self) -> None:
        with mock.patch.object(sm, "KEEP_WAVES", 2):
            for i in range(4):
                sm.waveform(self.track, sensitivity=1.0 + i / 10)
        self.assertEqual(len(sm._WAVES), 2)


if __name__ == "__main__":
    unittest.main()
