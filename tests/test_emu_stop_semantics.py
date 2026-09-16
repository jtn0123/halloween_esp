"""STOP vs SHOW "0" on the emulator, held to the firmware's own split.

castle_sd_common.yaml: ActionType::STOP runs `scene_stop` and nothing else —
the evening playlist keeps its place and starts the next scene after the gap.
Only SHOW "0" (/api/show/stop) and BLACKOUT call `show_playlist->stop()`.
The emulator used to end the playlist on a plain stop, so every tool verified
against it believed /api/stop ended the night; on the porch the castle came
back by itself (B02/B11).

What a stop LEAVES is pinned here too: scene_stop publishes scene="stop",
not "" (an empty name is only ever a board that has run nothing since boot),
and since v5.58 it hands the strips back — the page light show drives them
through lights_override and they used to hold its last colour in the dark.
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
        self.assertTrue(self.wait(lambda: self.status()["scene"] == "stop"))
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
        self.assertEqual(self.status()["scene"], "stop")

    def test_a_stop_hands_the_strips_back_from_a_light_show(self) -> None:
        """castle_sd_common.yaml's STOP branch runs lights_override("off")
        beside scene_stop: zeroing the zone arrays leaves the manual colour
        painting over them, so a page light show survived the stop."""
        self.post("/api/light?c=FF0000")
        self.assertTrue(self.wait(lambda: self.emu.state.light == "FF0000"))
        self.post("/api/stop")
        self.assertTrue(self.wait(lambda: self.emu.state.light == "off"))

    def test_show_stop_stops_the_scene_and_the_override_too(self) -> None:
        """SHOW "0" is `show_playlist->stop(); scene_stop; lights off`."""
        self.post("/api/scene?s=vigil")
        self.assertTrue(self.wait(lambda: self.status()["scene"] == "vigil"))
        self.post("/api/light?c=00FF00")
        self.assertTrue(self.wait(lambda: self.emu.state.light == "00FF00"))
        self.post("/api/show/stop")
        self.assertTrue(self.wait(lambda: self.status()["scene"] == "stop"))
        self.assertEqual(self.emu.state.light, "off")


class TestTheFirmwareSaysSo(unittest.TestCase):
    """The YAML, parsed — the emulator's stop is only as good as this."""

    COMMON = (
        Path(__file__).resolve().parent.parent / "firmware" / "castle_sd_common.yaml"
    ).read_text()

    def branch(self, action: str) -> str:
        """One arm of the mailbox's if/else chain in the 200 ms interval."""
        head = f"ActionType::{action}) {{"
        self.assertIn(head, self.COMMON)
        return self.COMMON.split(head, 1)[1].split("} else if", 1)[0]

    def test_stop_and_show_zero_hand_the_strips_back_like_blackout(self) -> None:
        hand_back = 'id(lights_override)->execute("off")'
        for action in ("STOP", "SHOW", "BLACKOUT"):
            self.assertIn(
                hand_back,
                self.branch(action),
                f"{action} leaves the page's light show painting in the dark",
            )

    def test_the_emulator_publishes_the_scene_name_the_firmware_does(self) -> None:
        """scene_stop's text_sensor.template.publish, read off the show."""
        scenes = (
            Path(__file__).resolve().parent.parent
            / "firmware"
            / "generated"
            / "scenes.yaml"
        ).read_text()
        stop = scenes.split("- id: scene_stop", 1)[1].split("- id: run_scene", 1)[0]
        published = stop.split("id: current_scene", 1)[1].split("state:", 1)[1]
        self.assertEqual(published.strip().splitlines()[0].strip(), "'stop'")


if __name__ == "__main__":
    unittest.main()
