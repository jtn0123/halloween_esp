"""The emulated castle's main loop outlives one action it cannot apply.

The ticker ran its tick inside a bare `while True` with a `try/finally` and
no `except`, so the first action that raised — a VOLUME whose argument is
not a number — ended the thread. The castle went on answering /api/status
and applied nothing ever again, which every later test would read as a slow
tick (grade report 2026-09-24 B3).
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


if __name__ == "__main__":
    unittest.main()
