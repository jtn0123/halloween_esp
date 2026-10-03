"""The Rust studio's publish, media probes and server ops — absolutely.

The other half of the port described in tests/test_studio_relay_rs.py:
what `tests/test_studio_relay_rust.py`'s `PublishToTwinCastles` and
`MediaAndOps` measured by holding two servers side by side, restated as
the answer itself now that only one server is left.

Publish gets an emulated castle of its own so the push has somewhere real
to land and the card can be listed afterwards; probe, compare and the
server ops get a castle-less studio, because none of them talk to
hardware. Restart and stop live in their own fixture: one execv's the
process and the other kills it, and neither should be able to take a
suite's shared server with it.
"""

from __future__ import annotations

import http.client
import json
import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from typing import Any, ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parent))

from studio_rs_case import (
    CARGO,
    IN_CI,
    NOT_SERVING,
    ROOT,
    StudioCase,
    fetch,
    wait_up,
)

sys.path.insert(0, str(ROOT / "tools"))
import castle_emu

JSON_HDRS = {"Content-Type": "application/json"}


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class Publish(StudioCase):
    """POST /studio/publish with a castle answering: the scene tracks and
    the Castle Radio page go to the card, and what a push CANNOT fix is named."""

    emu: ClassVar[castle_emu.CastleEmu]

    @classmethod
    def setUpClass(cls) -> None:
        # Booted knowing only `vigil` — so the show's second scene is one the
        # running castle learns from the publish itself (v5.69), or, on a
        # castle that predates that, a gap the publish has to name.
        cls.emu = castle_emu.CastleEmu(port=0, sd_dir=None, scenes=["vigil"])
        cls.emu.start()
        cls.HOST_ENV = f"127.0.0.1:{cls.emu.port}"
        super().setUpClass()

    @classmethod
    def tearDownClass(cls) -> None:
        super().tearDownClass()
        cls.emu.shutdown()
        cls.emu.server_close()

    def test_publish_lands_on_the_card(self) -> None:
        code, body = self.json("/studio/publish", "POST")
        self.assertEqual(code, 200, body)
        log = str(body.pop("log"))
        # The castle re-read show.man when the push landed it (v5.69), so a
        # new scene asks for nothing: the old answer, needs_reboot:["storm"]
        # from the status taken BEFORE the push, sent the operator to reboot
        # after every publish that added a scene (grade report 2026-09-24 H1).
        self.assertEqual(
            body, {"ok": True, "pushed": True, "needs_reboot": [], "note": ""}
        )
        self.assertIn("storm", self.emu.scenes)
        masked = self.masked(log).replace(self.HOST_ENV, "<CASTLE>")
        # The build dir is outside the repo, so the line names it the way
        # the OS spells paths (build_paths.rel): a backslash on Windows.
        self.assertRegex(masked, r"source: <BUILD>[\\/]audio/")
        self.assertIn("uploading 01_vigil.mp3", masked)
        self.assertIn("1 scene tracks in /sd/scenes/", masked)
        self.assertIn("2 cue files (2 sent) + show.man", masked)
        self.assertIn("http://<CASTLE>/ now serves Castle Radio", masked)
        # The scene track to /sd/scenes and the Castle Radio page pair to
        # /sd/site (2026-09-15) — those three files and nothing else: the
        # page streams scene audio from /sd/scenes/, so nothing is pushed
        # beside it.
        sd = Path(self.emu.sd_dir)
        self.assertEqual(
            sorted(f.relative_to(sd).as_posix() for f in sd.rglob("*") if f.is_file()),
            [
                # The show itself is card data since v5.67 — one .cue per
                # scene and the manifest that names them — so a publish
                # lands five files, not three.
                "scenes/01_vigil.mp3",
                "scenes/show.man",
                "scenes/storm.cue",
                "scenes/vigil.cue",
                "site/index.html",
                "site/index.html.gz",
            ],
        )
        # One self-contained page with the castle shim ahead of the app.
        page = (sd / "site" / "index.html").read_bytes()
        self.assertLess(page.find(b"Castle direct:"), page.find(b"Standalone concept"))
        self.assertNotIn(b'src="', page)

    def test_publish_to_a_castle_from_before_v5_69_still_says_reboot(self) -> None:
        """The one castle `needs_reboot` is still for: show.man lands, and
        nothing re-reads it until a restart. Named to run after the test
        above, whose log wants a card the push has not filled yet."""
        self.emu.reseeds = False
        self.emu.scenes = ["vigil"]
        # The studio caches the castle's status for 1.5 s; wait until its
        # view is the castle this test just set up, not the one above's.
        deadline = time.monotonic() + 10
        while "storm" in str(self.json("/api/status")[1].get("scenes")):
            self.assertLess(time.monotonic(), deadline, "status never refreshed")
            time.sleep(0.1)
        try:
            code, body = self.json("/studio/publish", "POST")
        finally:
            self.emu.reseeds = True
        self.assertEqual(code, 200, body)
        self.assertEqual(body["needs_reboot"], ["storm"])
        self.assertEqual(
            body["note"],
            "1 scene(s) the castle has not read after the push — firmware "
            "before v5.69 reads show.man only at boot: reboot it, or update it",
        )


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class Media(StudioCase):
    """Probe, compare and the castle-less publish — no hardware needed."""

    def post(self, path: str, obj: object) -> tuple[int, dict[str, Any]]:
        code, _, raw = self.req(path, "POST", JSON_HDRS, json.dumps(obj).encode())
        parsed = json.loads(raw)
        assert isinstance(parsed, dict), parsed
        return code, parsed

    def test_00_publish_without_a_castle(self) -> None:
        code, body = self.json("/studio/publish", "POST")
        self.assertEqual(
            (code, body),
            (
                502,
                {
                    "ok": False,
                    "pushed": False,
                    "error": "no castle answered — nothing pushed",
                },
            ),
        )

    def test_01_probe_refuses_a_non_link_before_it_spawns_anything(self) -> None:
        code, body = self.post("/studio/probe", {"url": "notalink"})
        self.assertEqual(
            (code, body),
            (400, {"ok": False, "error": "that does not look like a link"}),
        )

    def test_02_probe_of_an_unreachable_url_is_a_400_not_a_500(self) -> None:
        code, body = self.post(
            "/studio/probe", {"url": "https://127.0.0.1:1/never.wav"}
        )
        self.assertEqual(code, 400)
        self.assertIs(body["ok"], False)
        # The fetcher's own words, carrying the address that failed —
        # the operator needs to see WHICH url did not answer.
        self.assertIn("127.0.0.1", str(body["error"]))

    def test_03_compare_ranks_the_codecs(self) -> None:
        code, body = self.post(
            "/studio/compare", {"id": "t_alpha", "take": 0.5, "bitrate": 64}
        )
        self.assertEqual(code, 200, body)
        self.assertIs(body["ok"], True)
        self.assertEqual(body["reference"], "wav")
        token = str(body["token"])
        self.assertTrue(token.startswith("t_alpha-"), token)
        rows = body["codecs"]
        self.assertEqual([r["codec"] for r in rows], ["wav", "mp3", "opus", "flac"])
        for row in rows:
            self.assertEqual(row["url"], f"/api/compare/{token}/{row['codec']}")
            self.assertGreater(row["bytes"], 0, row)
            self.assertGreaterEqual(row["db"], 0.0, row)
        # The lossless pair scores a flat 0 dB against itself; the lossy
        # ones cost something. That ordering is the whole point of the
        # panel, so it is asserted rather than merely printed.
        by_codec = {str(r["codec"]): r for r in rows}
        self.assertEqual(by_codec["wav"]["db"], 0.0)
        self.assertEqual(by_codec["flac"]["db"], 0.0)
        self.assertGreater(by_codec["mp3"]["db"], 0.0)
        # The encode behind the row is servable, and it is that many bytes.
        code, hdrs, raw = self.req(f"/studio/compare/{token}/mp3")
        self.assertEqual(code, 200)
        self.assertEqual(hdrs.get("content-type"), "audio/mpeg")
        self.assertEqual(len(raw), by_codec["mp3"]["bytes"])

    def test_04_an_unknown_id_is_a_404(self) -> None:
        code, body = self.post("/studio/compare", {"id": "zzz"})
        self.assertEqual((code, body), (404, {"ok": False, "error": "no such track"}))
        code, body = self.json("/studio/compare/nosuchtoken/mp3")
        self.assertEqual((code, body), (404, {"error": "no such comparison"}))

    def test_05_a_typo_in_a_number_is_a_400_not_a_500(self) -> None:
        """Grade report 2026-08-31 A5: these came back as a 500 with a
        traceback, which read to the desk as "the studio is broken"."""
        for bad in (
            {"start": "abc"},
            {"bitrate": "high"},
            {"take": "soon"},
            {"channels": "two"},
        ):
            code, body = self.post("/studio/compare", {"id": "t_alpha", **bad})
            self.assertEqual(code, 400, (bad, body))
            self.assertIs(body["ok"], False, bad)
            self.assertIn("ValueError", str(body["error"]), bad)


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class ServerOps(StudioCase):
    """Its own server, because both tests here end it."""

    def test_01_restart_answers_then_comes_back(self) -> None:
        # Three rounds: a restart races its own dying sockets for the
        # port, and the failure mode is a process that is simply gone
        # (grade report 2026-08-31 A1). Once was a coin flip.
        for round_ in range(3):
            code, body = self.json("/studio/server/restart", "POST")
            self.assertEqual((code, body), (200, {"ok": True, "restarting": True}))
            time.sleep(1.0)
            wait_up(self.port)
            code, body = self.json("/studio/tracks")
            self.assertEqual(code, 200, round_)
            # The same sandbox, re-read: an execv that lost its
            # environment would come back serving the real library.
            self.assertEqual(
                [t["id"] for t in body["tracks"]],
                ["t_alpha", "t_beta", "t_del", "t_empty", "t_meta"],
                round_,
            )

    def test_02_stop_answers_then_the_process_dies(self) -> None:
        code, body = self.json("/studio/server/stop", "POST")
        self.assertEqual((code, body), (200, {"ok": True, "stopping": True}))
        end = time.monotonic() + 15
        while time.monotonic() < end:
            try:
                fetch(self.port, "/api/status")
                time.sleep(0.1)
            except NOT_SERVING:  # a reply torn off by the exit is "stopped" too
                break
        else:
            self.fail(f"server on {self.port} never stopped")
        self.assertIsNotNone(self.procs[0].wait(timeout=10))


class TornReply(unittest.TestCase):
    """The 2026-10-03 #64 scan-job flake, without the race: a server that
    exits mid-reply leaves headers that promise 16 bytes and then EOF, and
    urllib raises http.client.IncompleteRead — no OSError, so a poll that
    caught only those errored instead of reading the server as gone."""

    TORN = b"HTTP/1.1 200 OK\r\nContent-Length: 16\r\n\r\n"
    WHOLE = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"

    def serve(self, *replies: bytes) -> int:
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(len(replies))
        self.addCleanup(srv.close)

        def answer() -> None:
            for reply in replies:
                conn, _ = srv.accept()
                with conn:
                    conn.recv(65536)
                    conn.sendall(reply)

        threading.Thread(target=answer, daemon=True).start()
        return int(srv.getsockname()[1])

    def test_a_torn_reply_is_not_serving_and_wait_up_waits_past_it(self) -> None:
        port = self.serve(self.TORN, self.TORN, self.WHOLE)
        with self.assertRaises(NOT_SERVING) as caught:
            fetch(port, "/api/status")
        self.assertIsInstance(caught.exception, http.client.IncompleteRead)
        wait_up(port, deadline_s=10)  # the second torn reply is retried


if __name__ == "__main__":
    unittest.main()
