"""castle_native against a mocked aioesphomeapi: the flash build's leg.

No device, no network: `aioesphomeapi.APIClient` is replaced by a fake
whose connect/device_info/entities/state-stream are scripted per test. What
is under test is castle_native's own contract — connect/failure/drop/
reconnect bookkeeping, the status() field mapping the desk renders,
scene()/stop()/volume() submission, stop()-is-never-vacuous, and that the
studio's many HTTP threads can call in at once without leaking a thread
per call.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import castle_native as cn


class FakeEntity:
    def __init__(self, object_id: str, key: int) -> None:
        self.object_id, self.key = object_id, key


class FakeInfo:
    project_version = "castle 5.23"
    esphome_version = "2026.8.0"


class FakeState:
    def __init__(self, key: int, **attrs: object) -> None:
        self.key = key
        for k, v in attrs.items():
            setattr(self, k, v)


class Script:
    """What the fake client does, shared by every APIClient the link makes."""

    def __init__(self) -> None:
        self.connect_ok = True
        self.entities = [
            FakeEntity("scene__vigil", 1),
            FakeEntity("scene__storm", 2),
            FakeEntity("castle_audio", 3),
            FakeEntity("blackout", 4),
            FakeEntity("current_scene", 5),
            FakeEntity("current_track", 6),
            FakeEntity("sd_card_present", 7),
        ]
        self.states = [
            FakeState(5, state="vigil"),
            FakeState(6, state="01_vigil.mp3"),
            FakeState(7, state=True),
            FakeState(3, volume=0.7),
        ]
        self.connects = 0
        self.calls: list[tuple] = []
        self.fail_calls = False
        self.clients: list[FakeClient] = []
        self.lock = threading.Lock()


class FakeClient:
    def __init__(self, host: str, port: int, password: object) -> None:
        self.host, self.port = host, port
        self.on_stop = None
        SCRIPT.clients.append(self)

    async def connect(self, login: bool = False, on_stop=None) -> None:
        SCRIPT.connects += 1
        if not SCRIPT.connect_ok:
            raise OSError("connect refused (scripted)")
        self.on_stop = on_stop

    async def device_info(self) -> FakeInfo:
        return FakeInfo()

    async def list_entities_services(self):
        return list(SCRIPT.entities), []

    def subscribe_states(self, cb) -> None:
        for s in SCRIPT.states:
            cb(s)

    def button_command(self, key: int) -> None:
        self._record(("button", key))

    def media_player_command(self, key: int, **kw: object) -> None:
        self._record(("media", key, kw))

    def _record(self, call: tuple) -> None:
        if SCRIPT.fail_calls:
            raise RuntimeError("scripted failure")
        with SCRIPT.lock:
            SCRIPT.calls.append(call)

    def drop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Simulate the connection going away (the client's on_stop)."""
        assert self.on_stop is not None
        asyncio.run_coroutine_threadsafe(self.on_stop(False), loop).result(2)


SCRIPT = Script()
FAKE_API = types.SimpleNamespace(
    APIClient=FakeClient, MediaPlayerCommand=types.SimpleNamespace(STOP="STOP")
)


def wait_for(cond, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


class NativeCase(unittest.TestCase):
    HOST = "castle.test"

    def setUp(self) -> None:
        global SCRIPT  # noqa: PLW0603 — the fake's script is per test
        SCRIPT = Script()
        self.patches: list = [
            mock.patch.object(cn, "aioesphomeapi", FAKE_API),
            mock.patch.object(cn, "_links", {}),
            mock.patch.object(cn, "_RETRY_S", 0.05),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(cn.close_all)  # before the patches unwind (LIFO)

    def link(self) -> cn._Link:
        ln = cn._get(self.HOST)
        self.assertTrue(wait_for(lambda: ln.connected), "never connected")
        return ln


class TestConnection(NativeCase):
    def test_connects_and_reports_the_desk_shape(self) -> None:
        self.link()
        st = cn.status(self.HOST)
        assert st is not None
        self.assertEqual(
            st,
            {
                "version": "castle 5.23",
                "scene": "vigil",
                "track": "01_vigil.mp3",
                "native": True,
                "sd_mounted": True,
                "volume": 70,
            },
        )

    def test_connect_failure_is_offline_and_keeps_retrying(self) -> None:
        SCRIPT.connect_ok = False
        cn._get(self.HOST)
        self.assertTrue(wait_for(lambda: SCRIPT.connects >= 3))
        self.assertFalse(cn.connected(self.HOST))
        self.assertIsNone(cn.status(self.HOST))
        self.assertFalse(cn.scene(self.HOST, "vigil"))
        self.assertFalse(cn.stop(self.HOST))
        self.assertFalse(cn.volume(self.HOST, 50))
        SCRIPT.connect_ok = True  # the castle boots
        self.assertTrue(wait_for(lambda: cn.connected(self.HOST)))

    def test_drop_then_reconnect(self) -> None:
        ln = self.link()
        first = SCRIPT.clients[-1]
        first.drop(ln.loop)
        self.assertTrue(wait_for(lambda: not ln.connected))
        self.assertIsNone(cn.status(self.HOST))
        self.assertFalse(cn.stop(self.HOST))  # dropped = not vacuously ok
        self.assertTrue(
            wait_for(lambda: ln.connected and SCRIPT.clients[-1] is not first)
        )
        self.assertTrue(cn.stop(self.HOST))

    def test_missing_optional_entities_leave_fields_out(self) -> None:
        SCRIPT.entities = [
            e
            for e in SCRIPT.entities
            if e.object_id not in ("sd_card_present", "castle_audio")
        ]
        SCRIPT.states = [s for s in SCRIPT.states if s.key not in (3, 7)]
        self.link()
        st = cn.status(self.HOST)
        assert st is not None
        self.assertNotIn("sd_mounted", st)
        self.assertNotIn("volume", st)
        self.assertEqual(st["scene"], "vigil")

    def test_version_falls_back_to_esphome_version(self) -> None:
        with mock.patch.object(FakeInfo, "project_version", ""):
            self.link()
            self.assertEqual(cn.status(self.HOST)["version"], "2026.8.0")  # type: ignore[index]


class TestSubmission(NativeCase):
    def test_scene_presses_the_right_button(self) -> None:
        self.link()
        self.assertTrue(cn.scene(self.HOST, "storm"))
        self.assertEqual(SCRIPT.calls, [("button", 2)])
        self.assertFalse(cn.scene(self.HOST, "nope"))  # no such button
        self.assertEqual(len(SCRIPT.calls), 1)

    def test_stop_halts_audio_then_blacks_out(self) -> None:
        self.link()
        self.assertTrue(cn.stop(self.HOST))
        self.assertEqual(
            SCRIPT.calls, [("media", 3, {"command": "STOP"}), ("button", 4)]
        )

    def test_stop_with_nothing_to_press_is_false(self) -> None:
        """The pass-1 finding: empty key dicts made Stop vacuously True."""
        SCRIPT.entities = [FakeEntity("scene__vigil", 1)]
        self.link()
        self.assertFalse(cn.stop(self.HOST))
        self.assertEqual(SCRIPT.calls, [])

    def test_stop_with_only_a_blackout_button_still_presses_it(self) -> None:
        SCRIPT.entities = [FakeEntity("blackout", 4)]
        self.link()
        self.assertTrue(cn.stop(self.HOST))
        self.assertEqual(SCRIPT.calls, [("button", 4)])

    def test_a_failing_command_is_false_not_a_traceback(self) -> None:
        self.link()
        SCRIPT.fail_calls = True
        self.assertFalse(cn.scene(self.HOST, "vigil"))
        self.assertFalse(cn.stop(self.HOST))
        self.assertFalse(cn.volume(self.HOST, 10))
        self.assertTrue(cn.connected(self.HOST))  # the link itself is fine

    def test_volume_range_and_scaling(self) -> None:
        self.link()
        self.assertTrue(cn.volume(self.HOST, 0))
        self.assertTrue(cn.volume(self.HOST, 100))
        self.assertFalse(cn.volume(self.HOST, 101))
        self.assertFalse(cn.volume(self.HOST, -1))
        self.assertEqual([c[2]["volume"] for c in SCRIPT.calls], [0.0, 1.0])

    def test_a_slow_command_is_cut_off_by_the_call_timeout(self) -> None:
        self.link()
        slow = mock.patch.object(
            FakeClient, "button_command", lambda self, key: time.sleep(1.0)
        )
        with slow, mock.patch.object(cn, "CALL_TIMEOUT_S", 0.2):
            t0 = time.monotonic()
            self.assertFalse(cn.scene(self.HOST, "vigil"))
            self.assertLess(time.monotonic() - t0, 0.8)


class TestThreads(NativeCase):
    def test_one_thread_per_host_however_many_callers(self) -> None:
        before = [t for t in threading.enumerate() if t.name == "castle-native"]
        self.link()
        results: list[bool] = []

        def hammer() -> None:
            for _ in range(5):
                results.append(cn.scene(self.HOST, "vigil"))
                cn.status(self.HOST)
                cn.connected(self.HOST)

        ts = [threading.Thread(target=hammer) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(results, [True] * 40)
        self.assertEqual(len(SCRIPT.calls), 40)
        after = [t for t in threading.enumerate() if t.name == "castle-native"]
        self.assertEqual(len(after) - len(before), 1)

    def test_close_all_ends_the_threads(self) -> None:
        self.link()
        cn.close_all()
        self.assertFalse(
            any(
                t.name == "castle-native" and t.is_alive()
                for t in threading.enumerate()
            )
        )

    def test_two_hosts_two_threads_no_more(self) -> None:
        n0 = sum(t.name == "castle-native" for t in threading.enumerate())
        cn._get("a.test")
        cn._get("b.test")
        cn._get("a.test")
        cn.connected("b.test")
        self.assertEqual(
            sum(t.name == "castle-native" for t in threading.enumerate()) - n0, 2
        )


if __name__ == "__main__":
    unittest.main()
