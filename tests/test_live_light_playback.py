"""Raw imported audio must survive streamed light overrides and report its clock.

The mailbox half of that contract is EXECUTED, not grepped: tests/cxx/
light_mailbox_check.cpp compiles firmware/sd_web_state.h with the host
compiler and steps the pending-action slot the way the 200 ms interval does.
A substring assertion cannot tell a stop that survived a frame stream from
one the frame stream swallowed, which is the whole defect.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "firmware" / "castle_sd_common.yaml").read_text()
#: The 200 ms bridge is two files since v5.70: the tick that mirrors state
#: out lives in castle_sd_common.yaml, and the mailbox's if/else chain is
#: the `web_action` script it runs inline (castle_web_actions.yaml). The
#: branches below are read out of the second; the mirror out of the first.
ACTIONS = (ROOT / "firmware" / "castle_web_actions.yaml").read_text()
COMPILER = shutil.which("clang++") or shutil.which("g++")
FLAGS = [
    "-std=c++17",
    "-O1",
    "-Wall",
    "-Wextra",
    "-Werror",
    # The shim path goes FIRST, as it does in tests/firmware_web_harness.py:
    # sd_web_state.h reaches castle_rtc.h, which asks for <esp_attr.h> (the
    # RTC_NOINIT section that makes the ring outlive a panic, v5.62 L1).
    "-I",
    str(ROOT / "tests" / "cxx" / "shim"),
    "-I",
    str(ROOT / "firmware"),
]
IN_CI = bool(os.environ.get("CI"))

sys.path.insert(0, str(ROOT / "tools"))
import castle_emu


def load_device_bridge():
    """demo/castle-radio is the control room's own tree, not a package on
    this suite's path — load the module from its own directory rather than
    move it. The directory goes on the path because the bridge imports its
    light-show runner (light_show) from beside itself, and the module is
    registered under its own name so that runner reaches this same object
    when it looks `call` up at send time."""
    room = ROOT / "demo" / "castle-radio"
    if str(room) not in sys.path:
        sys.path.insert(0, str(room))
    spec = importlib.util.spec_from_file_location(
        "device_bridge", room / "device_bridge.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["device_bridge"] = module
    spec.loader.exec_module(module)
    return module


device_bridge = load_device_bridge()
light_show = sys.modules["light_show"]


def branch(start, end):
    return ACTIONS.split(start, 1)[1].split(end, 1)[0]


def run_cxx(source: Path) -> str:
    if COMPILER is None:
        raise AssertionError("CI is set and no host C++ compiler is on PATH")
    with tempfile.TemporaryDirectory() as tmp:
        exe = Path(tmp) / source.stem
        built = subprocess.run(
            [COMPILER, *FLAGS, str(source), "-o", str(exe)],
            capture_output=True,
            text=True,
            check=False,
        )
        if built.returncode != 0:
            raise AssertionError(built.stderr)
        run = subprocess.run([str(exe)], capture_output=True, text=True, check=False)
    if run.returncode != 0:
        raise AssertionError(run.stdout)
    return run.stdout


@unittest.skipIf(COMPILER is None and not IN_CI, "no host C++ compiler")
class LightMailboxTests(unittest.TestCase):
    def test_the_mailbox_keeps_a_stop_under_a_frame_stream(self) -> None:
        """Stop, volume, restart and the "show" hand-back, executed."""
        self.assertIn(
            "light mailbox OK",
            run_cxx(ROOT / "tests" / "cxx" / "light_mailbox_check.cpp"),
        )


class LiveLightPlaybackTests(unittest.TestCase):
    def test_light_override_only_stops_an_authored_scene(self):
        light = branch(
            "act.type == castle_web::ActionType::LIGHT",
            "act.type == castle_web::ActionType::SCENE",
        )
        self.assertIn('id(current_scene).state != "stop"', light)
        self.assertIn("castle_web::light_spec_is_show(act.arg)", light)
        self.assertIn("id(scene_stop)->execute()", light)

    def test_raw_play_reports_no_scene_and_restarts_the_clock(self):
        play = branch(
            "act.type == castle_web::ActionType::PLAY",
            "act.type == castle_web::ActionType::STOP",
        )
        self.assertIn('id(current_scene).publish_state("stop")', play)
        self.assertIn("castle_web::restart_audio_clock(", play)
        scene = branch(
            "act.type == castle_web::ActionType::SCENE",
            "act.type == castle_web::ActionType::PIRCFG",
        )
        self.assertIn("castle_web::restart_audio_clock(", scene)

    def test_mirror_tick_clears_a_raw_track_when_audio_ends(self):
        mirror = SOURCE.split("interval: 200ms", 1)[1].split(
            "- script.execute: web_action", 1
        )[0]
        self.assertIn("castle_web::mirror_audio(playing", mirror)
        self.assertIn("MEDIA_PLAYER_STATE_ANNOUNCING", mirror)
        self.assertIn('id(current_track).publish_state("")', mirror)

    def test_status_reports_the_audio_clock(self):
        web = (ROOT / "firmware" / "sd_web.h").read_text()
        self.assertIn('"playing":%s,"position_ms":%lld', web)
        state = (ROOT / "firmware" / "sd_web_state.h").read_text()
        self.assertIn(
            "inline bool mirror_audio(bool playing, bool sounding, long long now_us,",
            state,
        )
        # L10 (v5.62): the track rides in with it, so the ring's `sound` line
        # names what the amplifier got. The default keeps every other caller
        # (the two C harnesses) compiling unchanged.
        self.assertIn("std::string_view track = {}) {", state)


class StoppedShowGoesDarkTests(unittest.TestCase):
    """A stop must leave the pixels dark. The frame loop can have a colour in
    flight when the stop lands, so the off frame is part of stopping."""

    def test_a_stopped_import_still_sends_the_off_frame(self) -> None:
        stop = device_bridge.threading.Event()
        stop.set()
        with (
            mock.patch.object(light_show, "_show_control", {"stop": stop}),
            mock.patch.object(device_bridge, "call") as call,
        ):
            device_bridge._finish_imported_show(stop, 12, None)
        self.assertEqual(call.call_args.args[0], "/api/light?c=off")

    def test_a_superseded_show_does_not_darken_the_new_one(self) -> None:
        stop = device_bridge.threading.Event()
        stop.set()
        with (
            mock.patch.object(light_show, "_show_control", {"stop": None}),
            mock.patch.object(device_bridge, "call") as call,
        ):
            device_bridge._finish_imported_show(stop, 12, None)
        call.assert_not_called()


class EmulatorMailboxParityTests(unittest.TestCase):
    """The emulator is where the desk's stop path is rehearsed, so its slot
    must drop and keep the same commands the firmware's does."""

    def emu(self):
        # A ticker that never fires: the slot is inspected, not drained.
        patch = mock.patch.object(castle_emu, "APPLY_DELAY_S", 3600)
        patch.start()
        self.addCleanup(patch.stop)
        emu = castle_emu.CastleEmu(port=0)
        self.addCleanup(emu.server_close)
        return emu

    def test_a_light_frame_does_not_evict_a_stop(self) -> None:
        emu = self.emu()
        emu.queue("STOP", "")
        emu.queue("LIGHT", "ff0000")
        self.assertEqual(emu._pending, ("STOP", ""))

    def test_a_light_frame_still_replaces_a_light_frame(self) -> None:
        emu = self.emu()
        emu.queue("LIGHT", "ff0000")
        emu.queue("LIGHT", "00ff00")
        self.assertEqual(emu._pending, ("LIGHT", "00ff00"))

    def test_restart_waits_in_a_latch_of_its_own(self) -> None:
        emu = self.emu()
        emu.queue("RESTART", "")
        emu.queue("SCENE", "vigil")
        self.assertTrue(emu._restart_pending)
        self.assertEqual(emu._pending, ("SCENE", "vigil"))


if __name__ == "__main__":
    unittest.main()
