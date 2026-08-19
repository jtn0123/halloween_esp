"""The castle bridge: who the castle is, and what the studio relays to it.

The dangerous failure in this area is silent: a suite that resolved the real
porch address from devices.toml would drive live hardware from the tests.
Every test below points CASTLE_HOST somewhere it controls — a fake castle on
a loopback port, or a port nothing listens on. (studio_case.ServerCase pins
the same env for every other studio test, for the same reason.)
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
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

import castle_link as cl  # noqa: E402
from studio_case import ServerCase  # noqa: E402

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


def start_fake_castle() -> tuple[ThreadingHTTPServer, str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeCastle)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"127.0.0.1:{srv.server_address[1]}"


class TestCastleHost(unittest.TestCase):
    """Resolution order: env, then the first devices.toml entry, then None."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        dev = mock.patch.object(cl, "DEVICES", self.tmp / "devices.toml")
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
        with mock.patch.object(cl, "DEVICES", Path("/no/such/devices.toml")):
            code, body, _ = cl.forward("POST", "/api/stop")
        self.assertEqual(code, 502)
        self.assertIn("no castle", body.decode())


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
