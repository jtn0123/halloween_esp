"""The emulator's failure modes, driven through castle_link.

--serial is the real castle's single httpd task: one long PUT parks every
other request, status poll included. --wedge is the pre-v5.22 firmware
defect (every request stalls while a track plays). --no-sd is a pulled
card. Each is what the desk sees on a bad night; these pin the documented
verdicts — 504 "may have landed" for a mutation, None for status — and,
as important, the full recovery afterwards.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import castle_emu
import castle_link as cl

FAST = 0.3


def wait_for(cond, timeout: float = 4.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


class ModeCase(unittest.TestCase):
    def start(self, **kw) -> castle_emu.CastleEmu:
        card = Path(tempfile.mkdtemp(prefix="emu-mode-sd-"))
        (card / "tone.mp3").write_bytes(b"\xff\xfb" + b"\0" * 3000)   # ~1 s track
        emu = castle_emu.CastleEmu(port=0, sd_dir=card, scenes=["vigil", "storm"], **kw)
        emu.start()
        self.addCleanup(emu.server_close)
        self.addCleanup(emu.shutdown)
        env = mock.patch.dict(os.environ, {"CASTLE_HOST": f"127.0.0.1:{emu.port}"})
        env.start()
        self.addCleanup(env.stop)
        patches: list = [mock.patch.object(cl, "TIMEOUT_S", FAST),
                         mock.patch.object(cl, "PROBE_CONNECT_S", FAST),
                         mock.patch.object(cl, "_DOWN_TTL_S", 0.0),
                         mock.patch.dict(cl.READ_BUDGET_S,
                                         {k: FAST for k in cl.READ_BUDGET_S})]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        cl._cache.clear()
        self.addCleanup(cl._cache.clear)
        return emu

    def slow_put(self, emu: castle_emu.CastleEmu, name: str, total: int,
                 seconds: float) -> threading.Thread:
        """A PUT whose body trickles in over `seconds` — WiFi-to-SD speed."""
        def go() -> None:
            s = socket.create_connection(("127.0.0.1", emu.port), timeout=10)
            s.sendall(f"PUT /api/files/{name} HTTP/1.1\r\nHost: c\r\n"
                      f"Content-Length: {total}\r\nConnection: close\r\n\r\n".encode())
            steps = 10
            for _ in range(steps):
                s.sendall(b"x" * (total // steps))
                time.sleep(seconds / steps)
            s.sendall(b"x" * (total - (total // steps) * steps))
            s.shutdown(socket.SHUT_WR)
            while s.recv(4096):
                pass
            s.close()
        t = threading.Thread(target=go, daemon=True)
        t.start()
        time.sleep(0.15)            # let the handler take the serial lock
        return t


class TestSerial(ModeCase):
    def test_a_long_put_parks_everything_then_everything_recovers(self) -> None:
        emu = self.start(serial=True)
        t = self.slow_put(emu, "big.mp3", 20000, 1.6)
        # While the card is busy: status is None, a mutation is 504.
        self.assertIsNone(cl.status())
        code, out, _ = cl.forward("POST", "/api/stop")
        self.assertEqual(code, 504)
        self.assertIn("may have landed", out.decode())
        self.assertEqual(cl.forward("GET", "/api/files")[0], 502)
        t.join(timeout=10)
        # The upload completed untouched by the interruptions...
        self.assertTrue(wait_for(lambda: (emu.sd_dir / "big.mp3").is_file()
                                 and (emu.sd_dir / "big.mp3").stat().st_size == 20000))
        # ...and "may have landed" was literal: the parked stop ran later.
        self.assertTrue(wait_for(lambda: ("STOP", "") in emu.applied))
        cl._cache.clear()
        st = cl.status()
        assert st is not None
        self.assertEqual(st["version"], "5.32")
        self.assertEqual(cl.forward("POST", "/api/volume?v=33")[0], 200)
        self.assertTrue(wait_for(lambda: emu.state.volume == 33))

    def test_serial_mode_is_otherwise_the_same_castle(self) -> None:
        self.start(serial=True)
        self.assertIsNotNone(cl.status())
        self.assertEqual(cl.forward("PUT", "/api/files/x.mp3", b"abc")[0], 200)
        self.assertEqual(cl.forward("DELETE", "/api/files/x.mp3")[0], 200)


class TestWedge(ModeCase):
    def test_requests_stall_while_a_track_plays_then_come_back(self) -> None:
        emu = self.start(wedge=True)
        self.assertEqual(cl.forward("POST", "/api/play?f=tone.mp3")[0], 200)
        self.assertTrue(wait_for(lambda: emu.state.track == "tone.mp3"))
        cl._cache.clear()
        self.assertIsNone(cl.status())
        code, _, _ = cl.forward("POST", "/api/volume?v=50")
        self.assertEqual(code, 504)
        self.assertTrue(wait_for(lambda: emu.state.track == "", 6))
        cl._cache.clear()
        self.assertIsNotNone(cl.status())
        # The parked volume change applied once the task came back (literal
        # "may have landed"), and fresh commands flow again.
        self.assertTrue(wait_for(lambda: emu.state.volume == 50))
        self.assertEqual(cl.forward("POST", "/api/volume?v=60")[0], 200)


class TestNoCard(ModeCase):
    def test_card_routes_say_no_sd_card_and_control_still_works(self) -> None:
        self.start(sd_mounted=False)
        st = cl.status()
        assert st is not None
        self.assertFalse(st["sd_mounted"])
        self.assertEqual((st["sd_total_kb"], st["sd_free_kb"]), (0, 0))
        for method, path, body in (("GET", "/api/files", b""),
                                   ("PUT", "/api/files/a.mp3", b"x"),
                                   ("DELETE", "/api/files/a.mp3", b""),
                                   ("GET", "/sd/a.mp3", b"")):
            code, out, _ = cl.forward(method, path, body)
            self.assertEqual((code, out), (503, b"no SD card"), f"{method} {path}")
        self.assertEqual(cl.forward("GET", "/site/x.js")[0], 404)   # "not on card"
        code, out, _ = cl.forward("POST", "/api/scene?s=vigil")
        self.assertEqual((code, json.loads(out)), (200, {"queued": True}))
        self.assertEqual(cl.forward("POST", "/api/volume?v=5")[0], 200)


if __name__ == "__main__":
    unittest.main()
