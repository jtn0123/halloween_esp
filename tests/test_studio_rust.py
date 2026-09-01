"""The Rust studio against the Python studio — B5 pass 1, the read side.

castle-core's `studio` bin serves the cue desk's HTTP from the crate that
already owns the show's arithmetic. The fixture (two servers over twin
sandboxes) lives in studio_rust_case.py; this file holds the read-side
assertions: the lean page, the tracks listing (live and cached), streams,
deletion, aliases, and the relay's failure shapes.

Skipped, not failed, without cargo — except in CI.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from studio_rust_case import CARGO, IN_CI, StudioPair


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class CastleLess(StudioPair):
    """CASTLE_HOST='' — the simulator-on-purpose configuration."""

    def test_01_tracks_listing_matches_live_then_cached(self) -> None:
        a, b = self.both("/studio/tracks")
        self.assertEqual(self.parsed(a), self.parsed(b))
        self.assertEqual(a[1]["content-type"], b[1]["content-type"])
        # The listing decoded two fixtures and wrote the answers back —
        # the manifests must now be byte-identical.
        self.assertEqual(
            (self.py_tracks / "tracks.json").read_text(),
            (self.rs_tracks / "tracks.json").read_text(),
        )
        # And the second listing (the decode-free path) answers the same.
        a2, b2 = self.both("/studio/tracks")
        self.assertEqual(self.parsed(a2), self.parsed(b2))
        self.assertEqual(self.parsed(a), self.parsed(a2))

    def test_02_page_serves_lean_and_validates(self) -> None:
        a, b = self.both("/")
        self.assertEqual(a[0], 200)
        self.assertEqual(a[2], b[2])
        self.assertEqual(a[1]["content-type"], b[1]["content-type"])
        self.assertEqual(a[1]["etag"], b[1]["etag"])
        self.assertEqual(
            a[1].get("content-security-policy"), b[1].get("content-security-policy")
        )
        self.assertIn(b'"vigil": "/studio/scene-audio/vigil"', a[2])
        self.assertIn(b'"storm": "/studio/scene-audio/storm"', a[2])
        self.assertIn(b"QUJD", a[2])  # the non-scene data URI survived
        a3, b3 = self.both("/", headers={"If-None-Match": a[1]["etag"]})
        self.assertEqual(a3[0], 304)
        self.assertEqual(a3[2], b3[2])

    def test_03_scene_audio_streams_with_ranges(self) -> None:
        full, rfull = self.both("/studio/scene-audio/vigil")
        self.assertEqual(full[0], 200)
        self.assertEqual(full[2], rfull[2])
        for rng in (
            "bytes=100-199",
            "bytes=-50",
            "bytes=2900-",
            "bytes=zz",
            "bytes=5-2",
        ):
            a, b = self.both("/studio/scene-audio/vigil", headers={"Range": rng})
            self.assertEqual(a[2], b[2], rng)
            self.assertEqual(a[1].get("content-range"), b[1].get("content-range"), rng)
            self.assertEqual(a[1].get("accept-ranges"), b[1].get("accept-ranges"), rng)
        a, b = self.both("/studio/scene-audio/nope")
        self.assertEqual(a[0], 404)
        self.assertEqual(self.parsed(a), self.parsed(b))
        a, b = self.both("/studio/scene-audio/%2e%2e")
        self.assertEqual(a[0], 404)
        self.assertEqual(self.parsed(a), self.parsed(b))

    def test_03b_a_zero_byte_scene_audio_is_an_empty_200(self) -> None:
        # An interrupted render leaves a 0-byte mp3 behind, and the Rust
        # server used to answer a Range over it by promising one byte and
        # then writing nothing — desyncing the keep-alive connection for
        # every later request on it (grade report B1).
        for build in (self.py_build, self.rs_build):
            (build / "audio" / "07_hollow.mp3").write_bytes(b"")
        for rng in (None, "bytes=0-", "bytes=0-0", "bytes=-50"):
            a, b = self.both(
                "/studio/scene-audio/hollow",
                headers={"Range": rng} if rng else None,
            )
            self.assertEqual(a[0], 200, rng)
            self.assertEqual(a[2], b"", rng)
            self.assertEqual(a[2], b[2], rng)
            self.assertEqual(a[1]["content-length"], b[1]["content-length"], rng)
            self.assertEqual(a[1].get("content-range"), b[1].get("content-range"), rng)

    def test_04_track_streams_by_id_and_extension(self) -> None:
        for path in ("/studio/track/t_alpha", "/studio/track/t_alpha.wav"):
            a, b = self.both(path)
            self.assertEqual(a[0], 200, path)
            self.assertEqual(a[2], b[2])
            self.assertEqual(a[1]["content-type"], b[1]["content-type"])
        a, b = self.both("/studio/track/t_alpha", headers={"Range": "bytes=0-99"})
        self.assertEqual(a[0], 206)
        self.assertEqual(a[2], b[2])
        a, b = self.both("/studio/track/nope")
        self.assertEqual(a[0], 404)
        self.assertEqual(self.parsed(a), self.parsed(b))

    def test_05_status_is_the_studio_marker(self) -> None:
        a, b = self.both("/api/status")
        self.assertEqual(self.parsed(a), {"studio": True})
        self.assertEqual(self.parsed(a), self.parsed(b))

    def test_06_the_api_alias_still_answers(self) -> None:
        a, b = self.both("/api/tracks")
        self.assertEqual(a[0], 200)
        self.assertEqual(self.parsed(a), self.parsed(b))

    def test_07_unknown_routes_answer_the_same_404(self) -> None:
        for path in ("/studio/nope", "/nope", "/studio/track/"):
            a, b = self.both(path)
            self.assertEqual(a[0], 404, path)
            self.assertEqual(self.parsed(a), self.parsed(b))

    def test_08_delete_takes_the_file_sources_and_manifest(self) -> None:
        a, b = self.both("/studio/tracks/t_del", method="DELETE")
        self.assertEqual(a[0], 200)
        self.assertEqual(self.parsed(a), self.parsed(b))
        for tracks in (self.py_tracks, self.rs_tracks):
            self.assertFalse((tracks / "t_del.wav").exists())
            self.assertFalse((tracks / "_src" / "t_del.orig.wav").exists())
            self.assertNotIn("t_del", (tracks / "tracks.json").read_text())
        self.assertEqual(
            (self.py_tracks / "tracks.json").read_text(),
            (self.rs_tracks / "tracks.json").read_text(),
        )
        a, b = self.both("/studio/tracks/t_del", method="DELETE")
        self.assertEqual(a[0], 404)
        self.assertEqual(self.parsed(a), self.parsed(b))


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class DeadCastle(StudioPair):
    """CASTLE_HOST names a port that refuses instantly — the relay's
    unreachable walk, without a single live socket."""

    HOST_ENV = "127.0.0.1:1"

    def test_status_names_who_it_tried(self) -> None:
        a, b = self.both("/api/status")
        self.assertEqual(self.parsed(a), {"studio": True, "castle": "127.0.0.1:1"})
        self.assertEqual(self.parsed(a), self.parsed(b))

    def test_castle_verbs_report_unreachable(self) -> None:
        a, b = self.both("/api/stop", method="POST")
        self.assertEqual(a[0], 502)
        self.assertEqual(self.parsed(a), self.parsed(b))
        a, b = self.both("/api/files")
        self.assertEqual(a[0], 502)
        self.assertEqual(self.parsed(a), self.parsed(b))

    def test_a_typo_is_not_an_outage(self) -> None:
        a, b = self.both("/api/nonsense")
        self.assertEqual(a[0], 404)
        self.assertEqual(self.parsed(a), self.parsed(b))


if __name__ == "__main__":
    unittest.main()
