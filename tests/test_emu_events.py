"""The emulated castle's event ring and light counters (v5.59).

/api/status carries two numbers the desk could not get any other way —
LIGHT frames the main loop actually ran, and frames the one-slot mailbox
dropped on the way in — and GET /api/events carries the ring behind them:
what the loop DID, oldest first, which a page polling status once a second
can never reconstruct.

The firmware halves are executed in tests/cxx/events_check.cpp and the JSON
shape is pinned across both castles in tests/test_firmware_contract.py; this
is the emulator's own behaviour, driven over the wire.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import castle_emu
from castle_emu_events import ARG_MAX, RING


class EventCase(unittest.TestCase):
    def setUp(self) -> None:
        self.card = Path(tempfile.mkdtemp(prefix="emu-events-sd-"))
        (self.card / "wicked_winds.mp3").write_bytes(b"\xff\xfb" + b"\0" * 40000)
        self.emu = castle_emu.CastleEmu(
            port=0, sd_dir=self.card, scenes=["vigil", "storm"]
        )
        self.emu.start()
        self.addCleanup(self.emu.server_close)
        self.addCleanup(self.emu.shutdown)
        self.base = f"http://127.0.0.1:{self.emu.port}"

    def http(self, method: str, path: str) -> tuple[int, bytes]:
        req = urllib.request.Request(self.base + path, method=method)
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read()

    def events(self) -> list[dict[str, Any]]:
        code, body = self.http("GET", "/api/events")
        self.assertEqual((code, json.loads(body).__class__), (200, list))
        return list(json.loads(body))

    def status(self) -> dict[str, Any]:
        return dict(json.loads(self.http("GET", "/api/status")[1]))

    def wait_for(self, kind: str, timeout_s: float = 3.0) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            for e in self.events():
                if e["e"] == kind:
                    return e
            time.sleep(0.05)
        raise AssertionError(f"no {kind!r} event in {self.events()}")


class TestTheRingRecordsTheMainLoop(EventCase):
    def test_an_untouched_castle_answers_an_empty_array(self) -> None:
        code, body = self.http("GET", "/api/events")
        self.assertEqual((code, body), (200, b"[]"))

    def test_one_line_per_executed_command_oldest_first(self) -> None:
        self.http("POST", "/api/volume?v=45")
        self.wait_for("volume")
        self.http("POST", "/api/scene?s=storm")
        self.wait_for("scene")
        self.http("POST", "/api/stop")
        self.wait_for("stop")
        kinds = [e["e"] for e in self.events() if e["e"] in ("volume", "scene", "stop")]
        self.assertEqual(kinds, ["volume", "scene", "stop"])
        args = {e["e"]: e["a"] for e in self.events()}
        self.assertEqual(args["volume"], "45")
        self.assertEqual(args["scene"], "storm")
        self.assertEqual(args["stop"], "")

    def test_every_entry_has_the_three_fields_and_a_sane_stamp(self) -> None:
        self.http("POST", "/api/show/start")
        e = self.wait_for("show")
        self.assertEqual(set(e), {"t", "e", "a"})
        self.assertEqual(e["a"], "1")
        self.assertIsInstance(e["t"], int)
        self.assertGreaterEqual(e["t"], 0)
        self.assertLess(e["t"], 60_000)  # uptime ms, not a wall clock
        self.http("POST", "/api/blackout")
        self.assertEqual(self.wait_for("blackout")["a"], "")

    def test_stamps_never_go_backwards(self) -> None:
        for v in ("10", "20", "30"):
            self.http("POST", f"/api/volume?v={v}")
            self.wait_for("volume")
        stamps = [e["t"] for e in self.events()]
        self.assertEqual(stamps, sorted(stamps))

    def test_a_light_is_counted_not_recorded(self) -> None:
        """A colour-picker drag is 4 Hz of frames; the ring has 64 slots."""
        self.http("POST", "/api/light?c=ff0000")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not self.status()["light_applied"]:
            time.sleep(0.05)
        self.assertEqual(self.status()["light_applied"], 1)
        self.assertEqual([e for e in self.events() if e["e"] == "light"], [])

    def test_the_audio_clock_starts_and_ends_the_record(self) -> None:
        self.http("POST", "/api/play?f=wicked_winds.mp3")
        self.assertEqual(self.wait_for("play")["a"], "wicked_winds.mp3")
        self.assertEqual(self.wait_for("sound")["a"], "")
        self.assertEqual(self.wait_for("silent", timeout_s=6)["a"], "")
        order = [e["e"] for e in self.events() if e["e"] in ("play", "sound", "silent")]
        self.assertEqual(order, ["play", "sound", "silent"])


class TestLightCounters(EventCase):
    def test_a_light_over_a_pending_light_is_an_eviction(self) -> None:
        """Faster than the 200 ms drain: the frame underneath never runs,
        and both numbers say so."""
        for c in ("ff0000", "00ff00", "0000ff", "ffffff"):
            self.http("POST", f"/api/light?c={c}")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not self.status()["light_applied"]:
            time.sleep(0.05)
        st = self.status()
        self.assertEqual(st["light_applied"], 1)
        self.assertEqual(st["light_evicted"], 3)

    def test_a_light_dropped_behind_another_command_is_still_an_eviction(
        self,
    ) -> None:
        """v5.60 (A6): the STOP keeps the slot — but the frame that bounced
        off it never ran either, and a page reconciling what it sent against
        applied + evicted has to be told about it."""
        self.emu.queue("STOP", "")
        self.emu.queue("LIGHT", "ff0000")
        self.assertEqual(self.status()["light_evicted"], 1)
        self.assertEqual(self.emu._pending, ("STOP", ""))

    def test_dropped_frames_get_one_ring_line_a_second_at_most(self) -> None:
        for i in range(20):
            self.emu.queue("LIGHT", f"{i:06d}")
        e = self.wait_for("light_evicted")
        self.assertEqual(e["a"], "19")
        for i in range(20):
            self.emu.queue("LIGHT", f"{i:06d}")
        time.sleep(0.5)  # inside the same second: still one line
        self.assertEqual(
            len([x for x in self.events() if x["e"] == "light_evicted"]), 1
        )
        time.sleep(1.0)
        lines = [x for x in self.events() if x["e"] == "light_evicted"]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1]["a"], "19")  # the backlog, not a reset


class TestRingLimits(EventCase):
    def test_the_ring_holds_the_last_sixty_four_and_no_more(self) -> None:
        for i in range(RING + 20):
            self.emu.events.record("volume", str(i), i)
        got = self.events()
        self.assertEqual(len(got), RING)
        self.assertEqual([e["a"] for e in got], [str(i) for i in range(20, RING + 20)])
        self.assertEqual([e["t"] for e in got], list(range(20, RING + 20)))

    def test_a_long_arg_is_truncated_rather_than_growing_the_ring(self) -> None:
        self.emu.events.record("play", "a" * 80, 1)
        self.assertEqual(len(self.events()[-1]["a"]), ARG_MAX)

    def test_a_quote_in_an_arg_keeps_the_array_parseable(self) -> None:
        self.emu.events.record("play", 'a"b\\c.mp3', 1)
        code, body = self.http("GET", "/api/events")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)[-1]["a"], 'a"b\\c.mp3')


if __name__ == "__main__":
    unittest.main()
