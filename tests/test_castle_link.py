"""The castle bridge: who the castle is, and what the studio relays to it.

The dangerous failure in this area is silent: a suite that resolved the real
porch address from devices.toml would drive live hardware from the tests.
Every test below points CASTLE_HOST somewhere it controls — a fake castle on
a loopback port, or a port nothing listens on. (studio_case.ServerCase pins
the same env for every other studio test, for the same reason.)
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import castle_link as cl
import hosts
from studio_case import ServerCase

DEAD = "127.0.0.1:1"   # nothing listens; connect refuses instantly


class FakeCastle(BaseHTTPRequestHandler):
    """Answers like firmware/sd_web.h and records every request it sees."""

    seen: ClassVar[list[str]] = []

    def log_message(self, fmt, *a):
        pass

    def _answer(self, obj: dict) -> None:
        FakeCastle.seen.append(f"{self.command} {self.path}")
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/status":
            return self._answer({"version": "9.9", "sd_mounted": True,
                                 "volume": 40, "scene": "vigil"})
        self._answer({"ok": True})

    def do_POST(self):
        self._answer({"queued": True})

    def do_DELETE(self):
        self._answer({"removed": True})


class SlowCastle(FakeCastle):
    """A castle whose card writes take a while: acks a PUT after `put_delay`
    seconds, the way the real one acks only once the last byte is on SD."""

    put_delay: ClassVar[float] = 0.0
    puts: ClassVar[list[int]] = []

    def do_PUT(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        SlowCastle.puts.append(len(body))
        time.sleep(SlowCastle.put_delay)
        # The stalled-PUT tests hang up before the ack; a late write to a
        # closed socket is the point, not a traceback on the test's stderr.
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self._answer({"path": self.path, "bytes": len(body)})


def start_fake_castle(
        handler: type[FakeCastle] = FakeCastle) -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"127.0.0.1:{srv.server_address[1]}"


class TestCastleHost(unittest.TestCase):
    """Resolution order: env, then the first devices.toml entry, then None."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        dev = mock.patch.object(hosts, "DEVICES", self.tmp / "devices.toml")
        dev.start()
        self.addCleanup(dev.stop)
        os.environ.pop("CASTLE_HOST", None)

    def test_env_wins_over_the_file(self) -> None:
        (self.tmp / "devices.toml").write_text('[a]\nhost = "1.2.3.4"\n')
        os.environ["CASTLE_HOST"] = "9.9.9.9"
        self.assertEqual(cl.castle_host(), "9.9.9.9")

    def test_first_toml_entry_with_a_host(self) -> None:
        (self.tmp / "devices.toml").write_text(
            '[noise]\nnote = "no host key"\n[castle]\nhost = "1.2.3.4"\n')
        self.assertEqual(cl.castle_host(), "1.2.3.4")

    def test_no_file_means_no_castle(self) -> None:
        self.assertIsNone(cl.castle_host())

    def test_hosts_is_the_shared_candidate_list_with_fallbacks(self) -> None:
        """One resolver: what castle_link walks IS hosts.candidates()."""
        (self.tmp / "devices.toml").write_text(
            '[castle]\nhost = "1.2.3.4"\nfallbacks = ["1.2.3.5"]\n')
        self.assertEqual(cl.castle_hosts(), ["1.2.3.4", "1.2.3.5"])
        self.assertEqual(cl.castle_hosts(), hosts.candidates())
        os.environ["CASTLE_HOST"] = "castle,9.9.9.9"
        self.assertEqual(cl.castle_hosts(), ["1.2.3.4", "1.2.3.5", "9.9.9.9"])
        os.environ["CASTLE_HOST"] = ""
        self.assertEqual(cl.castle_hosts(), [])

    def test_malformed_toml_means_no_castle_not_a_traceback(self) -> None:
        (self.tmp / "devices.toml").write_text("host = = =\n")
        self.assertIsNone(cl.castle_host())


class TestStatusAndForward(unittest.TestCase):
    """status() and forward() against a castle the test owns."""

    def setUp(self) -> None:
        self.castle, host = start_fake_castle()
        self.addCleanup(self.castle.shutdown)
        self.addCleanup(self.castle.server_close)
        p = mock.patch.dict(os.environ, {"CASTLE_HOST": host})
        p.start()
        self.addCleanup(p.stop)
        cl._cache.clear()
        FakeCastle.seen = []

    def test_status_relays_and_marks_the_answer(self) -> None:
        s = cl.status()
        assert s is not None
        self.assertEqual(s["version"], "9.9")
        self.assertIn("bridged", s)

    def test_status_is_cached_between_polls(self) -> None:
        cl.status()
        cl.status()
        self.assertEqual(FakeCastle.seen.count("GET /api/status"), 1)

    def test_dead_castle_is_none_and_remembered(self) -> None:
        os.environ["CASTLE_HOST"] = DEAD
        cl._cache.clear()
        self.assertIsNone(cl.status())
        # The second miss must come from the down-cache, not another connect.
        self.assertIn("down", cl._cache)

    def test_forward_preserves_path_query_and_method(self) -> None:
        code, body, _ = cl.forward("POST", "/api/scene?s=vigil")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"queued": True})
        self.assertEqual(FakeCastle.seen, ["POST /api/scene?s=vigil"])

    def test_forward_without_a_castle_is_a_502(self) -> None:
        os.environ.pop("CASTLE_HOST")
        with mock.patch.object(hosts, "DEVICES", Path("/no/such/devices.toml")):
            code, body, _ = cl.forward("POST", "/api/stop")
        self.assertEqual(code, 502)
        self.assertIn("no castle", body.decode())

    def test_a_configured_but_dead_castle_502s_every_verb(self) -> None:
        """Pass-1 J1-1: Stop answered 200 "queued" to a castle that was not
        there, because the native stub's empty key dict made it vacuously
        true. Nothing is reachable here; nothing may claim to be queued."""
        os.environ["CASTLE_HOST"] = DEAD
        cl._cache.clear()
        for method, path, body in (("POST", "/api/stop", b""),
                                   ("POST", "/api/scene?s=vigil", b""),
                                   ("POST", "/api/volume?v=5", b""),
                                   ("POST", "/api/play?f=x.mp3", b""),
                                   ("PUT", "/api/files/x.mp3", b"\xff\xfb"),
                                   ("DELETE", "/api/files/x.mp3", b""),
                                   ("GET", "/api/files", b"")):
            code, out, _ = cl.forward(method, path, body)
            self.assertEqual(code, 502, f"{method} {path} -> {out!r}")
            self.assertIn("not reachable", out.decode())

    def test_a_mutation_drops_the_cached_status(self) -> None:
        """Pass-1 J1-5: the desk re-polls ~1 s after a click, inside the
        status cache's 1.5 s — it must get the post-click world."""
        cl.status()
        cl.forward("POST", "/api/volume?v=5")
        cl.status()
        self.assertEqual(FakeCastle.seen.count("GET /api/status"), 2)

    def test_an_answered_request_clears_the_down_cache(self) -> None:
        """J2-4: a probe that failed while the castle rebooted must not keep
        saying "down" for 3 s after the castle plainly served a click —
        the desk's own 0.9 s re-poll would flip it to 'not answering'."""
        cl._cache["down"] = (time.monotonic(), {})
        code, _, _ = cl.forward("POST", "/api/scene?s=vigil")
        self.assertEqual(code, 200)
        self.assertNotIn("down", cl._cache)
        self.assertIsNotNone(cl.status())

    def test_native_leg_is_skipped_for_hosts_with_a_port(self) -> None:
        """J2-8: "host:port" names an HTTP server; aioesphomeapi cannot
        resolve it and would log "Error resolving" every 5 s forever."""
        self.assertFalse(cl.native_host("127.0.0.1:8093"))
        self.assertTrue(cl.native_host("castle.lan"))
        with mock.patch.object(cl.castle_native, "connected") as conn:
            os.environ["CASTLE_HOST"] = DEAD
            cl._cache.clear()
            self.assertEqual(cl.forward("POST", "/api/stop")[0], 502)
        conn.assert_not_called()

    def test_a_plain_get_keeps_the_cached_status(self) -> None:
        cl.status()
        cl.forward("GET", "/api/files")
        cl.status()
        self.assertEqual(FakeCastle.seen.count("GET /api/status"), 1)


class TestSlowCard(unittest.TestCase):
    """Pass-1 J1-8: PUT budgets and no replay once the bytes have gone."""

    def setUp(self) -> None:
        self.castle, self.host = start_fake_castle(SlowCastle)
        self.addCleanup(self.castle.shutdown)
        self.addCleanup(self.castle.server_close)
        SlowCastle.puts = []
        SlowCastle.put_delay = 0.0
        FakeCastle.seen = []
        cl._cache.clear()

    def test_a_put_that_acks_after_the_post_budget_still_succeeds(self) -> None:
        SlowCastle.put_delay = cl.TIMEOUT_S + 0.5
        with mock.patch.dict(os.environ, {"CASTLE_HOST": self.host}):
            code, out, _ = cl.forward("PUT", "/api/files/big.mp3", b"x" * 1000)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(out)["bytes"], 1000)

    def test_a_stalled_put_is_never_replayed_to_the_fallback(self) -> None:
        """The bytes left once; a 504 says "maybe landed", and the fallback
        address (the same castle, re-leased) must NOT receive a second copy."""
        other, other_host = start_fake_castle()
        self.addCleanup(other.shutdown)
        self.addCleanup(other.server_close)
        SlowCastle.put_delay = 1.5
        with mock.patch.dict(os.environ, {"CASTLE_HOST": f"{self.host},{other_host}"}), \
                mock.patch.dict(cl.READ_BUDGET_S, {"PUT": 0.3}):
            code, out, _ = cl.forward("PUT", "/api/files/big.mp3", b"x" * 10)
        self.assertEqual(code, 504)
        self.assertIn("may have landed", out.decode())
        self.assertEqual(SlowCastle.puts, [10])
        self.assertEqual([c for c in FakeCastle.seen if c.startswith("PUT")], [])

    def test_a_get_that_stalls_tries_the_next_host(self) -> None:
        other, other_host = start_fake_castle()
        self.addCleanup(other.shutdown)
        self.addCleanup(other.server_close)
        with mock.patch.dict(os.environ, {"CASTLE_HOST": f"127.0.0.1:1,{other_host}"}):
            code, _, _ = cl.forward("GET", "/api/files")
        self.assertEqual(code, 200)


class TestStudioBridge(ServerCase):
    """The studio's routes, with a fake castle behind them."""

    castle: ThreadingHTTPServer
    castle_host: str

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.castle, cls.castle_host = start_fake_castle()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.castle.shutdown()
        cls.castle.server_close()
        super().tearDownClass()

    def setUp(self) -> None:
        os.environ["CASTLE_HOST"] = self.castle_host
        cl._cache.clear()
        FakeCastle.seen = []

    def tearDown(self) -> None:
        os.environ["CASTLE_HOST"] = DEAD
        cl._cache.clear()

    def get(self, path: str) -> tuple[int, dict]:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}{path}") as r:
            return r.status, json.loads(r.read())

    def post(self, path: str, body: bytes = b"") -> tuple[int, dict]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=body, method="POST")
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())

    def test_status_is_the_castles_own_when_it_answers(self) -> None:
        _, s = self.get("/api/status")
        self.assertEqual(s.get("version"), "9.9")
        self.assertNotIn("studio", s)   # this is what flips the desk's mode

    def test_status_falls_back_to_the_studio_marker(self) -> None:
        os.environ["CASTLE_HOST"] = DEAD
        cl._cache.clear()
        _, s = self.get("/api/status")
        self.assertEqual(s, {"studio": True})

    def test_scene_with_a_query_fires_on_the_castle(self) -> None:
        code, body = self.post("/api/scene?s=vigil")
        self.assertEqual(code, 200)
        self.assertEqual(body, {"queued": True})
        self.assertIn("POST /api/scene?s=vigil", FakeCastle.seen)

    def test_scene_with_a_json_body_stays_the_studios_own(self) -> None:
        # The studio's scenes.yaml editor and the castle's fire-a-scene
        # share a path; the JSON body must never end up on the hardware.
        try:
            self.post("/api/scene", json.dumps({"id": ""}).encode())
        except urllib.error.HTTPError as e:
            e.close()   # the editor rejecting the stub is fine — and local
        self.assertEqual(FakeCastle.seen, [])

    def test_unclaimed_api_gets_relay(self) -> None:
        _, body = self.get("/api/pir?armed=1")
        self.assertEqual(body, {"ok": True})
        self.assertIn("GET /api/pir?armed=1", FakeCastle.seen)

    def test_studios_own_routes_are_not_relayed(self) -> None:
        self.get("/api/tracks")
        self.assertEqual(FakeCastle.seen, [])


if __name__ == "__main__":
    unittest.main()
