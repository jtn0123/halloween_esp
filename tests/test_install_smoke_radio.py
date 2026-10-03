"""The install smoke test's conversation with Castle Radio, offline: the
page's upload headers, the job poll and its Demucs timing, the sync poll,
and what makes a synced song whole on the card — against a fake Castle
Radio on a loopback port, never the real one."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import threading
import unicodedata
import unittest
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import install_smoke_env as se  # tools/ on the path, then:

# isort: split
import cue_file
import install_smoke_radio as radio
import install_smoke_session as session

SONG = radio.SONGS[2]


class FakeRadio(BaseHTTPRequestHandler):
    """Castle Radio's routes as the smoke test speaks them, scripted."""

    heard: ClassVar[list[tuple[str, str, dict[str, str], bytes]]] = []
    jobs: ClassVar[list[list[dict[str, Any]]]] = []
    syncs: ClassVar[list[dict[str, Any]]] = []
    castle: ClassVar[dict[str, Any]] = {}

    def log_message(self, *_args: Any) -> None:
        pass

    def _send(self, status: int, body: Any, raw: bool = False) -> None:
        data = body if raw else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _heard(self) -> bytes:
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.heard.append((self.command, self.path, dict(self.headers), body))
        return body

    def do_GET(self) -> None:
        self._heard()
        if self.path == "/radio/jobs":
            page = self.jobs.pop(0) if len(self.jobs) > 1 else self.jobs[0]
            return self._send(200, page)
        if self.path.startswith("/radio/device/sync-status"):
            return self._send(200, self.syncs.pop(0))
        if self.path == "/api/status":
            return self._send(200, self.castle)
        if self.path == "/broken":
            return self._send(500, b"<html>no</html>", raw=True)
        return self._send(404, {"error": "not here"})

    def do_POST(self) -> None:
        body = self._heard()
        if self.path == "/radio/import":
            return self._send(202, {"id": "j1", "bytes": len(body)})
        if self.path == "/radio/device/sync":
            return self._send(202, self.syncs.pop(0))
        return self._send(400, {"error": "refused"})


class FakeCase(unittest.TestCase):
    def setUp(self) -> None:
        FakeRadio.heard, FakeRadio.jobs, FakeRadio.syncs = [], [], []
        server = ThreadingHTTPServer(("127.0.0.1", 0), FakeRadio)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.port = server.server_address[1]
        self.r = radio.Radio(f"http://127.0.0.1:{self.port}/")
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))


class ThePagesWords(unittest.TestCase):
    def test_x_filename_is_encode_uri_component(self) -> None:
        self.assertEqual(radio.page_filename(SONG.name),
                         "%C3%9Cn%C3%AFc%C3%B8d%C3%A9%20%E2%80%93%20%E9%AC%BC.mp3")  # fmt: skip
        self.assertEqual(radio.page_filename("a!'()*b"), "a!'()*b")

    def test_the_title_is_the_composed_stem(self) -> None:
        decomposed = unicodedata.normalize("NFD", "Ünï.mp3")
        self.assertEqual(radio.page_title(decomposed), "Ünï")
        self.assertEqual(len(radio.page_title("x" * 300 + ".wav")), 200)

    def test_ffmpeg_gets_an_expression_its_parser_accepts(self) -> None:
        cmd = radio.tone_command("ffmpeg", Path("out.mp3"), SONG)
        source = cmd[cmd.index("-i") + 1]
        self.assertIn("mod(t\\,0.5)", source)
        self.assertTrue(source.endswith(f":s=44100:d={SONG.seconds}"))
        self.assertEqual(cmd[-3:], ["-ac", "2", "out.mp3"])

    def test_phase_spans_charge_each_poll_to_its_phase(self) -> None:
        trail = [(0.0, "Queued"), (1.0, radio.SEPARATING), (4.0, radio.SEPARATING),
                 (6.0, "Encoding separated audio"), (7.0, "Ready in demo")]  # fmt: skip
        self.assertEqual(radio.phase_spans(trail), {
            "Queued": 1.0, radio.SEPARATING: 5.0, "Encoding separated audio": 1.0,
        })  # fmt: skip
        self.assertEqual(radio.phase_spans(trail[:1]), {})


class TheConversation(FakeCase):
    def test_upload_sends_the_import_panels_headers(self) -> None:
        song = self.tmp / "x.mp3"
        song.write_bytes(b"ID3abc")
        self.assertEqual(self.r.upload(song, SONG), {"id": "j1", "bytes": 6})
        _method, path, headers, body = FakeRadio.heard[-1]
        self.assertEqual((path, body), ("/radio/import", b"ID3abc"))
        self.assertEqual(headers["X-Castle"], "1")
        self.assertEqual(headers["X-Split"], "true")
        self.assertEqual(headers["X-Filename"], radio.page_filename(SONG.name))
        self.assertEqual(headers["Content-Type"], "application/octet-stream")

    def test_jobs_are_polled_until_done_with_their_trail(self) -> None:
        job = {"id": "j1", "title": "t", "done": False}
        FakeRadio.jobs = [
            [{**job, "phase": "Queued"}],
            [{**job, "phase": radio.SEPARATING}],
            [{**job, "phase": "Ready in demo", "done": True}],
        ]
        with redirect_stdout(io.StringIO()):
            final, trail = self.r.wait_jobs(["j1"], 30)
        self.assertEqual(final["j1"]["phase"], "Ready in demo")
        phases = [p for _t, p in trail["j1"]]
        self.assertEqual(phases[0], "Queued")
        self.assertEqual(phases[-1], "Ready in demo")

    def test_a_vanished_or_endless_job_fails_the_run(self) -> None:
        FakeRadio.jobs = [[]]
        with self.assertRaises(SystemExit) as gone:
            self.r.wait_jobs(["j1"], 30)
        self.assertIn("vanished", str(gone.exception))
        FakeRadio.jobs = [[{"id": "j1", "phase": "Queued", "done": False}]]
        with self.assertRaises(SystemExit) as late, redirect_stdout(io.StringIO()):
            self.r.wait_jobs(["j1"], 0.3)
        self.assertIn("not finished", str(late.exception))

    def test_sync_polls_its_status_until_done(self) -> None:
        FakeRadio.syncs = [{"done": False, "phase": "Sending"},
                           {"done": True, "phase": "Audio and show verified on castle"}]  # fmt: skip
        self.assertEqual(self.r.sync("radio_a")["phase"],
                         "Audio and show verified on castle")  # fmt: skip
        self.assertTrue(FakeRadio.heard[-1][1].endswith("?key=radio_a"))
        FakeRadio.syncs = [{"done": False}, *[{"done": False}] * 50]
        with self.assertRaises(SystemExit):
            self.r.sync("radio_a", seconds=0.2)

    def test_refusals_say_what_was_asked(self) -> None:
        self.assertEqual(self.r.call("/broken"), (500, "<html>no</html>"))
        with self.assertRaises(SystemExit) as get:
            self.r.get("/nowhere")
        self.assertIn("404", str(get.exception))
        with self.assertRaises(SystemExit) as post:
            self.r.post("/radio/device/command", {"action": "stop"})
        self.assertIn("refused", str(post.exception))

    def test_wait_playing_reads_the_castles_own_status(self) -> None:
        FakeRadio.castle = {"track": "radio_a.mp3", "cues": 12}
        self.assertEqual(radio.wait_playing(self.port, "radio_a.mp3")["cues"], 12)
        self.assertEqual(se.castle_status(self.port)["track"], "radio_a.mp3")


class TheCard(unittest.TestCase):
    def setUp(self) -> None:
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.card, self.library = tmp / "card", tmp / "library"
        self.card.mkdir()
        self.library.mkdir()
        scene = {"id": "radio_a", "duration_ms": 4000, "base": {"door": "ember"}}
        cues = [{"t": 0, "op": "strike", "zone": "door"}]
        files = {
            "radio_a.mp3": b"ID3audio",
            "radio_a.show.json": b'{"cue_crc32": "0"}',
            "radio_a.cue": cue_file.encode(scene, cues, ["towerL", "towerR", "door"]),
        }
        for name, data in files.items():
            (self.card / name).write_bytes(data)
            (self.library / name).write_bytes(data)

    def problems(self) -> list[str]:
        return radio.card_problems(self.card, self.library, "radio_a.mp3")

    def test_a_whole_song_has_no_problems(self) -> None:
        self.assertEqual(self.problems(), [])

    def test_each_way_a_song_can_arrive_broken(self) -> None:
        (self.card / "radio_a.mp3").write_bytes(b"ID3audi")
        self.assertEqual(
            self.problems(), ["radio_a.mp3 on the card differs from the library's"]
        )
        (self.card / "radio_a.mp3").unlink()
        (self.library / "radio_a.show.json").unlink()
        (self.card / "radio_a.show.json").write_text("{", encoding="utf-8")
        got = self.problems()
        self.assertIn("radio_a.mp3 is not on the card", got)
        self.assertIn("radio_a.show.json is not in the library to compare", got)
        self.assertTrue(any("is not JSON" in p for p in got))
        empty = cue_file.encode({"id": "radio_a", "duration_ms": 1}, [], ["door"])
        (self.card / "radio_a.cue").write_bytes(empty)
        self.assertIn(
            "radio_a.cue holds no cues the castle would load", self.problems()
        )


class TheDemucsLine(unittest.TestCase):
    def test_the_row_the_step_summary_and_the_log_agree(self) -> None:
        trail = [(10.0, "Queued"), (11.0, radio.SEPARATING),
                 (41.0, "Encoding separated audio"), (43.0, "Analyzing separated audio"),
                 (45.0, "Ready in demo")]  # fmt: skip
        row = session.demucs_report(SONG, trail)
        self.assertEqual((row["separate_s"], row["split_step_s"], row["whole_import_s"]),
                         (30.0, 34.0, 35.0))  # fmt: skip
        self.assertTrue(row["cpu"])
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (tmp / "logs").mkdir()
        summary = tmp / "summary.md"
        with (
            mock.patch.dict(os.environ, {"GITHUB_STEP_SUMMARY": str(summary)}),
            redirect_stdout(io.StringIO()) as out,
        ):
            session.say_demucs(row, tmp)
        self.assertIn("separated in 30.0 s", out.getvalue())
        self.assertEqual(
            json.loads((tmp / "logs" / "demucs.json").read_text(encoding="utf-8")), row
        )
        self.assertIn(
            "| 30.0 s | 34.0 s | 35.0 s |", summary.read_text(encoding="utf-8")
        )


if __name__ == "__main__":
    unittest.main()
