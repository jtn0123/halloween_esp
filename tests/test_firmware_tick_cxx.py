"""One command, both castles, through the main loop — the C6 gap.

Every other pair suite asks a QUIESCENT castle a question: the C harness had
no main loop, so `take_pending`, `record_action`, the 200 ms mirror and the
audio clock were never run in C at all, and the whole state machine
downstream of a command was emulator-only. That is the structural reason
C3 and C4 (the emulator's card-derived audio clock) survived a 4000-case
query fuzz with zero differences.

tests/cxx/web_check.cpp now answers TICK, so a test can POST a command,
tick both castles along the SAME timeline and compare what each of them
says it did. The C side is ticked explicitly at a virtual `now_us`, with
the media pipeline's state given per scenario; the emulator's own 200 ms
thread is its main loop, so the test sleeps the spans it ticks.

Its own module because tests/test_firmware_web_cxx.py is at the line cap.
"""

from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

from firmware_web_harness import Reply, WebPairCase

#: Fields of /api/status that are the HOST's, not the castle's: the boot
#: time of two processes started seconds apart, the build stamp, and the
#: real disk under two temporary card directories.
HOST_ARTIFACTS = (
    "compiled",
    "uptime_s",
    "sd_total_kb",
    "sd_free_kb",
    "psram_free_kb",
    "heap_free_kb",
)
#: The C castle's clock. Virtual: the tick's `now_us` is simply what
#: esp_timer would have said, and the test chooses it.
BOOT_US = 1_000_000
TICK_US = 200_000


def num(state: dict[str, object], key: str) -> int:
    """One counter out of /api/status — and a check that it IS a number:
    every field named here is an int in the contract, on both castles."""
    value = state[key]
    assert isinstance(value, int), f"{key} is {value!r}, not a number"
    return value


def kinds(reply: Reply) -> list[tuple[str, str]]:
    """/api/events as (kind, arg) pairs — the `t` stamp is the two
    castles' own uptimes and could never match."""
    return [(e["e"], e["a"]) for e in json.loads(reply.body)]


class TickCase(WebPairCase):
    """A pair whose main loop both halves actually run."""

    def setUp(self) -> None:
        self.now = BOOT_US

    def tick(self, playing: bool = False, sounding: bool = False) -> tuple[str, bytes]:
        """One tick of the C castle, 200 ms on from the last."""
        self.now += TICK_US
        return self.pair.tick(self.now, playing, sounding)

    def status(self, side: str) -> dict[str, object]:
        """/api/status from one castle, with the host's own numbers out."""
        c, e = self.pair.both("GET", b"/api/status")
        body: dict[str, object] = json.loads((c if side == "c" else e).body)
        for key in HOST_ARTIFACTS:
            body.pop(key, None)
        return body


class TestOneCommandThroughBothMainLoops(TickCase):
    """queue → apply → status, on both castles (C6). One test per class
    throughout this file: the event ring is a castle's whole history, and
    two tests sharing a Pair would read each other's lines."""

    def test_a_queued_volume_is_applied_by_the_tick_and_shows_in_status(self) -> None:
        """The POST only queues: /api/status carries the OLD volume until a
        main-loop tick has drained the slot, on both sides. Before TICK the
        C castle could not reach the second half of that sentence."""
        before, _ = self.pair.both("POST", b"/api/volume?v=45")
        self.assertEqual(before.status, 200)
        # The tick BEFORE the slot is drained still publishes the old value:
        # castle_sd_common.yaml mirrors and only then takes the mailbox.
        self.assertEqual(self.status("c")["volume"], 70)
        self.assertEqual(self.tick(), ("VOLUME", b"45"))
        self.assertEqual(self.tick(), ("NONE", b""))
        time.sleep(0.5)  # the emulator's own ticker, twice over
        self.assertEqual(self.status("c")["volume"], 45)
        self.assertEqual(self.status("e")["volume"], 45)
        for reply in self.pair.both("GET", b"/api/events"):
            self.assertEqual(kinds(reply), [("volume", "45")])


class TestLightFramesReconcile(TickCase):
    """A6/C8's counters, now END to END: the eviction is counted in the
    httpd handler, but `applied` is only ever written by the main loop, so
    until TICK no test could add the two up on the C side at all (C6).

    The property is the one a page needs: every frame it sent was either
    applied or evicted, and the two numbers say which. How the three below
    split depends on which tick each POST lands between — that is the
    castle's, not the test's."""

    def test_every_frame_sent_was_either_applied_or_evicted(self) -> None:
        for colour in (b"ff0000", b"00ff00", b"0000ff"):
            self.pair.both("POST", b"/api/light?c=" + colour)
        for _ in range(3):
            self.tick()
        time.sleep(0.7)
        for side in ("c", "e"):
            state = self.status(side)
            self.assertEqual(
                num(state, "light_applied") + num(state, "light_evicted"),
                3,
                f"{side}: {state}",
            )
        # A LIGHT is never a line of its own — far too frequent for a
        # 64-entry ring — so the only events here are the dropped-frame
        # lines, at most one a second.
        for reply in self.pair.both("GET", b"/api/events"):
            self.assertTrue(
                all(e == "light_evicted" for e, _ in kinds(reply)), kinds(reply)
            )


class TestTheClockIsArmedWithoutTheCard(TickCase):
    """C3: `/api/scene` arms the audio clock whether or not the scene's
    track is on the card. `storm` is a scene id both castles are built with
    (harness SCENE_IDS) and neither card has `scenes/storm.mp3`, so this is
    exactly the scene-that-failed-to-sync case the `missing` list exists to
    report — and the case the emulator used to answer `playing:false` to."""

    def test_a_scene_with_no_track_reads_starting_then_ends_once(self) -> None:
        self.pair.both("POST", b"/api/scene?s=storm")
        # The pipeline never comes up: every tick from here is (False, False).
        self.assertEqual(self.tick(), ("SCENE", b"storm"))
        self.tick()  # publishes the armed clock into the snapshot
        time.sleep(0.6)
        for side in ("c", "e"):
            state = self.status(side)
            self.assertTrue(
                state["playing"], f"{side}: an armed clock reports starting"
            )
            self.assertEqual(state["position_ms"], 0, side)
            self.assertEqual(state["scene"], "storm", side)
        # Past kSoundWaitUs (1.5 s) the sound that never came is an end —
        # exactly one, and the castle is idle from then on.
        for _ in range(10):
            self.tick()
        time.sleep(1.9)
        for side in ("c", "e"):
            state = self.status(side)
            self.assertFalse(state["playing"], f"{side}: the grace has lapsed")
            self.assertEqual(state["position_ms"], 0, side)
        for reply in self.pair.both("GET", b"/api/events"):
            # No `sound`: the amplifier was never handed a sample.
            self.assertEqual(kinds(reply), [("scene", "storm"), ("silent", "")])


class TestAFileTheCardDoesNotHave(TickCase):
    """C4: `/api/play` of a name that is not on the card. `safe_name` is the
    only gate, so both castles accept it; the emulator used to give the
    ghost file a one-second duration and fabricate a `sound` event and a
    moving `position_ms` out of it."""

    def test_a_missing_file_never_sounds_on_either_castle(self) -> None:
        self.pair.both("POST", b"/api/play?f=ghost.mp3")
        self.assertEqual(self.tick(), ("PLAY", b"ghost.mp3"))
        for _ in range(4):
            self.tick()
        time.sleep(0.8)
        for side in ("c", "e"):
            state = self.status(side)
            self.assertTrue(state["playing"], f"{side}: armed, and silent")
            self.assertEqual(state["position_ms"], 0, f"{side}: no clock has started")
            self.assertEqual(state["track"], "ghost.mp3", side)
        for _ in range(8):
            self.tick()
        time.sleep(1.7)
        for reply in self.pair.both("GET", b"/api/events"):
            self.assertEqual(kinds(reply), [("play", "ghost.mp3"), ("silent", "")])


class TestAFileTheCardDoesHave(TickCase):
    """The other half of the same timeline: a track that really plays sounds
    on both castles, and the clock counts from the SOUND, not the command."""

    def test_a_present_file_sounds_and_the_clock_runs(self) -> None:
        # wicked_winds.mp3 is 4 KB on both cards: one second of 96 kbps
        # audio to the emulator, and the same second scripted here.
        self.pair.both("POST", b"/api/play?f=wicked_winds.mp3")
        self.assertEqual(self.tick(), ("PLAY", b"wicked_winds.mp3"))
        # The pipeline comes up, the speaker follows half a second later —
        # castle_emu_clock.SPEAKER_START_S, and the reason position_ms is
        # held at 0 until then on the device.
        self.tick(playing=True)
        self.tick(playing=True)
        for _ in range(2):
            self.tick(playing=True, sounding=True)
        time.sleep(0.9)
        for side in ("c", "e"):
            state = self.status(side)
            self.assertTrue(state["playing"], side)
            self.assertGreater(num(state, "position_ms"), 0, f"{side}: clock running")
            self.assertEqual(state["track"], "wicked_winds.mp3", side)
        # The track runs out: one `silent`, and a raw file loses its name.
        for _ in range(6):
            self.tick()
        time.sleep(1.2)
        for side in ("c", "e"):
            state = self.status(side)
            self.assertFalse(state["playing"], side)
            self.assertEqual(
                state["track"], "", f"{side}: a raw file's name is cleared"
            )
        for reply in self.pair.both("GET", b"/api/events"):
            self.assertEqual(
                kinds(reply),
                [("play", "wicked_winds.mp3"), ("sound", ""), ("silent", "")],
            )


class TestALongTrackNameSaysItWasCut(TickCase):
    """A12: `safe_name` allows 99 characters and `kEventArgMax` keeps 47.
    The cut is deliberate — the ring is fixed RAM — but it was invisible in
    the JSON, so /api/events named a song that does not exist. Needs a tick:
    only the main loop writes the ring."""

    def test_the_ring_marks_an_arg_it_could_not_keep_whole(self) -> None:
        name = ("long-" * 19) + ".mp3"  # 99 bytes, and safe_name allows it
        self.assertEqual(len(name), 99)
        self.pair.both("POST", b"/api/play?f=" + name.encode())
        self.assertEqual(self.tick(), ("PLAY", name.encode()))
        time.sleep(0.4)
        for reply in self.pair.both("GET", b"/api/events"):
            event = json.loads(reply.body)[0]
            self.assertEqual(event["a"], name[:47])
            self.assertTrue(event["trunc"], event)
        # And a short arg carries no marker at all: the ordinary line is
        # unchanged, so a page that never looks at it reads as it always did.
        self.pair.both("POST", b"/api/volume?v=45")
        self.tick()
        time.sleep(0.4)
        for reply in self.pair.both("GET", b"/api/events"):
            self.assertNotIn("trunc", json.loads(reply.body)[-1])


if __name__ == "__main__":
    unittest.main()
