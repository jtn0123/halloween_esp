"""The studio's import surface — uploads, refresh, jobs, and the options.

This is what `tests/test_studio_import_rust.py` and the import half of
`tests/test_studio_api.py` / `tests/test_studio_tracks_api.py` became when
`tools/studio.py` retired (docs/RETIREMENT.md phase 3). The parity suite
asked "do the two servers answer the same?" and the Python suites asked
"what argv did the server build?" — by mocking the subprocess away. With
one server left, and nothing inside it to mock, every case here states the
ABSOLUTE answer and proves the option by its observable effect: the file
that landed, its container, its sample rate, its length, its head and
tail, and the row the panel reads back.

Nothing here reaches the network. The async import fetches from a
`ThreadingHTTPServer` this suite starts on 127.0.0.1, and the sandbox is
the fixture's own (CLAUDE.md's sandboxing section).

Skipped, not failed, without cargo or ffmpeg — except in CI, where both
are installed and a skip would be a hole.
"""

from __future__ import annotations

import json
import shutil
import sys
import threading
import time
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from helpers import make_click_track
from studio_rs_case import CARGO, IN_CI, StudioCase

#: ffmpeg is the importer's whole engine. Missing, the import cases cannot
#: run at all — so they skip loudly, and never in CI.
NO_FFMPEG = shutil.which("ffmpeg") is None and not IN_CI

BOUND = "XIMPORTBOUND"
JSON_HDRS = {"Content-Type": "application/json"}

#: Every phase the job runner can report (core/src/studio_jobs.rs).
PHASES = {"queued", "fetching", "converting", "analysing", "done", "failed"}


def multipart(
    name: str, data: bytes, opts: dict[str, object] | None = None
) -> tuple[dict[str, str], bytes]:
    """A browser's upload: one file part, options in the side-channel header."""
    body = (
        (
            f"--{BOUND}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "\r\n"
        ).encode()
        + data
        + f"\r\n--{BOUND}--\r\n".encode()
    )
    headers = {"Content-Type": f"multipart/form-data; boundary={BOUND}"}
    if opts is not None:
        headers["X-Import-Opts"] = json.dumps(opts)
    return headers, body


class Quiet(SimpleHTTPRequestHandler):
    """The stand-in for the internet: a directory, and no request log."""

    def log_message(self, fmt: str, *a: object) -> None:
        pass


class ImportCase(StudioCase):
    """The shared verbs: upload a file, find its row, read the manifest."""

    def upload(
        self, name: str, data: bytes, opts: dict[str, object] | None = None
    ) -> tuple[int, dict[str, Any]]:
        headers, body = multipart(name, data, opts)
        code, _h, raw = self.req("/studio/import", "POST", headers, body)
        parsed = json.loads(raw)
        assert isinstance(parsed, dict), parsed
        return code, parsed

    def row(self, body: dict[str, Any], tid: str) -> dict[str, Any]:
        """The one track row the answer carries for this id."""
        rows = [r for r in body["tracks"] if r["id"] == tid]
        self.assertEqual(len(rows), 1, f"{tid} is not in the refreshed list")
        found = rows[0]
        assert isinstance(found, dict)
        return found

    def entry(self, tid: str) -> dict[str, Any]:
        """What tracks.json remembers about this id."""
        data = json.loads((self.tracks / "tracks.json").read_text())
        self.assertIn(tid, data, "the import was not recorded")
        found = data[tid]
        assert isinstance(found, dict)
        return found

    def local_url(self, name: str) -> str:
        """A URL on this machine for the async import to fetch."""
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(Quiet, directory=str(self.tmp))
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        # LIFO: the loop stops, then the listening socket is closed.
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}/{name}"


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class ImportGuards(ImportCase):
    """What the routes refuse — every one of these answers before a single
    child process is spawned, so this class needs no ffmpeg."""

    def test_01_a_sync_import_needs_an_http_url(self) -> None:
        self.assertEqual(
            self.json("/studio/import", "POST", {}), (400, {"error": "no url"})
        )
        self.assertEqual(
            self.json("/studio/import", "POST", {"url": "   "}),
            (400, {"error": "no url"}),
        )
        for url in ("ftp://example.invalid/x", "file:///etc/passwd", "notaurl"):
            self.assertEqual(
                self.json("/studio/import", "POST", {"url": url}),
                (400, {"error": "url must be http(s)"}),
                url,
            )
        self.assertFalse((self.tracks / "_upload").exists())

    def test_02_an_upload_needs_a_file_and_readable_options(self) -> None:
        headers, raw = multipart("x.wav", b"")
        code, _h, out = self.req("/studio/import", "POST", headers, raw)
        self.assertEqual((code, out), (400, b'{"error": "no file in upload"}'))
        # X-Import-Opts goes through the same JSON boundary as a body, and
        # is read BEFORE the staging write — so a rejected upload leaves no
        # `_upload/` directory beside the library.
        headers, raw = multipart("clip.wav", b"RIFFfake")
        headers["X-Import-Opts"] = "{not json"
        code, _h, out = self.req("/studio/import", "POST", headers, raw)
        parsed = json.loads(out)
        self.assertEqual(code, 400)
        self.assertFalse(parsed["ok"])
        self.assertIn("not valid JSON", parsed["error"])
        self.assertFalse((self.tracks / "_upload").exists())
        self.assertFalse((self.tracks / "clip.mp3").exists())

    def test_03_an_async_import_needs_an_http_url(self) -> None:
        # An EMPTY body is not its own error here, as it is on the sync
        # route: no body parses to no url, and no url is not http(s).
        code, _h, out = self.req("/studio/import/async", "POST", JSON_HDRS, b"")
        self.assertEqual((code, out), (400, b'{"error": "url must be http(s)"}'))
        for req in ({}, {"url": "notaurl"}, {"url": "file:///etc/passwd"}):
            self.assertEqual(
                self.json("/studio/import/async", "POST", req),
                (400, {"error": "url must be http(s)"}),
                req,
            )

    def test_04_a_traversal_id_is_refused_on_import_and_on_refresh(self) -> None:
        """tests/test_safety_floor.py TestServerGuards, absolute: the id the
        browser sends is forwarded verbatim to the importer, so
        "../../audio/01_vigil" once walked out of tracks/ and overwrote show
        audio. Both spellings of the route refuse it."""
        refused = (400, {"error": "id: letters, digits and _ only"})
        for path in ("/studio/import", "/api/import", "/studio/import/async"):
            for bad in ("../evil", "../../audio/01_vigil", "bad id!", "a/b"):
                self.assertEqual(
                    self.json(
                        path, "POST", {"url": "https://example.invalid/x", "id": bad}
                    ),
                    refused,
                    (path, bad),
                )
        # The multipart spelling too — though the staged copy it already
        # wrote survives until the next import sweeps `_upload/`, exactly
        # as tools/studio.py left it.
        headers, raw = multipart("clip.wav", b"RIFFfake", {"id": "../evil"})
        code, _h, out = self.req("/studio/import", "POST", headers, raw)
        self.assertEqual((code, json.loads(out)), refused)
        for path in ("/studio/refresh", "/api/refresh"):
            for bad in ("../evil", "../../audio/01_vigil", ""):
                self.assertEqual(
                    self.json(path, "POST", {"id": bad}),
                    (400, {"error": "no id"}),
                    (path, bad),
                )
        # Nothing was written outside the library, and the show audio a
        # traversal aims at is byte-for-byte what it was.
        self.assertFalse((self.tracks.parent / "evil.mp3").exists())
        self.assertEqual(
            (self.build / "audio" / "01_vigil.mp3").read_bytes(), bytes(range(256)) * 12
        )

    def test_05_refresh_and_stems_name_what_they_cannot_find(self) -> None:
        self.assertEqual(
            self.json("/studio/refresh", "POST", {}), (400, {"error": "no id"})
        )
        # A split is only offered for a track that is actually there; a
        # missing or unknown id must not spawn Demucs to find that out.
        for req in ({}, {"id": "zzz"}, {"id": "../evil"}, {"id": "t_alpha.wav"}):
            self.assertEqual(
                self.json("/studio/stems", "POST", req),
                (400, {"error": "no such track"}),
                req,
            )

    def test_06_an_unknown_job_is_404_on_both_spellings(self) -> None:
        for path in ("/studio/job/deadbeefdead", "/api/job/deadbeefdead"):
            self.assertEqual(self.json(path), (404, {"error": "no such job"}), path)

    def test_07_a_body_over_the_cap_is_refused_before_it_is_read(self) -> None:
        """The server buffers bodies whole. A Content-Length it would never
        allocate for is a 400 off the head, with the connection closed —
        not a half-gigabyte read and then a refusal."""
        cap = 512 * 1024 * 1024
        code, _h, out = self.req(
            "/studio/import",
            "POST",
            {**JSON_HDRS, "Content-Length": str(cap + 1)},
            b"{}",
        )
        parsed = json.loads(out)
        self.assertEqual(code, 400)
        self.assertFalse(parsed["ok"])
        self.assertEqual(
            parsed["error"],
            f"request body too large ({cap + 1} bytes; the limit is {cap})",
        )
        # The server is still answering afterwards, on a fresh connection.
        self.assertEqual(self.json("/studio/import", "POST", {})[0], 400)


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
@unittest.skipIf(NO_FFMPEG, "no ffmpeg")
class Imports(ImportCase):
    """The real importer, against the sandbox library."""

    clip: ClassVar[Path]

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.clip = cls.tmp / "clip.wav"
        make_click_track(cls.clip, seconds=2.0)

    def test_01_an_upload_lands_the_track_its_original_and_the_row(self) -> None:
        code, body = self.upload(
            "dropped file.wav", self.clip.read_bytes(), {"id": "fresh"}
        )
        self.assertEqual(code, 200, body)
        self.assertEqual(sorted(body), ["log", "ok", "tracks"])
        self.assertTrue(body["ok"])
        self.assertIn("imported  tracks/fresh.mp3", self.masked(body["log"]))
        # The file, and the original kept beside the library — named by
        # TRACK id, not by the name the browser uploaded, so Re-import of a
        # dropped file has something to work from.
        landed = self.tracks / "fresh.mp3"
        kept = self.tracks / "_src" / "fresh.wav"
        # The importer resolves the path it remembers, and on macOS /tmp
        # resolves through /private — so the remembered source is compared
        # resolved, not as this test spelled the sandbox.
        source = f"file:{kept.resolve()}"
        self.assertTrue(landed.exists())
        self.assertEqual(kept.read_bytes(), self.clip.read_bytes())
        self.assertFalse(
            (self.tracks / "_upload").exists(), "the staging directory was left behind"
        )
        # The answer carries the refreshed list, so the panel needs no
        # second request to show the new row.
        row = self.row(body, "fresh")
        self.assertEqual(row["ext"], "mp3")
        self.assertEqual(row["bytes"], landed.stat().st_size)
        self.assertAlmostEqual(row["dur"], 2.0, delta=0.1)
        self.assertGreater(row["onsets"]["onset_low"], 0)
        self.assertEqual(row["source"], source)
        self.assertFalse(row["source_missing"])
        self.assertIn("t_alpha", [r["id"] for r in body["tracks"]])
        # ...and tracks.json remembers where it came from and how it was cut.
        entry = self.entry("fresh")
        self.assertEqual(entry["source"], source)
        self.assertEqual(entry["audio"]["format"], "mp3")
        self.assertAlmostEqual(entry["audio"]["duration"], 2.0, delta=0.1)
        self.assertEqual(
            {k: entry["opts"][k] for k in ("format", "bitrate", "channels")},
            {"format": "mp3", "bitrate": 96, "channels": 2},
        )
        self.assertEqual(entry["opts"]["normalize"], True)
        self.assertIsNone(entry["opts"]["take"])
        self.assertTrue(entry["imported"])

    def test_02_refresh_rebuilds_from_the_kept_source(self) -> None:
        """--refresh is the reason the manifest exists: no upload, no URL,
        just the id and whatever option changed."""
        code, body = self.upload("clip.wav", self.clip.read_bytes(), {"id": "again"})
        self.assertEqual(code, 200, body)
        before = (self.tracks / "again.mp3").stat().st_size
        code, body = self.json("/studio/refresh", "POST", {"id": "again"})
        self.assertEqual(code, 200, body)
        self.assertTrue(body["ok"])
        self.assertIn("imported  tracks/again.mp3", self.masked(body["log"]))
        self.assertAlmostEqual(self.row(body, "again")["dur"], 2.0, delta=0.1)
        # An override shortens the result, and is remembered for next time.
        code, body = self.json(
            "/studio/refresh", "POST", {"id": "again", "take": "0.5"}
        )
        self.assertEqual(code, 200, body)
        row = self.row(body, "again")
        self.assertAlmostEqual(row["dur"], 0.5, delta=0.1)
        self.assertEqual(row["opts"]["take"], "0.5")
        self.assertLess((self.tracks / "again.mp3").stat().st_size, before / 2)
        self.assertEqual(self.entry("again")["opts"]["take"], "0.5")

    def test_03_a_refresh_of_an_unremembered_id_is_one_line(self) -> None:
        code, body = self.json("/studio/refresh", "POST", {"id": "nosuch"})
        self.assertEqual(code, 500)
        self.assertFalse(body["ok"])
        self.assertEqual(
            body["reason"],
            "no remembered track 'nosuch' (tools/import_track.py --list)",
        )
        self.assertIn(body["reason"], body["log"])
        self.assertIn("t_alpha", [r["id"] for r in body["tracks"]])

    def test_04_a_failed_import_answers_a_reason_not_a_traceback(self) -> None:
        """The desk shows `reason` and hides `log` behind a disclosure, so a
        reason that is a stack trace is a UI full of Python."""
        code, body = self.upload("not-audio.wav", b"\x00garbage-not-a-riff")
        self.assertEqual(code, 500, body)
        self.assertEqual(sorted(body), ["log", "ok", "reason", "tracks"])
        self.assertFalse(body["ok"])
        reason = body["reason"]
        self.assertTrue(
            reason.startswith("not-audio.wav doesn't look like playable audio"),
            reason,
        )
        # ONE line — ffmpeg's own complaint is in the tail, which varies by
        # ffmpeg build, so only its shape is pinned here.
        self.assertNotIn("\n", reason)
        self.assertNotIn("Traceback", reason)
        self.assertIn(reason, body["log"], "the full output must stay in log")
        self.assertFalse((self.tracks / "not_audio.mp3").exists())
        self.assertFalse((self.tracks / "_upload").exists())

    def test_05_an_async_import_polls_from_queued_to_done(self) -> None:
        """The page starts the job, polls, and stops on `done` — so the
        refreshed list has to ride on the last response or the fetched
        track never appears."""
        url = self.local_url("clip.wav")
        code, job = self.json(
            "/studio/import/async", "POST", {"url": url, "id": "fetched"}
        )
        self.assertEqual(code, 200, job)
        self.assertEqual(job["phase"], "queued")
        self.assertEqual((job["percent"], job["done"], job["log"]), (0.0, False, []))
        self.assertTrue(job["id"])
        seen: list[str] = []
        deadline = time.monotonic() + 180
        poll: dict[str, Any] = {}
        while time.monotonic() < deadline:
            code, poll = self.json(f"/studio/job/{job['id']}")
            self.assertEqual(code, 200, poll)
            self.assertEqual(poll["id"], job["id"])
            self.assertIn(poll["phase"], PHASES, poll)
            self.assertGreaterEqual(poll["percent"], 0.0)
            self.assertLessEqual(poll["percent"], 100.0)
            seen.append(poll["phase"])
            if poll["done"]:
                break
            self.assertNotIn("tracks", poll, "the list rides on the LAST poll only")
            time.sleep(0.05)
        else:
            self.fail(f"the job never finished: {poll}")
        self.assertEqual((poll["phase"], poll["percent"]), ("done", 100.0))
        self.assertEqual(poll["error"], "")
        # Which running phase a poll catches depends on how fast the fetch
        # was — "analysing" can pass between two polls — so what is pinned
        # is that every phase before the end was a running one, and that
        # the finished job kept the importer's own output.
        self.assertEqual(seen[-1], "done")
        self.assertLessEqual(
            set(seen[:-1]), {"queued", "fetching", "converting", "analysing"}, seen
        )
        self.assertTrue(poll["log"], "the job reported nothing at all")
        self.assertTrue((self.tracks / "fetched.mp3").exists())
        row = self.row(poll, "fetched")
        self.assertAlmostEqual(row["dur"], 2.0, delta=0.1)
        self.assertEqual(row["source"], url)
        self.assertEqual(self.entry("fetched")["source"], url)


if __name__ == "__main__":
    unittest.main()
