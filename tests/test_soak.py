"""tools/soak.py run for seconds against an emulated castle that has a bad night.

The emulator (tools/castle_emu.py) is the castle, in this process, on a free
port. The faults are the ones a porch hands out — the castle drops off the
network and comes back, it reboots with a watchdog reason, its card starts
tearing reads, its radio fades — and each is made by changing the emulator's
own state (`readings`, `health`, the boot instant) or by closing and
reopening its listening socket, never by teaching the wire anything new: the
emulator stays the byte-level port of sd_web.h that test_firmware_contract
holds it to. Runs are seconds long with a 0.1 s poll; every emulator and
timer is stopped in a cleanup, whatever the test did.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import socket
import sys
import tempfile
import threading
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
from helpers import HostEnv


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

    def reboot(self, reason: str, crash: bool = False) -> None:
        """A fresh uptime and the health counters a restart leaves."""
        h = self.emu.health
        with self.emu.state.lock:
            self.emu.state.boot = time.monotonic()
        h["boots"] = int(str(h["boots"])) + 1
        h["crashes"] = int(str(h["crashes"])) + int(crash)
        h["last_reset"], h["was_crash"], h["sd_read_errors"] = reason, crash, 0

    def close(self) -> None:
        self.emu.shutdown()
        self.emu.server_close()
        shutil.rmtree(self.card, ignore_errors=True)


class SoakCase(unittest.TestCase, HostEnv):
    def setUp(self) -> None:
        self.host_env("")  # explicitly no castle: only the emulator is named
        self.castle = Castle()
        self.addCleanup(self.castle.close)
        self.out = Path(tempfile.mkdtemp(prefix="soak-out-"))
        self.addCleanup(shutil.rmtree, self.out, True)
        self.said: list[str] = []

    def at(self, seconds: float, fault: Callable[[], object]) -> None:
        timer = threading.Timer(seconds, fault)
        timer.start()
        self.addCleanup(timer.cancel)

    def soak(self, seconds: float, *flags: str) -> tuple[int, dict[str, Any]]:
        argv = [
            self.castle.host,
            "--hours",
            str(seconds / 3600),
            "--interval",
            "0.1",
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
            echo=self.said.append,
        )
        code = run.run()
        summary = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        return code, summary

    def kinds(self) -> list[str]:
        lines = (self.out / "soak.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(line)["kind"] for line in lines]


class TestGoodNight(SoakCase):
    def test_a_quiet_castle_passes(self) -> None:
        code, s = self.soak(1.0)
        self.assertEqual(code, 0, "\n".join(self.said))
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
        code, s = self.soak(1.0, "--drive", "scenes", "--start-timeout", "3")
        self.assertEqual(code, 0, "\n".join(self.said))
        self.assertEqual((s["starts_ok"], s["starts_failed"]), (1, []))
        self.assertIn("event", self.kinds())  # the ring's record of the start
        time.sleep(0.5)  # the stop lands on the emulator's next tick
        self.assertEqual(self.castle.emu.state.scene, "stop")

    def test_driving_the_show_and_a_scene_it_does_not_know(self) -> None:
        code, s = self.soak(0.8, "--drive", "show", "--start-timeout", "2")
        self.assertEqual((code, s["starts_ok"]), (0, 1), "\n".join(self.said))
        time.sleep(0.5)
        self.assertFalse(self.castle.emu.state.show_on)
        code, s = self.soak(0.8, "--drive", "scenes", "--scenes", "nope")
        self.assertEqual(code, 1)
        self.assertEqual(s["starts_failed"], ["nope: HTTP 404"])

    def test_a_disruption_command_runs_and_is_judged(self) -> None:
        code, s = self.soak(1.0, "--disrupt-cmd", "exit 3", "--disrupt-at", "0")
        self.assertEqual(code, 1)
        self.assertEqual(s["disruptions"][0]["exit"], 3)


class TestBadNight(SoakCase):
    def test_outage_reboot_crash_dying_card_and_fading_radio(self) -> None:
        c = self.castle

        def away() -> None:
            c.emu.readings["rssi"] = -88
            c.unplug()

        def back() -> None:
            c.reboot("task-watchdog", crash=True)
            c.emu.health.update(
                sd_read_errors=2, sd_last_error="x.mp3@4096", heap_min_kb=12
            )
            c.emu.sd_mounted = False
            c.replug()

        self.at(0.5, away)
        self.at(1.3, back)
        code, s = self.soak(2.5, "--outage-min-s", "0.3")
        said = "\n".join(self.said)
        self.assertEqual(code, 1, said)
        self.assertEqual((s["reboots"], s["crashes"]), (1, 1), said)
        self.assertEqual(s["reset_reasons"], ["task-watchdog"])
        self.assertEqual(len(s["outages"]), 1)
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
        code, s = self.soak(0.5)
        self.assertEqual((code, s["samples"]), (1, 0))
        self.assertIn("never", (self.out / "verdict.txt").read_text(encoding="utf-8"))

    def test_an_interrupted_run_is_judged_as_short(self) -> None:
        calls = {"n": 0}
        real = soak_run.Soak.poll

        def poll(run: soak_run.Soak, wall: float, n: int) -> None:
            calls["n"] += 1
            if calls["n"] == 4:
                raise KeyboardInterrupt
            real(run, wall, n)

        with mock.patch.object(soak_run.Soak, "poll", poll):
            code, _ = self.soak(30.0, "--drive", "show")
        self.assertEqual(code, 1)
        self.assertIn(
            "ran the full time", (self.out / "verdict.txt").read_text(encoding="utf-8")
        )

    def test_this_computer_sleeping_is_not_the_castles_fault(self) -> None:
        jump = {"s": 0.0}
        args = soak.parse(
            [
                self.castle.host,
                "--hours",
                "0.01",
                "--interval",
                "0.1",
                "--out",
                str(self.out),
                "--no-keep-awake",
            ]
        )
        run = soak_run.Soak(
            self.castle.host,
            args,
            soak.limits_of(args),
            self.out,
            clock=lambda: time.time() + jump["s"],
            echo=self.said.append,
        )
        self.at(0.4, lambda: jump.update(s=100.0))
        with mock.patch.object(soak_run, "HEARTBEAT_S", 0.0):
            self.assertEqual(run.run(), 0, "\n".join(self.said))
        s = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertGreater(s["monitor_paused_s"], 90)
        self.assertEqual(s["reboots"], 0)
        self.assertTrue(any(" polls, " in line for line in self.said))  # heartbeat


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
