"""STOP vs SHOW "0" on the emulator, held to the firmware's own split.

castle_sd_common.yaml: ActionType::STOP runs `scene_stop` and nothing else —
the evening playlist keeps its place and starts the next scene after the gap.
Only SHOW "0" (/api/show/stop) and BLACKOUT call `show_playlist->stop()`.
The emulator used to end the playlist on a plain stop, so every tool verified
against it believed /api/stop ended the night; on the porch the castle came
back by itself (B02/B11).
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import castle_emu


class TestStopLeavesThePlaylistRunning(unittest.TestCase):
    emu: castle_emu.CastleEmu
    base: str

    @classmethod
    def setUpClass(cls) -> None:
        card = Path(tempfile.mkdtemp(prefix="emu-stop-sd-"))
        cls.emu = castle_emu.CastleEmu(port=0, sd_dir=card, scenes=["vigil"])
        cls.emu.start()
        cls.base = f"http://127.0.0.1:{cls.emu.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.emu.shutdown()

    def post(self, path: str) -> int:
        req = urllib.request.Request(self.base + path, data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=3) as r:
            return int(r.status)

    def status(self) -> dict[str, Any]:
        with urllib.request.urlopen(self.base + "/api/status", timeout=3) as r:
            return dict(json.loads(r.read()))

    def wait(self, cond: Callable[[], object], timeout_s: float = 2.0) -> bool:
        """Queued actions apply on the emulator's tick, not on the reply."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if cond():
                return True
            time.sleep(0.05)
        return False

    def test_scene_stop_does_not_end_the_evening_playlist(self) -> None:
        self.post("/api/show/start")
        self.assertTrue(self.wait(lambda: self.status()["show_on"]))
        self.post("/api/scene?s=vigil")
        self.assertTrue(self.wait(lambda: self.status()["scene"] == "vigil"))
        self.post("/api/stop")
        self.assertTrue(self.wait(lambda: self.status()["scene"] == ""))
        self.assertTrue(self.status()["show_on"], "/api/stop must not stop SHOW")

    def test_show_stop_ends_the_playlist(self) -> None:
        self.post("/api/show/start")
        self.assertTrue(self.wait(lambda: self.status()["show_on"]))
        self.post("/api/show/stop")
        self.assertTrue(self.wait(lambda: self.status()["show_on"] is False))

    def test_blackout_is_the_panic_switch_and_takes_both(self) -> None:
        self.post("/api/show/start")
        self.assertTrue(self.wait(lambda: self.status()["show_on"]))
        self.post("/api/blackout")
        self.assertTrue(self.wait(lambda: self.status()["show_on"] is False))
        self.assertEqual(self.status()["scene"], "")


if __name__ == "__main__":
    unittest.main()
