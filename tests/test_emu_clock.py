"""The emulator's audio clock, held to firmware 5.55's sound-true rule.

`position_ms` is the speaker's clock, not the mailbox's: the decoder and the
I2S ring take a moment after PLAY, and the board reports 0 for that stretch.
An emulator that counted from the command would prove light frames correct
here and fire them half a second early on the porch (finding B52).
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import castle_emu


class TestSoundTrueClock(unittest.TestCase):
    def setUp(self) -> None:
        self.card = Path(tempfile.mkdtemp(prefix="emu-clock-sd-"))
        (self.card / "wicked_winds.mp3").write_bytes(b"\xff\xfb" + b"\0" * 400000)
        self.emu = castle_emu.CastleEmu(port=0, sd_dir=self.card, scenes=["vigil"])

    def tearDown(self) -> None:
        self.emu.server_close()

    def status(self) -> dict[str, Any]:
        return self.emu.status_json()

    def play(self, started: float) -> None:
        with self.emu.state.lock:
            self.emu.state.track = "wicked_winds.mp3"
            self.emu.state.track_started = started
            self.emu.state.track_ends = started + 30

    def test_the_clock_is_zero_until_the_speaker_runs(self) -> None:
        self.play(time.monotonic() - castle_emu.SPEAKER_START_S / 2)
        self.assertEqual(self.status()["position_ms"], 0)

    def test_the_clock_counts_from_the_sound_not_the_command(self) -> None:
        self.play(time.monotonic() - (castle_emu.SPEAKER_START_S + 1.0))
        position = self.status()["position_ms"]
        assert isinstance(position, int)
        self.assertGreaterEqual(position, 1000)
        self.assertLess(position, 1100)

    def test_no_track_is_no_clock(self) -> None:
        self.assertEqual(self.status()["position_ms"], 0)


if __name__ == "__main__":
    unittest.main()
