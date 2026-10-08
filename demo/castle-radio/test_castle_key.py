"""The castle key in Castle Radio (firmware v5.74).

A keyed castle refuses changes without `X-Castle-Key`. These drive the real
bridge, uploader and routes at tools/castle_emu with a key set, the store
sandboxed through CASTLE_DEVICES, and hold Castle Radio to four promises:

  - it sends the key the store holds for the castle it talks to, on every
    change — commands, deletes and the streamed upload alike;
  - a refusal reads "This castle has a key — enter it in Settings", as a 401
    the page can tell apart, and a sync says so BEFORE any bytes go out;
  - the Run settings card can use a key (checked against the castle before
    it is remembered), set a new one and clear it;
  - no reply carries the key, and CASTLE_KEY, when set, pins it.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import radio_env  # noqa: F401 — the sandbox first, then tools/ on the path

# isort: split
import castle_keys
import device_bridge
import remote_library
from castle_emu import CastleEmu
from test_server_fixes import JSON, _Caller, post

KEY = 'pa"ss\\#1'
NEW = "n3w-key!"
OWN = "# the owner's castles\n"
PIR = {"action": "pir", "armed": True, "cooldown": 60}
QUEUED = {"queued": True}


def key_post(action, key=""):
    return post(
        "/radio/device/key", JSON, json.dumps({"action": action, "key": key}).encode()
    )


def key_get():
    caller = _Caller(path="/radio/device/key")
    device_bridge_status_reset()
    caller.do_GET()
    return caller.answer()


def device_bridge_status_reset():
    with device_bridge._STATUS_LOCK:
        device_bridge._status_cache["state"] = None


class KeyedCastle(unittest.TestCase):
    tmp: Path
    emu: CastleEmu

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="radio-key-"))
        (cls.tmp / "card").mkdir()
        cls.emu = CastleEmu(port=0, sd_dir=cls.tmp / "card", scenes=["vigil"])
        cls.emu.start()

    @classmethod
    def tearDownClass(cls):
        cls.emu.shutdown()
        cls.emu.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.devices = self.tmp / "devices.toml"
        self.devices.write_text(OWN, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k != "CASTLE_KEY"}
        env["CASTLE_DEVICES"] = str(self.devices)
        self.host = f"127.0.0.1:{self.emu.port}"
        for patch in (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(device_bridge, "HOST", self.host),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.emu.key = KEY.encode()
        self.addCleanup(setattr, self.emu, "key", b"")
        device_bridge_status_reset()

    def test_a_refused_change_is_said_in_the_apps_words(self):
        with self.assertRaises(device_bridge.KeyRequired) as cm:
            device_bridge.command(PIR)
        self.assertEqual(str(cm.exception), castle_keys.KEY_REQUIRED)
        status, body = post("/radio/device/command", JSON, json.dumps(PIR).encode())
        self.assertEqual(
            (status, body),
            (401, {"error": castle_keys.KEY_REQUIRED, "key_required": True}),
        )
        # Reads stay open on a keyed castle: the bridge still sees it.
        self.assertTrue(device_bridge.status()["state"]["locked"])

    def test_the_settings_card_uses_sets_and_clears(self):
        self.assertEqual(
            key_get(),
            (
                200,
                {
                    "host": self.host,
                    "remembered": False,
                    "pinned": False,
                    "locked": True,
                },
            ),
        )
        status, body = key_post("use", "not-it")
        self.assertEqual((status, body["error"]), (401, castle_keys.WRONG_KEY))
        status, body = key_post("use", "two words")
        self.assertEqual((status, body["error"]), (400, castle_keys.BAD_KEY))
        self.assertEqual(self.devices.read_text(encoding="utf-8"), OWN)

        status, body = key_post("use", KEY)
        self.assertEqual((status, body["remembered"]), (200, True))
        self.assertEqual(device_bridge.command(PIR), QUEUED)

        status, body = key_post("set", NEW)
        self.assertEqual((status, self.emu.key), (200, NEW.encode()))
        self.assertEqual(castle_keys.stored_key(self.host), NEW)
        self.assertEqual(device_bridge.command({**PIR, "armed": False}), QUEUED)

        status, body = key_post("clear")
        self.assertEqual(
            (status, body["locked"], body["remembered"]), (200, False, False)
        )
        self.assertEqual(self.devices.read_text(encoding="utf-8"), OWN)
        for answer in (key_get(), key_post("use", KEY)):
            self.assertNotIn(KEY, json.dumps(answer))

    def test_a_pinned_key_is_sent_and_never_changed_from_here(self):
        with mock.patch.dict(os.environ, {"CASTLE_KEY": KEY}):
            self.assertEqual(device_bridge.command(PIR), QUEUED)
            self.assertTrue(key_get()[1]["pinned"])
            status, body = key_post("clear")
            self.assertEqual((status, body["error"]), (409, castle_keys.PINNED))
        self.assertEqual(self.emu.key, KEY.encode())

    def test_deletes_and_uploads_carry_the_key(self):
        castle_keys.remember(self.host, KEY)
        (self.tmp / "card" / "old.mp3").write_bytes(b"x")
        self.assertEqual(remote_library.delete_audio("old.mp3"), {"deleted": True})
        remote_library._JOBS["k"] = {"done": False}
        self.addCleanup(remote_library._JOBS.pop, "k")
        data = b"audio" * 1000
        remote_library.upload_with_progress("k", "/api/files", "new.mp3", data)
        self.assertEqual((self.tmp / "card" / "new.mp3").read_bytes(), data)
        castle_keys.forget(self.host)
        with self.assertRaises(device_bridge.KeyRequired):
            remote_library.upload_with_progress("k", "/api/files", "new.mp3", b"a")
        with self.assertRaises(device_bridge.KeyRequired):
            remote_library.delete_audio("new.mp3")

    def test_a_sync_to_a_locked_castle_fails_before_any_bytes_go(self):
        library = self.tmp / "library"
        library.mkdir(exist_ok=True)
        (library / "radio_x.mp3").write_bytes(b"audio")
        with self.assertRaises(device_bridge.KeyRequired):
            remote_library.start(self.tmp, library, [{"key": "radio_x"}], "radio_x")
        self.assertNotIn("radio_x", remote_library._JOBS)
        self.assertFalse((self.tmp / "card" / "radio_x.mp3").exists())


class HungUp:
    """A castle that answered 401 before the body and hung up mid-send."""

    def __init__(self, status=401):
        self.status = status

    def putrequest(self, *_a):
        pass

    def putheader(self, *_a):
        pass

    def endheaders(self):
        pass

    def send(self, _block):
        raise BrokenPipeError(32, "Broken pipe")

    def getresponse(self):
        if self.status is None:
            raise ConnectionResetError(54, "reset")
        return mock.Mock(status=self.status)

    def close(self):
        pass


class HangUpTests(unittest.TestCase):
    def setUp(self):
        remote_library._JOBS["h"] = {"done": False}
        self.addCleanup(remote_library._JOBS.pop, "h")

    def test_the_early_401_is_read_back_after_the_pipe_breaks(self):
        with self.assertRaises(device_bridge.KeyRequired):
            remote_library.upload_with_progress(
                "h", "/api/files", "a.mp3", b"a", HungUp
            )

    def test_any_other_hang_up_is_the_transport_error_it_was(self):
        for status in (413, None):
            with self.subTest(status=status), self.assertRaises(BrokenPipeError):
                remote_library.upload_with_progress(
                    "h", "/api/files", "a.mp3", b"a", lambda s=status: HungUp(s)
                )


class UnreachableTests(unittest.TestCase):
    def test_a_silent_castle_is_unknown_not_unlocked(self):
        with mock.patch.object(device_bridge, "call", side_effect=OSError("down")):
            self.assertIsNone(device_bridge.key_state()["locked"])


if __name__ == "__main__":
    unittest.main()
