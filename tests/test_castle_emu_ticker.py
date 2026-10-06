"""The emulated castle's main loop outlives one action it cannot apply.

The ticker ran its tick inside a bare `while True` with a `try/finally` and
no `except`, so the first action that raised — a VOLUME whose argument is
not a number — ended the thread. The castle went on answering /api/status
and applied nothing ever again, which every later test would read as a slow
tick (grade report 2026-09-24 B3).

And it stops with its castle. It used to run for the life of the process: a
suite that built a castle per test left a thread per test calling
time.sleep(0.2) for ever, and a later test that patched `time.sleep` to count
its own one pause counted 1,893 of theirs too (Castle Radio's
test_device_bridge, Windows CI on 2026-10-06).
"""

from __future__ import annotations

import contextlib
import io
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import castle_emu


class TestTheTickerSurvives(unittest.TestCase):
    def queue(self, emu: castle_emu.CastleEmu, action: tuple[str, str]) -> None:
        with emu.state.lock:
            emu._pending = action

    def wait_applied(self, emu: castle_emu.CastleEmu, action: tuple[str, str]) -> None:
        deadline = time.monotonic() + 5
        while action not in emu.applied:
            self.assertLess(time.monotonic(), deadline, f"{action} never applied")
            time.sleep(0.02)

    def test_a_bad_action_is_reported_and_the_next_one_still_lands(self) -> None:
        emu = castle_emu.CastleEmu(port=0)
        emu.start()
        self.addCleanup(emu.server_close)
        self.addCleanup(emu.shutdown)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.queue(emu, ("VOLUME", "loud"))
            self.wait_applied(emu, ("VOLUME", "loud"))
            self.queue(emu, ("VOLUME", "40"))
            self.wait_applied(emu, ("VOLUME", "40"))
        with emu.state.lock:
            self.assertEqual(emu.state.volume, 40)
        self.assertIn("ValueError", err.getvalue())


class TestTheTickerStopsWithItsCastle(unittest.TestCase):
    def assert_stops(self, emu: castle_emu.CastleEmu, how: str) -> None:
        emu.ticker.join(timeout=5)
        self.assertFalse(emu.ticker.is_alive(), f"{how} left the ticker running")

    def test_closing_a_castle_that_never_served_stops_its_ticker(self) -> None:
        emu = castle_emu.CastleEmu(port=0)
        self.assertTrue(emu.ticker.is_alive())
        emu.server_close()
        self.assert_stops(emu, "server_close()")

    def test_an_unplugged_castle_ticks_on_until_it_is_closed(self) -> None:
        # shutdown() is the network going (the soak's unplug); the board's
        # loop outlives that, and only the power switch ends it.
        emu = castle_emu.CastleEmu(port=0)
        emu.start()
        emu.shutdown()
        self.assertTrue(emu.ticker.is_alive(), "shutdown() alone stopped the loop")
        emu.server_close()
        self.assert_stops(emu, "server_close() after shutdown()")


if __name__ == "__main__":
    unittest.main()
