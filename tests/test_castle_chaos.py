"""castle_link under a misbehaving castle: scripted failures, invariants.

A fake castle follows a script — refuse, accept-then-stall, slow-ack, 404,
500, close-without-reply, flap back up — and the bridge's promises are
checked across the whole sequence rather than one failure at a time:

  * a castle that is not answering NEVER yields a 2xx, for any verb
  * a PUT whose body left the socket is NEVER replayed — not to the next
    host, not to the native leg
  * the status / down / up caches move only on the documented events
  * every call returns inside its documented budget (measured)
  * CASTLE_HOST "" is "no castle", and a live host listed last still wins

Budgets are patched small (TIMEOUT_S 0.1 s) so the suite stays quick; the
arithmetic asserted is the same the real numbers obey.
"""

from __future__ import annotations

import collections
import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import castle_link as cl

DEAD = "127.0.0.1:1"
FAST = 0.1  # the patched TIMEOUT_S / read budgets
SLACK = 0.6  # thread scheduling + connect on loopback (6x the budget)


class Scripted(BaseHTTPRequestHandler):
    """Pops one behaviour per request from the server's script; the last
    entry repeats. Records every (method, path, body-length) it consumed."""

    server: ScriptedCastle

    def log_message(self, fmt, *a):
        pass

    def _go(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        self.server.seen.append((self.command, self.path, len(body)))
        act = self.server.next()
        if act == "stall":
            self.server.release.wait(5)  # longer than any patched budget
            act = "ok"  # ...then answer, late
        if act == "close":
            self.close_connection = True
            return
        if act.startswith("slow"):
            time.sleep(float(act.split(":")[1]))
            act = "ok"
        if act == "ok":
            payload = (
                json.dumps({"version": "9.9", "volume": 40, "sd_mounted": True})
                if self.path == "/api/status"
                else '{"queued":true}'
            ).encode()
            self.send_response(200)
        else:
            payload, code = b"scripted " + act.encode(), int(act)
            self.send_response(code)
        self.send_header(
            "Content-Type", "application/json" if act == "ok" else "text/plain"
        )
        self.send_header("Content-Length", str(len(payload)))
        try:
            self.end_headers()
            self.wfile.write(payload)
        except OSError:
            pass  # the bridge gave up waiting; a late answer to nobody

    def handle(self) -> None:
        try:
            super().handle()
        except OSError:
            pass

    do_GET = do_POST = do_PUT = do_DELETE = _go


class ScriptedCastle(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int = 0) -> None:
        super().__init__(("127.0.0.1", port), Scripted)
        self.script: collections.deque[str] = collections.deque(["ok"])
        self.seen: list[tuple[str, str, int]] = []
        self.release = threading.Event()
        self.lock = threading.Lock()
        # poll_interval: shutdown() waits for the serve loop to notice, and
        # the default 0.5 s was most of the suite's wall time — every test
        # stops one or two of these.
        threading.Thread(
            target=self.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        ).start()

    @property
    def host(self) -> str:
        return f"127.0.0.1:{self.server_address[1]}"

    def next(self) -> str:
        with self.lock:
            return self.script.popleft() if len(self.script) > 1 else self.script[0]

    def program(self, *acts: str) -> None:
        with self.lock:
            self.script = collections.deque(acts)
        self.seen.clear()

    def stop(self) -> None:
        self.release.set()
        self.shutdown()
        self.server_close()


VERBS = (
    ("POST", "/api/stop", b""),
    ("POST", "/api/scene?s=vigil", b""),
    ("POST", "/api/volume?v=5", b""),
    ("POST", "/api/play?f=x.mp3", b""),
    ("PUT", "/api/files/x.mp3", b"\xff\xfbdata"),
    ("DELETE", "/api/files/x.mp3", b""),
    ("GET", "/api/files", b""),
    ("GET", "/sd/x.mp3", b""),
)


class ChaosCase(unittest.TestCase):
    def setUp(self) -> None:
        self.castle = ScriptedCastle()
        self.addCleanup(self.castle.stop)
        self.native = mock.patch.object(
            cl.castle_native, "connected", return_value=False
        )
        self.native_connected = self.native.start()
        self.addCleanup(self.native.stop)
        patches: list = [
            mock.patch.object(cl, "TIMEOUT_S", FAST),
            mock.patch.object(cl, "PROBE_CONNECT_S", FAST),
            mock.patch.dict(cl.READ_BUDGET_S, {k: FAST for k in cl.READ_BUDGET_S}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.env = mock.patch.dict(os.environ, {"CASTLE_HOST": self.castle.host})
        self.env.start()
        self.addCleanup(self.env.stop)
        cl._cache.clear()
        self.addCleanup(cl._cache.clear)

    def hosts(self, *hs: str) -> None:
        os.environ["CASTLE_HOST"] = ",".join(hs)
        cl._cache.clear()


class TestNeverALie(ChaosCase):
    """Across refuse / stall / 500 / close-without-reply: no verb gets a 2xx."""

    def assert_no_2xx(self, label: str) -> None:
        for method, path, body in VERBS:
            code, out, _ = cl.forward(method, path, body)
            self.assertFalse(
                200 <= code < 300, f"{label}: {method} {path} → {code} {out!r}"
            )
        self.assertIsNone(cl.status(), label)
        cl._cache.clear()

    def test_refused_stalled_erroring_and_hanging_up(self) -> None:
        self.hosts(DEAD)
        self.assert_no_2xx("refused")
        self.hosts(self.castle.host)
        self.castle.program("stall")
        self.assert_no_2xx("stall")
        self.castle.release.set()
        self.castle.program("500")
        self.assert_no_2xx("500")
        self.castle.program("close")
        self.assert_no_2xx("close-without-reply")
        self.native_connected.assert_not_called()  # ported host: no native leg

    def test_stall_verdicts_by_verb(self) -> None:
        """GET → 502 (safe to retry, nothing changed); anything mutating →
        504 "may have landed" — and NEVER 200."""
        self.castle.program("stall")
        self.assertEqual(cl.forward("GET", "/api/files")[0], 502)
        for method, path, body in VERBS[:6]:
            code, out, _ = cl.forward(method, path, body)
            self.assertEqual(code, 504, f"{method} {path}")
            self.assertIn("may have landed", out.decode())


class TestNoReplay(ChaosCase):
    def test_stalled_put_is_not_replayed_to_the_fallback(self) -> None:
        other = ScriptedCastle()
        self.addCleanup(other.stop)
        self.hosts(self.castle.host, other.host)
        self.castle.program("stall")
        code, _, _ = cl.forward("PUT", "/api/files/song.mp3", b"x" * 5000)
        self.assertEqual(code, 504)
        self.assertEqual(
            [s for s in self.castle.seen if s[0] == "PUT"],
            [("PUT", "/api/files/song.mp3", 5000)],
        )
        self.assertEqual(other.seen, [])
        self.native_connected.assert_not_called()

    def test_hung_up_put_is_not_replayed_either(self) -> None:
        """Body consumed, then the socket closed with no reply: it may well
        be on the card. 504, one copy sent."""
        other = ScriptedCastle()
        self.addCleanup(other.stop)
        self.hosts(self.castle.host, other.host)
        self.castle.program("close")
        code, _, _ = cl.forward("PUT", "/api/files/song.mp3", b"x" * 10)
        self.assertEqual(code, 504)
        self.assertEqual(len(self.castle.seen), 1)
        self.assertEqual(other.seen, [])

    def test_refused_primary_sends_the_body_once_to_the_fallback(self) -> None:
        """Nothing left for a refused connect, so trying the next host is
        safe — and the bytes still travel exactly once overall."""
        self.hosts(DEAD, self.castle.host)
        code, _, _ = cl.forward("PUT", "/api/files/song.mp3", b"x" * 10)
        self.assertEqual(code, 200)
        self.assertEqual(
            [s for s in self.castle.seen if s[0] == "PUT"],
            [("PUT", "/api/files/song.mp3", 10)],
        )

    def test_slow_ack_inside_the_budget_is_a_plain_200(self) -> None:
        # A wider budget for this one test: the ack has to land INSIDE it,
        # and 0.15 s against FAST would be a coin-toss on a busy machine.
        with (
            mock.patch.object(cl, "TIMEOUT_S", 0.5),
            mock.patch.dict(cl.READ_BUDGET_S, {"PUT": 0.5}),
        ):
            self.castle.program("slow:0.15")
            self.assertEqual(cl.forward("PUT", "/api/files/song.mp3", b"x")[0], 200)

    def test_a_stalled_post_never_reaches_the_native_leg(self) -> None:
        """Even for a native-capable (port-less) host: a stall means the
        HTTP leg took the request; translating it again would double it."""
        self.native_connected.return_value = True
        with (
            mock.patch.object(cl, "_call", side_effect=cl.Stalled("stalled")),
            mock.patch.object(cl.castle_native, "stop") as stop,
        ):
            os.environ["CASTLE_HOST"] = "castle.lan"
            cl._cache.clear()
            code, _, _ = cl.forward("POST", "/api/stop")
        self.assertEqual(code, 504)
        stop.assert_not_called()


class TestCaches(ChaosCase):
    def test_down_is_remembered_then_cleared_by_any_answer(self) -> None:
        self.hosts(DEAD, self.castle.host)
        self.castle.program("close")  # both hosts unusable for status
        self.assertIsNone(cl.status())
        self.assertIn("down", cl._cache)
        self.castle.program("404")
        code, _, _ = cl.forward("POST", "/api/scene?s=nope")
        self.assertEqual(code, 404)
        self.assertNotIn("down", cl._cache)  # an answer of any kind is life
        self.assertNotIn("up", cl._cache)  # ...but only a 2xx promotes

    def test_within_down_ttl_no_socket_is_touched(self) -> None:
        self.hosts(self.castle.host)
        self.castle.program("close")
        self.assertIsNone(cl.status())
        before = len(self.castle.seen)
        self.assertIsNone(cl.status())
        self.assertEqual(len(self.castle.seen), before)

    def test_mutation_drops_status_a_get_keeps_it(self) -> None:
        self.assertIsNotNone(cl.status())
        cl.forward("GET", "/api/files")
        self.assertIn("status", cl._cache)
        cl.forward("POST", "/api/stop")
        self.assertNotIn("status", cl._cache)

    def test_up_host_is_promoted_and_demoted_by_reality(self) -> None:
        other = ScriptedCastle()
        self.addCleanup(other.stop)
        self.hosts(self.castle.host, other.host)
        self.castle.program("close")  # primary sick, fallback fine
        st = cl.status()
        assert st is not None
        self.assertEqual(st["bridged"], other.host)
        self.assertEqual(cl.castle_hosts()[0], other.host)
        # The primary recovers, the fallback dies: the order follows.
        cl._cache.clear()
        self.castle.program("ok")
        other.program("close")
        st = cl.status()
        assert st is not None
        self.assertEqual(st["bridged"], self.castle.host)
        self.assertEqual(cl.castle_hosts(), [self.castle.host, other.host])

    def test_a_host_that_only_errors_is_never_remembered_as_up(self) -> None:
        other = ScriptedCastle()
        self.addCleanup(other.stop)
        self.hosts(self.castle.host, other.host)
        self.castle.program("500")
        for _ in range(3):
            cl.forward("POST", "/api/stop")
        self.assertNotIn("up", cl._cache)
        self.assertEqual(cl.castle_hosts()[0], self.castle.host)


class TestBudgets(ChaosCase):
    """Wall time per call, against the documented arithmetic."""

    def timed(self, fn):
        t0 = time.monotonic()
        out = fn()
        return out, time.monotonic() - t0

    def test_status_against_a_stalled_castle(self) -> None:
        self.castle.program("stall")
        _, took = self.timed(cl.status)
        # one host: connect (instant) + read FAST; then no native for a ported host
        self.assertLess(took, cl.PROBE_CONNECT_S + cl.TIMEOUT_S + SLACK)

    def test_status_walks_every_host_then_stops(self) -> None:
        other = ScriptedCastle()
        self.addCleanup(other.stop)
        self.hosts(self.castle.host, other.host, DEAD)
        self.castle.program("stall")
        other.program("stall")
        _, took = self.timed(cl.status)
        self.assertLess(took, 2 * (cl.PROBE_CONNECT_S + cl.TIMEOUT_S) + SLACK)

    def test_post_and_put_against_a_stall_return_at_the_first_host(self) -> None:
        other = ScriptedCastle()
        self.addCleanup(other.stop)
        self.hosts(self.castle.host, other.host)
        self.castle.program("stall")
        (code, _, _), took = self.timed(lambda: cl.forward("POST", "/api/stop"))
        self.assertEqual(code, 504)
        self.assertLess(
            took, cl.TIMEOUT_S + cl._read_budget("POST", "/api/stop") + SLACK
        )
        (code, _, _), took = self.timed(
            lambda: cl.forward("PUT", "/api/files/a.mp3", b"x")
        )
        self.assertEqual(code, 504)
        self.assertLess(took, cl.TIMEOUT_S + cl.READ_BUDGET_S["PUT"] + SLACK)
        self.assertEqual(other.seen, [])

    def test_a_get_walks_on_after_a_stall(self) -> None:
        other = ScriptedCastle()
        self.addCleanup(other.stop)
        self.hosts(self.castle.host, other.host)
        self.castle.program("stall")
        (code, _, _), took = self.timed(lambda: cl.forward("GET", "/api/files"))
        self.assertEqual(code, 200)
        self.assertLess(took, cl.TIMEOUT_S + cl.READ_BUDGET_S["FILES_GET"] + SLACK)

    def test_refused_hosts_cost_almost_nothing(self) -> None:
        self.hosts(DEAD, DEAD, DEAD, self.castle.host)
        (st, took) = self.timed(cl.status)
        self.assertIsNotNone(st)
        self.assertLess(took, SLACK)


class TestHostLists(ChaosCase):
    def test_empty_castle_host_means_no_castle_and_no_sockets(self) -> None:
        self.hosts("")
        self.assertIsNone(cl.status())
        code, out, _ = cl.forward("POST", "/api/stop")
        self.assertEqual(code, 502)
        self.assertIn("no castle configured", out.decode())
        self.assertEqual(self.castle.seen, [])
        self.native_connected.assert_not_called()
        self.assertNotIn("down", cl._cache)  # nothing to be down

    def test_live_host_listed_last_still_serves_and_is_promoted(self) -> None:
        dead2 = ScriptedCastle()
        self.addCleanup(dead2.stop)
        dead2.program("close")
        self.hosts(DEAD, dead2.host, self.castle.host)
        st = cl.status()
        assert st is not None
        self.assertEqual(st["bridged"], self.castle.host)
        self.assertEqual(cl.forward("POST", "/api/volume?v=9")[0], 200)
        self.assertEqual(cl.castle_hosts()[0], self.castle.host)

    def test_flapping_castle_is_tracked_phase_by_phase(self) -> None:
        seq = []
        for phase in ("ok", "500", "close", "ok"):
            self.castle.program(phase)
            cl._cache.clear()
            seq.append(
                (phase, cl.status() is not None, cl.forward("POST", "/api/stop")[0])
            )
        self.assertEqual(
            seq,
            [
                ("ok", True, 200),
                ("500", False, 500),
                ("close", False, 504),
                ("ok", True, 200),
            ],
        )

    def test_restart_on_the_same_port_is_picked_up(self) -> None:
        port = self.castle.server_address[1]
        self.assertIsNotNone(cl.status())
        self.castle.stop()
        cl._cache.clear()
        self.assertIsNone(cl.status())
        reborn = ScriptedCastle(port)
        self.addCleanup(reborn.stop)
        cl._cache.clear()
        self.assertIsNotNone(cl.status())


if __name__ == "__main__":
    unittest.main()
