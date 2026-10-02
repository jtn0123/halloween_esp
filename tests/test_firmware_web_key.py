"""The castle key and boot_play (v5.74, firmware/sd_web_prefs.h), run twice.

The real C (tests/cxx/web_check.cpp) and the emulator get the same requests
with the same X-Castle-Key header and must answer with the same bytes —
the same arrangement as tests/test_firmware_web_cxx.py, on a castle of its
own because setting a key changes every write route that follows.

What is held here is the CONTRACT an app and the castle page rely on:

  * a castle with no key is today's castle — nothing asks for anything;
  * with a key, the routes that CHANGE the castle (card writes and deletes,
    the firmware, PIR settings, these settings, the key itself) answer 401
    without it and with a wrong one, and work with it;
  * reading and running the show stay open with no header at all;
  * /api/status says `locked`, so a client knows before its first write.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

from firmware_web_harness import WebPairCase

#: Every route that a key guards, with a request that would otherwise have
#: been ACCEPTED — so a 401 is the key's doing and nothing else's.
GUARDED: tuple[tuple[str, bytes, bytes], ...] = (
    ("PUT", b"/api/files/new.mp3", b"\xff\xfb" + b"\x00" * 64),
    ("PUT", b"/api/site/new.js", b"x"),
    ("PUT", b"/api/scenes/new.cue", b"x"),
    ("DELETE", b"/api/files/wicked_winds.mp3", b""),
    ("DELETE", b"/api/site/app.js", b""),
    ("PUT", b"/api/ota", b"\xe9" + b"\x00" * 70000),
    ("POST", b"/api/pir?cooldown=30", b""),
    ("POST", b"/api/settings?boot_play=0", b""),
    ("POST", b"/api/key?clear=1", b""),
    ("POST", b"/api/factory-reset?confirm=yes", b""),
)

#: And the ones it must never guard: reading the castle and running the show.
OPEN: tuple[tuple[str, bytes], ...] = (
    ("GET", b"/api/status"),
    ("GET", b"/api/health"),
    ("GET", b"/api/files"),
    ("POST", b"/api/stop"),
    ("POST", b"/api/volume?v=40"),
    ("POST", b"/api/blackout"),
    ("POST", b"/api/play?f=wicked_winds.mp3"),
)


class TestCastleKey(WebPairCase):
    def setUp(self) -> None:
        # Each test starts from an open castle with no header, whatever the
        # last one left behind.
        self.pair.send_key(b"s3cret-key")
        self.same("POST", b"/api/key?clear=1")
        self.pair.send_key(b"")

    def status(self) -> dict[str, object]:
        c, e = self.pair.both("GET", b"/api/status")
        cj, ej = json.loads(c.body), json.loads(e.body)
        self.assertEqual(
            (cj["locked"], cj["boot_play"]), (ej["locked"], ej["boot_play"])
        )
        return dict(cj)

    def lock(self) -> None:
        r = self.same("POST", b"/api/key?new=s3cret-key")
        self.assertEqual(json.loads(r.body), {"locked": True})
        self.assertTrue(self.status()["locked"])

    def test_status_names_the_board_and_the_build(self) -> None:
        # What an updater picks a release image by — the yard's identity is
        # the default on both castles (sd_web_state.h g_board/g_fw_variant).
        c, e = self.pair.both("GET", b"/api/status")
        cj, ej = json.loads(c.body), json.loads(e.body)
        for k in ("board", "fw_variant"):
            self.assertEqual(cj[k], ej[k], k)
        self.assertEqual((cj["board"], cj["fw_variant"]), ("feather-s3-4m2p", "yard"))

    def test_an_open_castle_says_so_and_asks_for_nothing(self) -> None:
        self.assertFalse(self.status()["locked"])
        r = self.same("POST", b"/api/settings?boot_play=1")
        self.assertEqual((r.status, json.loads(r.body)), (200, {"boot_play": True}))

    def test_every_write_is_refused_without_the_key(self) -> None:
        self.lock()
        for key in (b"", b"wrong", b"s3cret-ke", b"s3cret-key2"):
            self.pair.send_key(key)
            for method, target, body in GUARDED:
                r = self.same(method, target, body)
                self.assertEqual(
                    (r.status, r.body), (401, b"castle key required"), (key, target)
                )
        self.assertTrue(self.status()["locked"])  # nothing got through
        on_c, on_e = self.pair.cards()
        self.assertIn("wicked_winds.mp3", on_c)
        self.assertEqual(on_c, on_e)

    def test_reading_and_running_the_show_stay_open(self) -> None:
        self.lock()
        for method, target in OPEN:
            # Status codes only: /api/status and /api/files carry the
            # machine's own numbers (tests/test_firmware_web_cxx.py says
            # which), and the point here is that neither castle said 401.
            c, e = self.pair.both(method, target)
            self.assertEqual((c.status, e.status), (200, 200), target)

    def test_the_right_key_opens_every_write(self) -> None:
        self.lock()
        self.pair.send_key(b"s3cret-key")
        r = self.same("POST", b"/api/settings?boot_play=0")
        self.assertEqual(json.loads(r.body), {"boot_play": False})
        self.assertFalse(self.status()["boot_play"])
        self.assertEqual(self.same("PUT", b"/api/files/k.mp3", b"\xff\xfb").status, 200)
        self.assertEqual(self.same("DELETE", b"/api/files/k.mp3").status, 200)
        r = self.same("POST", b"/api/key?clear=1")
        self.assertEqual(json.loads(r.body), {"locked": False})
        self.pair.send_key(b"")
        self.assertFalse(self.status()["locked"])
        self.same("POST", b"/api/settings?boot_play=1")

    def test_the_key_route_refuses_what_it_cannot_store(self) -> None:
        for target, why in (
            (b"/api/key", b"need new=<key> or clear=1"),
            (b"/api/key?new=a&clear=1", b"need new=<key> or clear=1"),
            (b"/api/key?clear=0", b"need new=<key> or clear=1"),
            (b"/api/key?new=has%20space", b"bad key"),
            (b"/api/key?new=" + b"k" * 65, b"bad key"),
            (b"/api/settings", b"need boot_play="),
            (b"/api/settings?boot_play=maybe", b"bad boot_play"),
        ):
            r = self.same("POST", target)
            self.assertEqual((r.status, r.body), (400, why), target)
        self.assertFalse(self.status()["locked"])
        # 64 is the ceiling, not past it.
        self.assertEqual(self.same("POST", b"/api/key?new=" + b"k" * 64).status, 200)
        self.pair.send_key(b"k" * 64)
        self.assertEqual(self.same("POST", b"/api/key?clear=1").status, 200)

    def test_a_factory_reset_forgets_the_key_and_the_settings(self) -> None:
        self.lock()
        self.pair.send_key(b"s3cret-key")
        self.same("POST", b"/api/settings?boot_play=0")
        for target in (b"/api/factory-reset", b"/api/factory-reset?confirm=1"):
            r = self.same("POST", target)
            self.assertEqual((r.status, r.body), (400, b"need confirm=yes"))
        r = self.same("POST", b"/api/factory-reset?confirm=yes")
        self.assertEqual(json.loads(r.body), {"resetting": True})
        self.pair.send_key(b"")
        st = self.status()
        self.assertEqual((st["locked"], st["boot_play"]), (False, True))


if __name__ == "__main__":
    unittest.main()
