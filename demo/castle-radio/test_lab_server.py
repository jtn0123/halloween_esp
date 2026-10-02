"""The lab server: static files as before, and a notes file only it writes."""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import lab_server

NOTE = {"t": 83_500, "song": "radio_1", "name": "Monster Mash", "current": "current",
        "candidate": "spin", "about": "candidate", "tags": ["boring"],
        "text": "door just sits there", "section": "B · Haunt · sweep",
        "firmware": "own", "soften": True}  # fmt: skip


class Server(unittest.TestCase):
    def setUp(self):
        quiet = mock.patch.object(lab_server.LabHandler, "log_message")
        quiet.start()
        self.addCleanup(quiet.stop)
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "show-lab.html").write_text("<p>lab</p>", encoding="utf-8")
        self.httpd = lab_server.server(self.dir, 0, "127.0.0.1")
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def post(self, path, body):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=5) as reply:
            return reply.status, json.loads(reply.read())

    def refused(self, path, body):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.post(path, body)
        return caught.exception.code

    def test_the_page_is_still_served_and_never_cached(self):
        with urllib.request.urlopen(self.base + "/show-lab.html", timeout=5) as r:
            self.assertEqual(r.read(), b"<p>lab</p>")
            self.assertEqual(r.headers["Cache-Control"], "no-store")

    def test_a_note_is_appended_and_read_back(self):
        status, stored = self.post("/notes", NOTE)
        self.assertEqual(status, 201)
        self.post("/notes", {**NOTE, "t": 1000, "tags": ["love"], "text": ""})
        lines = (self.dir / "notes.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(line)["t"] for line in lines], [83_500, 1000])
        self.assertEqual(stored["text"], "door just sits there")
        self.assertIn("at", stored)
        with urllib.request.urlopen(self.base + "/notes.jsonl", timeout=5) as r:
            self.assertEqual(len(r.read().splitlines()), 2)

    def test_no_notes_yet_reads_as_an_empty_list_and_writes_nothing(self):
        with urllib.request.urlopen(self.base + "/notes.jsonl", timeout=5) as r:
            self.assertEqual((r.status, r.read()), (200, b""))
        self.assertFalse((self.dir / "notes.jsonl").exists())

    def test_what_is_not_a_note_is_refused_and_nothing_is_written(self):
        for bad in (
            {**NOTE, "tags": ["rm -rf"]},  # a tag the page does not offer
            {**NOTE, "about": "../x"},
            {**NOTE, "t": -1},
            {**NOTE, "text": 5},
            {**NOTE, "tags": [], "text": "  "},  # says nothing
            ["not", "an", "object"],
        ):
            self.assertEqual(self.refused("/notes", bad), 400, bad)
        self.assertEqual(self.refused("/notes", b"{not json"), 400)
        self.assertEqual(self.refused("/notes", b"x" * 5000), 413)
        self.assertEqual(self.refused("/show-lab.html", NOTE), 404)
        self.assertFalse((self.dir / "notes.jsonl").exists())

    def test_long_text_is_cut_not_refused(self):
        _status, stored = self.post("/notes", {**NOTE, "text": "a" * 900})
        self.assertEqual(len(stored["text"]), 500)


class Report(unittest.TestCase):
    def test_notes_read_back_song_by_song_in_song_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "notes.jsonl")
            self.assertEqual(lab_server.report(path), ["no notes yet"])
            liked = {**NOTE, "t": 5000, "about": "both", "tags": ["love"]}
            rows = [
                lab_server.clean(NOTE),
                lab_server.clean({**liked, "text": "", "section": ""}),
            ]
            path.write_text(
                "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
            )
            self.assertEqual(
                lab_server.report(path),
                [
                    "Monster Mash",
                    "    0:05  current + spin         [love]",
                    (
                        '    1:23  spin                   [boring] "door just sits there"'
                        "  (B · Haunt · sweep)"
                    ),
                ],
            )


if __name__ == "__main__":
    unittest.main()
