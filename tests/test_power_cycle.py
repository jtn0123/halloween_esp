"""tools/power_cycle.py against an emulated castle on a fake smart plug.

The plug is tests/fake_plug.py: `on` starts tools/castle_emu.py in its own
process on a free port and `off` kills it, so every cycle here is a real stop
and a real cold start of the thing power_cycle.py is watching — the boot is
timed, the fresh uptime is real, and the card, scene list and play/stop checks
run against the emulator's own replies. The plugs that misbehave are Python
one-liners that switch nothing and exit 0 or 4 (helpers.exits). Every test
switches the plug off in its cleanup, so no emulator outlives it.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # helpers

import castle_emu
import operator_cmd
import power_cycle
from castle_emu_health import reason_code
from helpers import HostEnv, command_line, exits

FAKE_PLUG = Path(__file__).resolve().parent / "fake_plug.py"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class PlugCase(unittest.TestCase, HostEnv):
    def setUp(self) -> None:
        self.host_env("")  # explicitly no castle: only the emulator is named
        self.state = Path(tempfile.mkdtemp(prefix="power-cycle-"))
        self.addCleanup(shutil.rmtree, self.state, True)
        self.port = free_port()
        self.host = f"127.0.0.1:{self.port}"
        self.addCleanup(self.plug, "off")  # runs before the rmtree above
        self.out = self.state / "out"
        self.out.mkdir()

    def plug_argv(self, verb: str, *extra: str) -> list[str]:
        return [
            sys.executable,
            str(FAKE_PLUG),
            verb,
            "--port",
            str(self.port),
            "--state",
            str(self.state),
            *extra,
        ]

    def plug_cmd(self, verb: str, *extra: str) -> str:
        return command_line(self.plug_argv(verb, *extra))

    def plug(self, verb: str, *extra: str) -> None:
        subprocess.run(self.plug_argv(verb, *extra), check=True, timeout=30)

    def switch_on(self, *extra: str) -> None:
        self.plug("on", *extra)
        deadline = time.monotonic() + 15
        while power_cycle.status_now(self.host) is None:
            self.assertLess(time.monotonic(), deadline, "the emulator never answered")
            time.sleep(0.1)

    def cycle(
        self, *flags: str, off: str = "", on: str = ""
    ) -> tuple[int, dict[str, Any], str]:
        said: list[str] = []
        args = power_cycle.parse(
            [
                self.host,
                "--off-cmd",
                off or self.plug_cmd("off"),
                "--on-cmd",
                on or self.plug_cmd("on"),
                "--off-s",
                "0.1",
                "--settle-s",
                "0",
                "--interval",
                "0.1",
                "--boot-timeout",
                "15",
                "--play-timeout",
                "5",
                "--out",
                str(self.out),
                "--no-keep-awake",
                *flags,
            ]
        )
        code = power_cycle.run(self.host, args, self.out, echo=said.append)
        path = self.out / "summary.json"
        summary = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return code, summary, "\n".join(said)


class TestPowerCycle(PlugCase):
    def test_two_cold_boots_come_back_whole(self) -> None:
        self.switch_on()
        with contextlib.redirect_stdout(io.StringIO()) as said:
            code = power_cycle.main(
                [
                    self.host,
                    "--cycles",
                    "2",
                    "--off-cmd",
                    self.plug_cmd("off"),
                    "--on-cmd",
                    self.plug_cmd("on"),
                    "--off-s",
                    "0.1",
                    "--settle-s",
                    "0",
                    "--interval",
                    "0.1",
                    "--out",
                    str(self.out),
                    "--no-keep-awake",
                ]
            )
        self.assertEqual(code, 0, said.getvalue())
        s = json.loads((self.out / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual((s["cycles_run"], s["whole"], s["failed"]), (2, 2, []))
        self.assertEqual(s["reset_reasons"], ["power-on"])
        self.assertLess(s["boot_s"]["max"], 15)
        lines = (self.out / "cycles.jsonl").read_text(encoding="utf-8").splitlines()
        first = json.loads(lines[0])
        self.assertEqual((first["ok"], first["scene"]), (True, "vigil"))
        self.assertLess(first["uptime_s"], 15)
        self.assertIn(
            "POWER-CYCLE PASS", (self.out / "verdict.txt").read_text(encoding="utf-8")
        )

    def test_a_castle_with_no_card_fails_its_cycle(self) -> None:
        self.switch_on("--no-sd")
        code, s, said = self.cycle(
            "--cycles", "1", "--ready-timeout", "0.5", on=self.plug_cmd("on", "--no-sd")
        )
        self.assertEqual(code, 1, said)
        self.assertIn("card not mounted", s["failed"][0]["failures"][0])

    def test_a_castle_that_never_comes_back(self) -> None:
        self.switch_on()
        code, s, said = self.cycle(
            "--cycles", "3", "--stop-after", "1", "--boot-timeout", "0.5", on=exits(0)
        )
        self.assertEqual(code, 1, said)
        self.assertEqual(s["cycles_run"], 1)
        self.assertIn("did not answer", said)
        self.assertIn("stopping: 1 cycles in a row failed", said)

    def test_plugs_that_misbehave_are_the_harness_failing(self) -> None:
        self.switch_on()
        code, s, said = self.cycle(
            "--cycles", "1", "--down-timeout", "0.5", off=exits(0)
        )
        self.assertEqual(code, 2)
        self.assertIn("did not cut its power", s["harness_error"])
        code, s, said = self.cycle("--cycles", "1", off=exits(4))
        self.assertEqual(code, 2)
        self.assertIn("exit 4", said)
        missing = str(self.state / "no-such-plug-tool")
        code, s, said = self.cycle("--cycles", "1", off=command_line([missing, "off"]))
        self.assertEqual(code, 2)
        self.assertIn("the off command could not start", s["harness_error"])
        self.assertIsNotNone(power_cycle.status_now(self.host))  # still on

    def test_an_interrupted_run_switches_the_castle_back_on(self) -> None:
        self.switch_on()
        # Interrupted with the power OFF: power_off ran, power_on never did.
        with mock.patch.object(power_cycle, "power_on", side_effect=KeyboardInterrupt):
            code, s, _ = self.cycle("--cycles", "2")
        self.assertEqual((code, s["interrupted"]), (1, True))
        self.switch_on()  # the finally ran --on-cmd: it answers again

    def test_nothing_to_cycle(self) -> None:
        code, _, said = self.cycle("--cycles", "1")
        self.assertEqual(code, 2)
        self.assertIn("not answering before the first cycle", said)


class TestAfterBoot(unittest.TestCase):
    """Step 4 and 5 against an in-process castle whose replies a test sets:
    a crash reason and an uptime that says the power never went."""

    def test_a_crash_a_stale_uptime_and_an_unknown_scene(self) -> None:
        card = Path(tempfile.mkdtemp(prefix="power-cycle-card-"))
        self.addCleanup(shutil.rmtree, card, True)
        emu = castle_emu.CastleEmu(port=0, sd_dir=card, scenes=["vigil"])
        emu.state.boot = time.monotonic() - 1000
        emu.reset_reason = reason_code("BROWNOUT")  # was_crash follows the word
        emu.start()
        self.addCleanup(emu.server_close)
        self.addCleanup(emu.shutdown)
        host = f"127.0.0.1:{emu.port}"
        args = power_cycle.parse(
            [
                host,
                "--off-cmd",
                "x",
                "--on-cmd",
                "y",
                "--scene",
                "nope",
                "--interval",
                "0.1",
                "--play-timeout",
                "0.5",
            ]
        )
        c = power_cycle.Cycle(1, boot_s=1.0)
        first = power_cycle.status_now(host)
        assert first is not None
        power_cycle.after_boot(host, args, c, first)
        self.assertEqual(c.reason, "BROWNOUT")
        joined = " | ".join(c.failures)
        for what in (
            "never lost power",
            "came back from a crash: BROWNOUT",
            "scene nope: HTTP 404",
        ):
            self.assertIn(what, joined)
        self.assertIn("FAIL", c.line())


class TestCommandLine(unittest.TestCase, HostEnv):
    def test_refusals(self) -> None:
        self.host_env("")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(power_cycle.main(["--off-cmd", "a", "--on-cmd", "b"]), 2)
            with tempfile.NamedTemporaryFile() as f:
                argv = [
                    "127.0.0.1:1",
                    "--off-cmd",
                    "a",
                    "--on-cmd",
                    "b",
                    "--out",
                    str(Path(f.name) / "x"),
                ]
                self.assertEqual(power_cycle.main(argv), 2)
            for bad in (
                ["--off-cmd", "a", "--on-cmd", "b", "--cycles", "0"],
                ["--off-cmd", "a"],
            ):
                with self.subTest(bad), self.assertRaises(SystemExit):
                    power_cycle.parse(["h", *bad])


class TestOperatorCmd(unittest.TestCase):
    """A plug command is split, not interpreted: the platform's quoting, no
    shell (the no-shell run itself is test_soak.TestDisruption's)."""

    def test_each_platform_splits_its_own_quoting(self) -> None:
        posix = operator_cmd.argv_of(
            "curl -fsS 'http://plug/relay?turn=off' \"two words\"", posix=True
        )
        self.assertEqual(
            posix, ["curl", "-fsS", "http://plug/relay?turn=off", "two words"]
        )
        win = '"C:\\Program Files\\kasa.exe" --host 192.168.1.30 "a b" \'c d\''
        self.assertEqual(
            operator_cmd.argv_of(win, posix=False),
            ["C:\\Program Files\\kasa.exe", "--host", "192.168.1.30", "a b", "c d"],
        )
        line = command_line([sys.executable, "-c", "print(1)", r"C:\x y"])
        self.assertEqual(
            operator_cmd.argv_of(line), [sys.executable, "-c", "print(1)", r"C:\x y"]
        )

    def test_an_empty_command_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            operator_cmd.run("   ", 5)


if __name__ == "__main__":
    unittest.main()
