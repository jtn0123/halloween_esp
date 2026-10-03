"""Find my castle in Castle Radio — castle_place.py and castle_finder.py.

The radio's castle is the app's pin (CASTLE_RADIO_HOST), else the first
castle of the store the app names (CASTLE_DEVICES) — followed as the file
changes, so a castle found in the light desk is this page's too — else
nobody. Find my castle browses (the browse injected here: nothing in this
file multicasts) and remembers only what answers like a castle. Every store
is a temp file; every castle is tools/castle_emu.
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
import castle_address
import castle_find
import castle_finder
import castle_place
import device_bridge
import hosts
from castle_emu import CastleEmu
from test_server_fixes import JSON, _Caller, post

NAME = "castle-a1b2c3.local"


def answers_for(port):
    """What a castle's responder says to the browse: an `_http._tcp` SRV
    naming `NAME` on `port`, and its A record — on loopback."""
    srv = ("castle-a1b2c3._http._tcp.local", castle_find._SRV, (port, NAME))
    return [("127.0.0.1", [srv, (NAME, castle_find._A, "127.0.0.1")])]


def get(route):
    caller = _Caller(path=route)
    caller.do_GET()
    return caller.answer()


def adopt_post(host, name=""):
    body = json.dumps({"host": host, "name": name}).encode()
    return post("/radio/device/address", JSON, body)


class PlaceCase(unittest.TestCase):
    tmp: Path
    emu: CastleEmu
    addr: str

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="radio-find-"))
        (cls.tmp / "card").mkdir()
        cls.emu = CastleEmu(port=0, sd_dir=cls.tmp / "card", scenes=["vigil"])
        cls.emu.start()
        cls.addr = f"127.0.0.1:{cls.emu.port}"

    @classmethod
    def tearDownClass(cls):
        cls.emu.shutdown()
        cls.emu.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.store = self.tmp / "devices.toml"
        self.store.write_text("", encoding="utf-8")
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("CASTLE_RADIO_HOST", "CASTLE_KEY")
        }
        env["CASTLE_DEVICES"] = str(self.store)
        for patch in (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(device_bridge, "HOST", ""),
            mock.patch.dict(castle_place._last, {"host": None, "stamp": None}),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.reset()

    def reset(self):
        with device_bridge._STATUS_LOCK:
            device_bridge._status_cache["state"] = None

    def start(self):
        """HOST as the bridge sets it at start-up, in this environment."""
        device_bridge.HOST = castle_place.initial()
        return device_bridge.HOST


class TheRule(PlaceCase):
    def test_the_pin_else_the_named_store_else_nobody(self):
        self.store.write_text('[c]\nhost = "192.168.1.30"\n', encoding="utf-8")
        self.assertEqual(self.start(), "192.168.1.30")
        with mock.patch.dict(os.environ, {"CASTLE_RADIO_HOST": "castle.local"}):
            self.assertEqual(self.start(), "castle.local")
        # Unnamed, the store would be the checkout's tracked devices.toml —
        # the seller's yard (docs/PRODUCTION-TODO.md §8). Not this app's.
        with mock.patch.dict(os.environ):
            del os.environ["CASTLE_DEVICES"]
            self.assertTrue(hosts.first_castle(), "the checkout knows a castle")
            self.assertEqual(self.start(), "")
            with self.assertRaisesRegex(OSError, "No castle found yet"):
                device_bridge.castle()

    def test_the_bridge_follows_the_store_a_studio_writes(self):
        self.assertEqual(self.start(), "")
        with self.assertRaisesRegex(OSError, "Find my castle"):
            device_bridge.castle()
        # The light desk (or a second Castle Radio) found it: same file.
        castle_address.adopt(self.addr, NAME, self.store)
        self.assertEqual(device_bridge.castle(), self.addr)
        self.assertTrue(device_bridge.status()["connected"])
        # An address set any other way — a pin, a test — is never overruled.
        device_bridge.HOST = "10.9.9.9"
        castle_address.adopt("10.9.9.20", "", self.store)
        self.assertEqual(device_bridge.castle(), "10.9.9.9")


class TheRoutes(PlaceCase):
    def test_find_then_adopt_then_the_castle_is_current(self):
        self.start()
        self.assertEqual(
            get("/radio/device/address"),
            (200, {"host": "", "pinned": False, "store": True}),
        )
        with mock.patch.object(
            castle_find, "browse", return_value=answers_for(self.emu.port)
        ):
            status, found = post("/radio/device/find", JSON, b"{}")
            self.assertEqual(status, 200)
            [castle] = found["found"]
            self.assertEqual(
                (castle["name"], castle["address"], castle["current"]),
                (NAME, self.addr, False),
            )
            status, body = adopt_post(castle["address"], castle["name"])
            self.assertEqual(status, 200, body)
            self.assertEqual((body["host"], body["version"]), (self.addr, "5.40"))
            self.assertEqual(hosts.first_castle(self.store), [self.addr, NAME])
            self.assertEqual(device_bridge.HOST, self.addr)
            status, again = post("/radio/device/find", JSON, b"{}")
            self.assertTrue(again["found"][0]["current"])
        self.assertTrue(device_bridge.status()["connected"])

    def test_what_is_not_a_castle_is_said_and_never_saved(self):
        self.start()
        cases = [
            ("127.0.0.1:1", 502, "answered as a castle"),
            ("a b", 400, "not a castle address"),
            ("http://x/", 400, "not a castle address"),
        ]
        for host, code, said in cases:
            with self.subTest(host=host):
                status, body = adopt_post(host)
                self.assertEqual(status, code)
                self.assertIn(said, body["error"])
        self.assertEqual(self.store.read_text(encoding="utf-8"), "")
        with mock.patch.dict(os.environ, {"CASTLE_RADIO_HOST": "castle.local"}):
            status, body = adopt_post(self.addr)
            self.assertEqual((status, body["error"]), (409, castle_place.PINNED))
        with mock.patch.dict(os.environ):
            del os.environ["CASTLE_DEVICES"]
            status, body = adopt_post(self.addr)
            self.assertEqual((status, body["error"]), (409, castle_place.NO_STORE))
        self.assertEqual(self.store.read_text(encoding="utf-8"), "")
        self.assertEqual(device_bridge.HOST, "")

    def test_no_network_is_said_and_a_browse_is_never_a_get(self):
        with mock.patch.object(
            castle_find, "find", side_effect=OSError("could not ask the network: x")
        ):
            status, body = post("/radio/device/find", JSON, b"{}")
        self.assertEqual((status, body["error"]), (502, "could not ask the network: x"))
        caller = _Caller(path="/radio/device/find")
        with mock.patch.object(castle_find, "find") as find:
            caller.do_GET()
        find.assert_not_called()
        self.assertEqual(caller.errored, 404)
        # Form-shaped posts never start one either (request_guard).
        status, _ = post("/radio/device/find", {"Content-Type": "text/plain"}, b"{}")
        self.assertEqual(status, 415)

    def test_the_routes_are_the_pages(self):
        for route in ("/radio/device/find", "/radio/device/address"):
            self.assertIn(route, castle_finder.POST_ROUTES)
        page = (Path(__file__).parent / "castle-find.js").read_text(encoding="utf-8")
        for route in castle_finder.POST_ROUTES:
            self.assertIn(f"'{route}'", page)


if __name__ == "__main__":
    unittest.main()
