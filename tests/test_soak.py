"""tools/soak.py run for seconds against an emulated castle that has a bad night.

The emulator (tools/castle_emu.py) is the castle, in this process, on a free
port. The faults are the ones a porch hands out — the castle drops off the
network and comes back, it reboots with a watchdog reason, its card starts
tearing reads, its radio fades — and each is made by changing the emulator's
own state (`readings`, `health`, the boot instant) or by closing and
reopening its listening socket, never by teaching the wire anything new: the
emulator stays the byte-level port of sd_web.h that test_firmware_contract
holds it to.

Nothing here races a timer against the loop. The soak's clock is a
PollClock: it moves only when a poll ends (and the castle's uptime with it),
a fault is set for "before poll k", and a run is a number of polls — so a
bad night happens the same way on a slow CI runner as on a fast laptop. The
only real waits are the loop's own short sleeps and the emulator's 200 ms
tick, and a test that needs the tick waits for a condition, not a duration.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import socket
import sys
import tempfile
import time
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # helpers

import castle_emu
import soak
import soak_run
from castle_emu_health import CRASHES, reason_code
from helpers import HostEnv, command_line, exits


class Castle:
    """An in-process emulated castle a test can unplug, replug and reboot."""

    def __init__(self) -> None:
        self.card = Path(tempfile.mkdtemp(prefix="soak-card-"))
        self.emu = castle_emu.CastleEmu(
            port=0, sd_dir=self.card, scenes=["vigil", "storm"]
        )
        self.emu.state.boot = time.monotonic() - 1000  # up for a while already
        self.emu.start()
        self.host = f"127.0.0.1:{self.emu.port}"

    def unplug(self) -> None:
        """Gone from the network: connections are refused, not left hanging."""
        self.emu.shutdown()
        self.emu.socket.close()

    def replug(self) -> None:
        """Back, on the same address, with whatever state it had."""
        e = self.emu
        e.socket = socket.socket(e.address_family, e.socket_type)
        e.server_bind()
        e.server_activate()
        e.start()

    def reboot(self, reason: str) -> None:
        """A fresh uptime and the health counters a restart leaves: one more
        boot, one more crash when castle_health.h's was_crash() says the
        reason is one, `reason` as this boot's, and the RAM-only torn-read
        count back at 0. Through the castle's own knobs (v5.75), so
        /api/health and the owner's page tell the same story."""
        e = self.emu
        with e.state.lock:
            e.state.boot = time.monotonic()
        e.boots += 1
        e.crashes += int(reason in CRASHES)
        e.reset_reason = reason_code(reason)
        e.health["sd_read_errors"] = 0

    def close(self) -> None:
        self.emu.shutdown()
        self.emu.server_close()
        shutil.rmtree(self.card, ignore_errors=True)


class PollClock:
    """The soak's clock, moved by its polls and nothing else. Each poll ends
    `step` seconds later; `before(k, fault)` runs a fault just before poll k;
    `sleep(s)` is this computer asleep for s seconds in the middle of a poll.
    The castle lives on the same clock: its uptime moves with every step and
    every sleep, as a real castle's does while the monitor is away."""

    def __init__(self, castle: Castle, step: float) -> None:
        self.castle, self.step = castle, step
        self.now = 1_800_000_000.0
        self.polls = 0
        self.faults: dict[int, Callable[[], object]] = {}

    def __call__(self) -> float:
        return self.now

    def before(self, poll: int, fault: Callable[[], object]) -> None:
        self.faults[poll] = fault

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self.now += seconds
        state = self.castle.emu.state
        with state.lock:
            state.boot -= seconds

    def wrap(self, real: Callable[..., None]) -> Callable[..., None]:
        def poll(run: soak_run.Soak, wall: float, n: int) -> None:
            self.polls += 1
            fault = self.faults.pop(self.polls, None)
            if fault is not None:
                fault()
            real(run, wall, n)
            self.advance(self.step)

        return poll


class SoakCase(unittest.TestCase, HostEnv):
    def setUp(self) -> None:
        self.host_env("")  # explicitly no castle: only the emulator is named
        self.castle = Castle()
        self.addCleanup(self.castle.close)
        self.out = Path(tempfile.mkdtemp(prefix="soak-out-"))
        self.addCleanup(shutil.rmtree, self.out, True)
        self.said: list[str] = []
        self.clock = PollClock(self.castle, step=1.0)

    def soak(self, polls: int, *flags: str) -> tuple[int, dict[str, Any]]:
        """A run of `polls` polls on the PollClock, `self.clock.step` apart."""
        argv = [
            self.castle.host,
            "--hours",
            str(polls * self.clock.step / 3600),
            "--interval",
            "0.05",
            "--out",
            str(self.out),
            "--no-keep-awake",
            *flags,
        ]
        args = soak.parse(argv)
        run = soak_run.Soak(
            self.castle.host,
            args,
            soak.limits_of(args),
            self.out,
            clock=self.clock,
            echo=self.said.append,
        )
        with mock.patch.object(
            soak_run.Soak, "poll", self.clock.wrap(soak_run.Soak.poll)
        ):
            code = run.run()
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        return code, summary

    def until(self, what: str, ok: Callable[[], bool]) -> None:
        """The emulator's own tick, waited for by condition, not by duration."""
        deadline = time.monotonic() + 10
        while not ok():
            self.assertLess(time.monotonic(), deadline, what)
            time.sleep(0.05)

    def kinds(self) -> list[str]:
        lines = (self.out / "soak.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(line)["kind"] for line in lines]


class TestGoodNight(SoakCase):
    def test_a_quiet_castle_passes(self) -> None:
        code, s = self.soak(10)
        self.assertEqual(code, 0, "\n".join(self.said))
        self.assertEqual(s["samples"], 10)
        self.assertEqual((s["reboots"], s["misses"], s["outages"]), (0, 0, []))
        self.assertEqual(s["heap_min_kb"], 64)
        self.assertEqual(s["first_boot_reason"], "power-on")
        self.assertIn(
            "SOAK PASS", (self.out / "verdict.txt").read_text(encoding="utf-8")
        )
        self.assertTrue((self.out / "bootlog-1.txt").is_file())
        for kind in ("start", "status", "health", "bootlog", "verdict"):
            self.assertIn(kind, self.kinds())

    def test_driving_scenes_starts_one_and_stops_it_at_the_end(self) -> None:
        code, s = self.soak(10, "--drive", "scenes", "--start-timeout", "5")
        self.assertEqual(code, 0, "\n".join(self.said))
        self.assertEqual((s["starts_ok"], s["starts_failed"]), (1, []))
        self.assertIn("event", self.kinds())  # the ring's record of the start
        self.until("the stop lands", lambda: self.castle.emu.state.scene == "stop")

    def test_driving_the_show_and_a_scene_it_does_not_know(self) -> None:
        code, s = self.soak(8, "--drive", "show", "--start-timeout", "5")
        self.assertEqual((code, s["starts_ok"]), (0, 1), "\n".join(self.said))
        self.until("the show stops", lambda: not self.castle.emu.state.show_on)
        code, s = self.soak(8, "--drive", "scenes", "--scenes", "nope")
        self.assertEqual(code, 1)
        self.assertEqual(s["starts_failed"], ["nope: HTTP 404"])


class TestDisruption(SoakCase):
    """--disrupt-cmd: steps run in order, as programs (no shell), judged on
    their exits — and the last one runs even when the soak is stopping."""

    def step(self, word: str, code: int = 0) -> str:
        """A step that writes `word,` to the marker file and exits `code`."""
        src = "import sys; open(sys.argv[1], 'a').write(sys.argv[2] + ',')"
        argv = [sys.executable, "-c", f"{src}; raise SystemExit({code})"]
        return command_line([*argv, str(self.marker), word])

    def setUp(self) -> None:
        super().setUp()
        self.marker = self.out / "steps.txt"

    def steps_seen(self) -> str:
        return self.marker.read_text(encoding="utf-8") if self.marker.exists() else ""

    def test_every_step_runs_in_order_and_a_failed_one_fails_the_night(self) -> None:
        flags = ("--disrupt-at", "0", "--disrupt-hold", "0")
        cmds = ("--disrupt-cmd", self.step("off", 3), "--disrupt-cmd", self.step("on"))
        code, s = self.soak(5, *flags, *cmds)
        self.assertEqual(code, 1)
        self.assertEqual(self.steps_seen(), "off,on,")  # the failure stopped nothing
        d = s["disruptions"][0]
        self.assertEqual((d["exit"], [st["exit"] for st in d["steps"]]), (3, [3, 0]))
        self.assertIn("disruption command", self.verdict())
        code, s = self.soak(5, "--disrupt-at", "0", "--disrupt-cmd", exits(0))
        self.assertEqual((code, s["disruptions"][0]["exit"]), (0, 0))

    def test_a_step_is_a_program_and_its_arguments_with_no_shell(self) -> None:
        missing = command_line([str(self.out / "no-such-plug-tool"), "off"])
        code, s = self.soak(5, "--disrupt-at", "0", "--disrupt-cmd", missing)
        d = s["disruptions"][0]
        self.assertEqual((code, d["exit"]), (1, -1))
        self.assertIn("could not start", d["output"])
        echo = [sys.executable, "-c", "import sys; print(sys.argv[1:])"]
        line = command_line(echo) + " && exit 4 | $HOME"
        code, s = self.soak(5, "--disrupt-at", "0", "--disrupt-cmd", line)
        self.assertEqual(code, 0)
        self.assertIn(
            "['&&', 'exit', '4', '|', '$HOME']", s["disruptions"][0]["output"]
        )

    def test_a_stopped_run_cuts_the_hold_and_still_switches_back_on(self) -> None:
        def interrupt() -> None:
            self.until("the off step ran", lambda: self.steps_seen() == "off,")
            raise KeyboardInterrupt

        self.clock.before(4, interrupt)
        flags = ("--disrupt-at", "0", "--disrupt-hold", "600")
        cmds = ("--disrupt-cmd", self.step("off"), "--disrupt-cmd", self.step("on"))
        t0 = time.monotonic()
        code, s = self.soak(30, *flags, *cmds)
        self.assertLess(time.monotonic() - t0, 120)  # not the 600 s hold
        self.assertEqual(code, 1)  # stopped early
        self.assertEqual(self.steps_seen(), "off,on,")
        self.assertEqual([st["exit"] for st in s["disruptions"][0]["steps"]], [0, 0])

    def verdict(self) -> str:
        return (self.out / "verdict.txt").read_text(encoding="utf-8")


class TestBadNight(SoakCase):
    def test_outage_reboot_crash_dying_card_and_fading_radio(self) -> None:
        c = self.castle

        def away() -> None:
            c.emu.readings["rssi"] = -88
            c.unplug()

        def back() -> None:
            c.reboot("task-watchdog")
            c.emu.health.update(
                sd_read_errors=2, sd_last_error="x.mp3@4096", heap_min_kb=12
            )
            c.emu.sd_mounted = False
            c.replug()

        self.clock.before(5, away)
        self.clock.before(13, back)
        code, s = self.soak(25, "--outage-min-s", "3")
        said = "\n".join(self.said)
        self.assertEqual(code, 1, said)
        self.assertEqual((s["reboots"], s["crashes"]), (1, 1), said)
        self.assertEqual(s["reset_reasons"], ["task-watchdog"])
        # Gone before poll 5, back before poll 13: polls 5-12 saw it, on
        # any runner, and they are one outage of eight poll steps.
        self.assertEqual(s["misses"], 8, said)
        self.assertEqual(len(s["outages"]), 1, said)
        self.assertEqual(s["outages"][0]["seconds"], 8.0)
        self.assertTrue(s["outages"][0]["rebooted"])
        self.assertEqual((s["sd_read_errors"], s["sd_last_error"]), (2, "x.mp3@4096"))
        self.assertEqual(s["heap_min_kb"], 12)
        self.assertGreater(s["unmounted_samples"], 0)
        self.assertEqual(s["rssi_drops"], 1)
        self.assertIn("castle NOT answering", said)
        self.assertIn("REBOOT detected (uptime went back)", said)
        self.assertTrue((self.out / "bootlog-2.txt").is_file())
        verdict = (self.out / "verdict.txt").read_text(encoding="utf-8")
        for name in ("reboots", "crashes", "card read errors", "heap low-water"):
            self.assertRegex(verdict, rf"FAIL\s+{name}")
        self.assertIn("gap", self.kinds())

    def test_a_castle_that_never_answers(self) -> None:
        self.castle.unplug()
        code, s = self.soak(5)
        self.assertEqual((code, s["samples"]), (1, 0))
        self.assertIn("never", (self.out / "verdict.txt").read_text(encoding="utf-8"))

    def test_an_interrupted_run_is_judged_as_short(self) -> None:
        def interrupt() -> None:
            raise KeyboardInterrupt

        self.clock.before(4, interrupt)
        code, s = self.soak(30, "--drive", "show")
        self.assertEqual((code, s["samples"]), (1, 3))
        self.assertIn(
            "ran the full time", (self.out / "verdict.txt").read_text(encoding="utf-8")
        )

    def test_this_computer_sleeping_is_not_the_castles_fault(self) -> None:
        """Asleep for 60 s in the middle of a 200 s run: unwatched time, and
        the castle — whose uptime ran on through it — is not blamed."""
        self.clock.step = 5.0
        self.clock.before(5, lambda: self.clock.sleep(60))
        with mock.patch.object(soak_run, "HEARTBEAT_S", 0.0):
            code, s = self.soak(40)
        self.assertEqual(code, 0, "\n".join(self.said))
        self.assertTrue(55 < s["monitor_paused_s"] < 70, s["monitor_paused_s"])
        self.assertEqual((s["reboots"], s["misses"], s["outages"]), (0, 0, []))
        self.assertGreater(s["samples"], 20)  # it went on watching after
        self.assertTrue(any(" polls, " in line for line in self.said))  # heartbeat
        self.assertIn("pause", self.kinds())

    def test_a_sleep_that_outlasts_the_run_is_still_unwatched_time(self) -> None:
        """The CI flake on PR #66: the machine wakes after the run's end.
        The loop must not leave on the deadline before counting the sleep."""
        self.clock.before(3, lambda: self.clock.sleep(100))
        code, s = self.soak(10)
        self.assertEqual(code, 0, "\n".join(self.said))
        self.assertEqual(s["samples"], 3)
        self.assertGreater(s["monitor_paused_s"], 90)
        verdict = (self.out / "verdict.txt").read_text(encoding="utf-8")
        self.assertIn("unwatched (this computer slept)", verdict)


class TestCommandLine(SoakCase):
    def test_main_runs_and_names_its_own_folder(self) -> None:
        with (
            mock.patch.object(soak, "REPO", self.out),
            contextlib.redirect_stdout(io.StringIO()) as o,
        ):
            code = soak.main(
                [
                    self.castle.host,
                    "--hours",
                    str(0.5 / 3600),
                    "--interval",
                    "0.1",
                    "--no-keep-awake",
                    "--max-reboots",
                    "2",
                ]
            )
        self.assertEqual(code, 0, o.getvalue())
        runs = list((self.out / "soak-logs").iterdir())
        self.assertEqual(len(runs), 1)
        self.assertTrue(runs[0].name.startswith(f"127.0.0.1_{self.castle.emu.port}-"))

    def test_refusals(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(
                soak.main(["--hours", "1"]), 2
            )  # CASTLE_HOST="" names none
            blocker = self.out / "file"
            blocker.write_text("", encoding="utf-8")
            self.assertEqual(
                soak.main([self.castle.host, "--out", str(blocker / "x")]), 2
            )
            for bad in (
                ["--hours", "0"],
                ["--disrupt-at", "1"],
                ["--disrupt-cmd", "true", "--disrupt-at", "5", "--hours", "2"],
                ["--disrupt-cmd", "true", "--disrupt-hold", "-1"],
            ):
                with self.subTest(bad), self.assertRaises(SystemExit):
                    soak.parse(["h", *bad])
        args = soak.parse(["h", "--max-reboots", "2", "--heap-floor-kb", "30"])
        limits = soak.limits_of(args)
        self.assertEqual((limits.max_reboots, limits.heap_floor_kb), (2, 30))

    def test_keep_awake_undoes_itself(self) -> None:
        undo = soak.keep_awake()
        undo()


if __name__ == "__main__":
    unittest.main()
