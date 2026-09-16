"""The v5.59 light counters and event ring, firmware against emulator.

Two halves, both about the record the MAIN LOOP keeps of what it did:

  * tests/cxx/events_check.cpp compiles firmware/sd_web_state.h and
    sd_web_events.h with a host compiler and executes the ring — order, the
    wraparound at 64, the one-a-second limit on dropped light frames, and
    the JSON the handler renders. A ring that answered 200 with its newest
    entry dropped would pass every routing test there is.
  * the pair harness (tests/firmware_web_harness.py) asks both castles for
    /api/status and /api/events and compares what comes back.

Its own module because tests/test_firmware_web_cxx.py is at the line cap,
and because this is one feature end to end rather than another route.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

from firmware_web_harness import COMPILER, CXX_DIR, FIRMWARE, IN_CI, WebPairCase


class TestEventsOverTheWire(WebPairCase):
    """Nothing here may queue an action first: the emulator's tick would
    then have written a line the C harness (which has no main loop) cannot."""

    def test_the_light_counters_are_in_both_status_replies(self) -> None:
        """v5.59: what the main loop ran, and what the one slot dropped."""
        c, e = self.pair.both("GET", b"/api/status")
        cj, ej = json.loads(c.body), json.loads(e.body)
        for key in ("light_applied", "light_evicted"):
            self.assertEqual(cj[key], 0, key)  # nothing has been queued here
            self.assertEqual(ej[key], 0, key)
            self.assertIsInstance(cj[key], int)

    def test_an_untouched_castle_has_an_empty_event_ring(self) -> None:
        """/api/events is an ARRAY, oldest first, and empty is "[]" on both
        sides — a page that polls it must never have to special-case null."""
        r = self.same("GET", b"/api/events")
        self.assertEqual((r.status, r.ctype), (200, "application/json"))
        self.assertEqual(json.loads(r.body), [])


class TestEventRing(unittest.TestCase):
    """tests/cxx/events_check.cpp: the ring and the counters of
    sd_web_state.h, executed rather than parsed — order, the wraparound at
    64, the one-a-second rate limit on dropped light frames, and the JSON
    sd_web_events.h renders. A ring that answered 200 with the newest entry
    dropped would pass every other test in this file."""

    @unittest.skipIf(COMPILER is None and not IN_CI, "no host C++ compiler")
    def test_the_ring_and_the_light_counters_behave(self) -> None:
        if COMPILER is None:
            raise AssertionError("CI is set and no host C++ compiler is on PATH")
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "events_check"
            built = subprocess.run(
                [
                    COMPILER,
                    "-std=c++17",
                    "-O1",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(CXX_DIR / "shim"),
                    "-I",
                    str(FIRMWARE),
                    str(CXX_DIR / "events_check.cpp"),
                    "-o",
                    str(exe),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            run = subprocess.run(
                [str(exe)], capture_output=True, text=True, check=False
            )
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn("event ring OK", run.stdout)


if __name__ == "__main__":
    unittest.main()
