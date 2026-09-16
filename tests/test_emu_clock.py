"""The emulator's audio clock, held to firmware 5.55's sound-true rule.

`position_ms` is the speaker's clock, not the mailbox's: the decoder and the
I2S ring take a moment after PLAY, and the board reports 0 for that stretch.
An emulator that counted from the command would prove light frames correct
here and fire them half a second early on the porch (finding B52).
"""

from __future__ import annotations

import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import castle_emu
import castle_emu_clock


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
        self.play(time.monotonic() - castle_emu_clock.SPEAKER_START_S / 2)
        self.assertEqual(self.status()["position_ms"], 0)

    def test_the_clock_counts_from_the_sound_not_the_command(self) -> None:
        self.play(time.monotonic() - (castle_emu_clock.SPEAKER_START_S + 1.0))
        position = self.status()["position_ms"]
        assert isinstance(position, int)
        self.assertGreaterEqual(position, 1000)
        self.assertLess(position, 1100)

    def test_no_track_is_no_clock(self) -> None:
        self.assertEqual(self.status()["position_ms"], 0)

    def test_a_stop_before_the_speaker_still_reads_as_starting(self) -> None:
        """mirror_audio's armed grace: STOP a track the amplifier never
        reached and the board answers playing:true, position_ms:0 until
        kSoundWaitUs is up — not an end the sound never had. An emulator
        that went quiet at once had every follower skip ahead."""
        started = time.monotonic() - castle_emu_clock.SPEAKER_START_S / 2
        self.play(started)
        self.emu._apply("STOP", "")
        status = self.status()
        self.assertEqual(status["track"], "")
        self.assertIs(status["playing"], True)
        self.assertEqual(status["position_ms"], 0)
        self.assertAlmostEqual(
            self.emu.state.starting_until,
            started + castle_emu_clock.SOUND_WAIT_S,
            places=3,
        )

    def test_the_grace_expires_and_the_castle_goes_quiet(self) -> None:
        self.play(time.monotonic() - castle_emu_clock.SOUND_WAIT_S)
        self.emu._apply("STOP", "")
        self.assertIs(self.status()["playing"], False)

    def test_a_stop_after_the_sound_ends_at_once(self) -> None:
        """The clock was no longer armed: the end is the end."""
        self.play(time.monotonic() - (castle_emu_clock.SPEAKER_START_S + 1.0))
        self.emu._apply("STOP", "")
        self.assertIs(self.status()["playing"], False)
        self.assertEqual(self.emu.state.starting_until, 0.0)

    def test_a_scene_keeps_naming_its_track_after_the_audio_ends(self) -> None:
        """castle_sd_common.yaml clears current_track on the tick audio
        ends ONLY when current_scene is "stop" (a raw file). An authored
        scene keeps the name until scene_stop — and reports silence."""
        with self.emu.state.lock:
            self.emu.state.scene = "vigil"
            self.emu.state.track = "vigil.mp3"
            self.emu.state.track_started = time.monotonic() - 10
            self.emu.state.track_ends = time.monotonic() - 1
        status = self.status()
        self.assertEqual(status["track"], "vigil.mp3")
        self.assertIs(status["playing"], False)
        self.assertEqual(status["position_ms"], 0)


class TestTheGraceIsTheFirmwareNumber(unittest.TestCase):
    def test_sound_wait_matches_k_sound_wait_us(self) -> None:
        """castle_emu_clock.SOUND_WAIT_S is sd_web_state.h's kSoundWaitUs;
        a grace that drifted apart would have the emulator report an end the
        board is still calling "starting"."""
        state = (ROOT / "firmware" / "sd_web_state.h").read_text()
        match = re.search(r"kSoundWaitUs = (\d+);", state)
        assert match is not None, "kSoundWaitUs is gone from sd_web_state.h"
        self.assertEqual(castle_emu_clock.SOUND_WAIT_S, int(match.group(1)) / 1e6)


if __name__ == "__main__":
    unittest.main()
