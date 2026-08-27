"""device.py without a device: entity matching and the command dispatch.

The ESPHome API client is replaced by a recorder, so `press`, `set`, `list`
and `watch` can be checked for what they would send — and the log
subscription is asserted to happen BEFORE the press, which is the ordering
the module's own comment explains (the reply races the subscription).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import device
import helpers  # noqa: F401  (hermetic env)


def ent(name: str, key: int, kind: str = "ButtonInfo") -> types.SimpleNamespace:
    return types.SimpleNamespace(name=name, key=key, __class__=type(kind, (), {}))


ENTITIES = [
    ent("Dump boot log", 1),
    ent("SD file", 2, "TextInfo"),
    ent("Stop audio", 3),
    ent("Volume", 4, "NumberInfo"),
]


class FakeClient:
    def __init__(self, host: str, port: int = 6053, password: str = "") -> None:
        self.host, self.port = host, port
        self.log: list[tuple] = []

    async def connect(self, login: bool = True) -> None:
        self.log.append(("connect", self.host, self.port))

    async def list_entities_services(self):
        return list(ENTITIES), []

    def subscribe_logs(self, cb, log_level=None) -> None:
        self.log.append(("subscribe", log_level))
        cb(types.SimpleNamespace(message=b"[I][castle] hello"))

    def button_command(self, key: int) -> None:
        self.log.append(("press", key))

    def text_command(self, key: int, value: str) -> None:
        self.log.append(("set", key, value))

    async def disconnect(self) -> None:
        self.log.append(("disconnect",))


class TestKeyFor(unittest.TestCase):
    def test_exact_match_is_case_insensitive(self) -> None:
        self.assertEqual(device._key_for(ENTITIES, "sd FILE").key, 2)

    def test_exact_beats_substring(self) -> None:
        ents = [ent("Stop audio now", 9), ent("Stop audio", 3)]
        self.assertEqual(device._key_for(ents, "stop audio").key, 3)

    def test_substring_falls_back(self) -> None:
        self.assertEqual(device._key_for(ENTITIES, "boot").key, 1)

    def test_no_match_is_none(self) -> None:
        self.assertIsNone(device._key_for(ENTITIES, "kettle"))


class TestRun(unittest.TestCase):
    def setUp(self) -> None:
        self.clients: list[FakeClient] = []

        def make(host, port=6053, password=""):
            c = FakeClient(host, port, password)
            self.clients.append(c)
            return c

        async def no_wait(fut, timeout=None):
            raise asyncio.TimeoutError

        async def no_sleep(_s):
            pass

        for p in (
            mock.patch.object(device, "APIClient", make),
            mock.patch.object(device.asyncio, "wait_for", no_wait),
            mock.patch.object(device.asyncio, "sleep", no_sleep),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.out = io.StringIO()
        self.err = io.StringIO()

    def run_cmd(self, cmd: str, *args: str) -> int:
        with contextlib.redirect_stdout(self.out), contextlib.redirect_stderr(self.err):
            return asyncio.run(device.run("10.1.1.1", cmd, list(args)))

    def test_list_prints_every_entity_and_disconnects(self) -> None:
        self.assertEqual(self.run_cmd("list"), 0)
        text = self.out.getvalue()
        for name in ("Dump boot log", "SD file", "Stop audio", "Volume"):
            self.assertIn(name, text)
        self.assertEqual(self.clients[0].log[-1], ("disconnect",))

    def test_press_subscribes_to_logs_first_then_presses(self) -> None:
        self.assertEqual(self.run_cmd("press", "dump", "boot"), 0)
        log = self.clients[0].log
        kinds = [e[0] for e in log]
        self.assertLess(kinds.index("subscribe"), kinds.index("press"))
        self.assertIn(("press", 1), log)
        self.assertEqual(log[1][1], device.LogLevel.LOG_LEVEL_VERY_VERBOSE)
        self.assertIn("[castle] hello", self.out.getvalue())  # the log line

    def test_set_sends_the_rest_of_argv_as_the_value(self) -> None:
        self.assertEqual(self.run_cmd("set", "SD file", "spooky", "song.mp3"), 0)
        self.assertIn(("set", 2, "spooky song.mp3"), self.clients[0].log)

    def test_unknown_entity_is_a_2_and_sends_nothing(self) -> None:
        self.assertEqual(self.run_cmd("press", "kettle"), 2)
        self.assertEqual(self.run_cmd("set", "kettle", "x"), 2)
        sent = [e for c in self.clients for e in c.log if e[0] in ("press", "set")]
        self.assertEqual(sent, [])
        self.assertIn("kettle", self.err.getvalue())

    def test_unknown_command_is_a_2(self) -> None:
        self.assertEqual(self.run_cmd("dance"), 2)

    def test_watch_is_fine_with_no_action(self) -> None:
        self.assertEqual(self.run_cmd("watch", "0.1"), 0)
        kinds = [e[0] for e in self.clients[0].log]
        self.assertEqual(kinds, ["connect", "subscribe", "disconnect"])


class TestMain(unittest.TestCase):
    def test_no_command_prints_the_usage(self) -> None:
        out = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"CASTLE_HOST": "10.1.1.1"}),
            mock.patch.object(sys, "argv", ["device.py"]),
            contextlib.redirect_stdout(out),
        ):
            self.assertEqual(device.main(), 2)
        self.assertIn("press", out.getvalue())


if __name__ == "__main__":
    unittest.main()
