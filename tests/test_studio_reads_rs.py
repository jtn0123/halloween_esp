"""The studio's read surface — the page, the listing, the streams.

This is what `tests/test_studio_rust.py` and the read half of
`tests/test_studio_api.py` became when `tools/studio.py` retired
(docs/RETIREMENT.md phase 3). Those suites asked "do the two servers
answer the same?"; with one server left the question is "what IS the
answer", so every case here states it: the bytes on disk, the
Content-Range, the 404 body, the manifest after a delete.

The cases the corpus can hold as fixed bytes live in `tests/golden/` and
are replayed by `tests/test_studio_golden.py`. What is here is everything
that cannot: ranges over real files, a page rewrite, a decode that must
not happen twice, and a socket that must be closed rather than left owing
bytes.

Skipped, not failed, without cargo — except in CI.
"""

from __future__ import annotations

import json
import socket
import sys
import time
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from studio_rs_case import CARGO, IN_CI, StudioCase


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class Reads(StudioCase):
    """CASTLE_HOST='' — no castle, so nothing here can reach the LAN."""

    def listing(self) -> dict[str, Any]:
        code, body = self.json("/studio/tracks")
        self.assertEqual(code, 200)
        return body

    def test_01_the_listing_names_every_container_and_the_show(self) -> None:
        body = self.listing()
        self.assertEqual(sorted(body), ["scenes", "tracks"])
        self.assertEqual(body["scenes"], ["vigil", "storm"])
        rows = {t["id"]: t for t in body["tracks"]}
        self.assertEqual(
            sorted(rows), ["t_alpha", "t_beta", "t_del", "t_empty", "t_meta"]
        )
        alpha = rows["t_alpha"]
        self.assertEqual(alpha["ext"], "wav")
        self.assertEqual(alpha["bytes"], (self.tracks / "t_alpha.wav").stat().st_size)
        self.assertGreater(alpha["kb"], 0)
        self.assertAlmostEqual(alpha["dur"], 2.0, delta=0.1)
        self.assertGreater(alpha["onsets"]["onset_low"], 0)
        # The manifest's cached entry is reported without a decode, and its
        # level_* entry is not an onset (it is an envelope, and the panel
        # would count it as beats).
        meta = rows["t_meta"]
        self.assertEqual(meta["dur"], 2.34)
        self.assertEqual(meta["title"], "Späti 🎃")
        self.assertEqual(meta["onsets"], {"onset_low": 3, "onset_mid": 5})
        # A `file:` source whose file is gone must be flagged: the panel
        # offers Re-import from it otherwise (judge B, JB1-3).
        self.assertTrue(rows["t_del"]["source_missing"])
        self.assertFalse(rows["t_meta"]["source_missing"])

    def test_02_the_second_listing_is_answered_without_decoding_again(self) -> None:
        """The first listing decodes what the manifest has not seen and
        writes the answer back beside the provenance; the second reads it.
        The proof from outside is that the manifest gained the durations
        and onsets, kept `source`/`notes`, and the answer did not move."""
        first = self.listing()
        manifest = json.loads((self.tracks / "tracks.json").read_text())
        self.assertEqual(
            manifest["t_meta"]["notes"], "cached entry — no decode should happen"
        )
        self.assertEqual(
            manifest["t_alpha"]["audio"]["bytes"],
            (self.tracks / "t_alpha.wav").stat().st_size,
        )
        self.assertIn("onset_low", manifest["t_alpha"]["onsets"])
        before = (self.tracks / "tracks.json").stat().st_mtime_ns
        second = self.listing()
        self.assertEqual(first, second)
        self.assertEqual((self.tracks / "tracks.json").stat().st_mtime_ns, before)

    def test_03_the_page_is_served_lean_and_validated(self) -> None:
        code, headers, body = self.req("/")
        self.assertEqual(code, 200)
        self.assertTrue(headers["content-type"].startswith("text/html"))
        # Every scene's inlined data URI becomes its route; a data URI that
        # is not a scene entry survives untouched.
        self.assertIn(b'"vigil": "/studio/scene-audio/vigil"', body)
        self.assertIn(b'"storm": "/studio/scene-audio/storm"', body)
        self.assertNotIn(b"SGVsbG8=", body)
        self.assertIn(b"QUJD", body)
        etag = headers["etag"]
        self.assertTrue(etag.startswith('"') and etag.endswith('-lean"'), etag)
        code2, _h2, body2 = self.req("/", headers={"If-None-Match": etag})
        self.assertEqual(code2, 304)
        self.assertEqual(body2, b"")
        # /index.html is the same page, and a stale validator is a 200.
        self.assertEqual(self.req("/index.html")[0], 200)
        self.assertEqual(self.req("/", headers={"If-None-Match": '"stale"'})[0], 200)

    def test_04_scene_audio_streams_the_file_and_its_ranges(self) -> None:
        on_disk = (self.build / "audio" / "01_vigil.mp3").read_bytes()
        code, headers, body = self.req("/studio/scene-audio/vigil")
        self.assertEqual(code, 200)
        self.assertEqual(body, on_disk)
        self.assertEqual(headers["content-type"], "audio/mpeg")
        self.assertEqual(headers.get("accept-ranges"), "bytes")
        total = len(on_disk)
        for rng, want, partial in (
            ("bytes=100-199", on_disk[100:200], True),
            ("bytes=-50", on_disk[-50:], True),
            ("bytes=2900-", on_disk[2900:], True),
            # Nonsense, a backwards range and a bare `bytes=` are the whole
            # file at 200 — never a truncated 206.
            ("bytes=zz", on_disk, False),
            ("bytes=5-2", on_disk, False),
            ("bytes=", on_disk, False),
            ("bytes=-", on_disk, False),
        ):
            code, headers, body = self.req(
                "/studio/scene-audio/vigil", headers={"Range": rng}
            )
            self.assertEqual(code, 206 if partial else 200, rng)
            self.assertEqual(body, want, rng)
            self.assertEqual(headers["content-length"], str(len(want)), rng)
            if partial:
                start = (
                    total - len(want)
                    if rng.startswith("bytes=-")
                    else int(rng.split("=")[1].split("-")[0])
                )
                self.assertEqual(
                    headers["content-range"],
                    f"bytes {start}-{start + len(want) - 1}/{total}",
                    rng,
                )
            else:
                self.assertIsNone(headers.get("content-range"), rng)

    def test_05_a_zero_byte_scene_audio_is_an_empty_200(self) -> None:
        """An interrupted render leaves a 0-byte mp3 behind, and a Range
        over it once promised one byte and then wrote nothing — desyncing
        the keep-alive connection for every later request on it (grade
        report 2026-08-31 B1)."""
        (self.build / "audio" / "07_hollow.mp3").write_bytes(b"")
        self.addCleanup((self.build / "audio" / "07_hollow.mp3").unlink, True)
        for rng in (None, "bytes=0-", "bytes=0-0", "bytes=-50", "bytes=-"):
            code, headers, body = self.req(
                "/studio/scene-audio/hollow", headers={"Range": rng} if rng else None
            )
            self.assertEqual(code, 200, rng)
            self.assertEqual(body, b"", rng)
            self.assertEqual(headers["content-length"], "0", rng)
            self.assertIsNone(headers.get("content-range"), rng)

    def test_06_an_unrendered_or_hostile_scene_id_is_a_404(self) -> None:
        for path in (
            "/studio/scene-audio/nope",
            "/studio/scene-audio/%2e%2e",
            "/studio/scene-audio/01_vigil.mp3",
            "/studio/scene-audio/",
        ):
            code, body = self.json(path)
            self.assertEqual(code, 404, path)
            self.assertIn("error", body)

    def test_07_a_track_streams_by_id_with_or_without_its_extension(self) -> None:
        on_disk = (self.tracks / "t_alpha.wav").read_bytes()
        for path in ("/studio/track/t_alpha", "/studio/track/t_alpha.wav"):
            code, headers, body = self.req(path)
            self.assertEqual(code, 200, path)
            self.assertEqual(body, on_disk, path)
            self.assertEqual(headers["content-type"], "audio/wav", path)
        code, headers, body = self.req(
            "/studio/track/t_alpha", headers={"Range": "bytes=10-19"}
        )
        self.assertEqual(code, 206)
        self.assertEqual(body, on_disk[10:20])
        self.assertEqual(headers["content-range"], f"bytes 10-19/{len(on_disk)}")

    def test_08_the_audio_route_is_not_a_file_server(self) -> None:
        """It resolves an ID to a container, and nothing else: the
        manifest sitting in the same directory is not a track."""
        for path in (
            "/studio/track/tracks.json",
            "/studio/track/nope",
            "/studio/track/",
            "/studio/track/..%2ftracks.json",
        ):
            code, body = self.json(path)
            self.assertEqual(code, 404, path)
            self.assertIn("error", body)

    def test_09_the_studio_marks_itself_when_no_castle_answers(self) -> None:
        self.assertEqual(self.json("/api/status"), (200, {"studio": True}))
        # The deprecated /api/ spellings still answer for one more release.
        self.assertEqual(self.json("/api/tracks")[0], 200)
        self.assertEqual(self.req("/api/track/t_alpha")[0], 200)
        self.assertEqual(self.req("/api/waveform/t_alpha")[0], 200)
        self.assertEqual(self.json("/api/job/zzz")[0], 404)
        # ...and /studio/status is not one of them.
        self.assertEqual(self.json("/studio/status")[0], 404)

    def test_10_unknown_routes_are_404s_not_relays(self) -> None:
        for path in ("/studio/nope", "/nope", "/studio/tracks/../nope"):
            code, body = self.json(path)
            self.assertEqual(code, 404, path)
            self.assertEqual(body, {"error": "not found"}, path)
        # A castle route that does not exist names what does, so a desk
        # typo does not read as an outage.
        code, body = self.json("/api/nonsense")
        self.assertEqual(code, 404)
        self.assertEqual(body["error"], "unknown castle route")
        self.assertIn("/api/status", body["known"])

    def test_11_the_wrong_verb_on_a_real_path_is_answered(self) -> None:
        for method, path in (
            ("DELETE", "/studio/tracks"),
            ("PUT", "/studio/nope"),
            ("POST", "/studio/waveform/t_alpha"),
        ):
            code, body = self.json(path, method)
            self.assertEqual(code, 404, f"{method} {path}")
            self.assertIn("error", body)

    def test_12_delete_takes_the_file_its_original_and_the_manifest(self) -> None:
        code, body = self.json("/studio/tracks/t_del", "DELETE")
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["removed"])
        self.assertFalse((self.tracks / "t_del.wav").exists())
        self.assertFalse((self.tracks / "_src" / "t_del.orig.wav").exists())
        self.assertNotIn("t_del", json.loads((self.tracks / "tracks.json").read_text()))
        # Twice is a 404, not a second success.
        code, body = self.json("/studio/tracks/t_del", "DELETE")
        self.assertEqual(code, 404)
        self.assertEqual(body, {"error": "not found"})

    def test_13_a_body_that_comes_up_short_ends_the_connection(self) -> None:
        """A file that shrinks mid-serve must close, not stay keep-alive.

        Content-Length promised bytes the file no longer has; kept alive,
        the client reads the NEXT response as the tail of this body and
        every later request on that connection is answered to the wrong
        question (grade report 2026-09-01 B1).
        """
        # Bigger than any socket buffer, so the server is certainly still
        # writing (blocked) when the file is truncated under it.
        size = 24 * 1024 * 1024
        shrink = self.tracks / "t_shrink.wav"
        shrink.write_bytes(bytes(size))
        self.addCleanup(shrink.unlink, True)
        with socket.create_connection(("127.0.0.1", self.port), timeout=20) as s:
            s.sendall(
                b"GET /studio/track/t_shrink HTTP/1.1\r\n"
                b"Host: 127.0.0.1\r\nConnection: keep-alive\r\n\r\n"
            )
            head = b""
            while b"\r\n\r\n" not in head:
                part = s.recv(65536)
                self.assertTrue(part, "closed before the head")
                head += part
            head, rest = head.split(b"\r\n\r\n", 1)
            self.assertIn(b" 200 ", head.split(b"\r\n", 1)[0])
            self.assertIn(b"Content-Length: %d" % size, head)
            time.sleep(0.5)  # the server is now blocked on the write
            shrink.write_bytes(b"")
            got = len(rest)
            try:
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    got += len(chunk)
            except TimeoutError:
                self.fail(f"kept a connection that owes {size - got} bytes")
            self.assertLess(got, size, "the body did not come up short")


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class DeadCastle(StudioCase):
    """CASTLE_HOST names a port that refuses instantly — the relay's
    unreachable walk, without a single live socket."""

    HOST_ENV = "127.0.0.1:1"

    def test_status_names_who_it_tried(self) -> None:
        self.assertEqual(
            self.json("/api/status"),
            (200, {"studio": True, "castle": "127.0.0.1:1"}),
        )

    def test_castle_verbs_report_unreachable(self) -> None:
        for method, path in (("POST", "/api/stop"), ("GET", "/api/files")):
            code, body = self.json(path, method)
            self.assertEqual(code, 502, path)
            self.assertEqual(body, {"error": "castle not reachable"}, path)

    def test_a_typo_is_not_an_outage(self) -> None:
        code, body = self.json("/api/nonsense")
        self.assertEqual(code, 404)
        self.assertEqual(body["error"], "unknown castle route")


if __name__ == "__main__":
    unittest.main()
