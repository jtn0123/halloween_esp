"""The castle key, end to end: desk route → studio → store → relay → castle.

Firmware v5.74 refuses a keyed castle's writes without the key; the
emulator refuses them the same way (tools/castle_emu_http.py, held to the
firmware by tests/test_firmware_contract.py). This drives the real studio
bin at a keyed emulated castle and walks the owner's whole story:

  a write without the key is a 401 the desk can word; a wrong key offered
  through /studio/castle-key is refused and NOT remembered; the right one is
  remembered (in the sandbox devices.toml, never the repo's) and the same
  write now lands; a new key is set on the castle and followed by the store;
  clearing it opens the castle and leaves the inventory as it was.

And the things that must never happen: the key in a reply, in the studio's
log, or in the store under any table but the castle's own. Which key the
relay sends for which host is tests/test_castle_key_rust.py's job.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import castle_emu
import hosts
from studio_rs_case import CARGO, StudioCase

IN_CI = bool(os.environ.get("CI"))
KEY = 'pa"ss\\#1'
NEW = "n3w-key!"


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class KeyedCastle(StudioCase):
    card: ClassVar[Path]
    emu: ClassVar[castle_emu.CastleEmu]

    @classmethod
    def setUpClass(cls) -> None:
        cls.card = Path(tempfile.mkdtemp(prefix="key-rs-card-"))
        cls.emu = castle_emu.CastleEmu(port=0, sd_dir=cls.card, scenes=["vigil"])
        cls.emu.start()
        cls.HOST_ENV = f"127.0.0.1:{cls.emu.port}"
        # The inventory the owner already had: a castle elsewhere, whose
        # table and comments must come through every edit untouched.
        cls.DEVICES = '# my castles\n[porch]\nhost = "10.9.9.9"   # the yard\n'
        super().setUpClass()

    @classmethod
    def tearDownClass(cls) -> None:
        super().tearDownClass()
        cls.emu.shutdown()
        cls.emu.server_close()
        shutil.rmtree(cls.card, ignore_errors=True)

    def key_route(self, action: str, key: str = "") -> tuple[int, dict[str, object]]:
        return self.json("/studio/castle-key", "POST", {"action": action, "key": key})

    def stored(self) -> str:
        return hosts.stored_key(self.HOST_ENV, self.devices)

    def assert_never_said(self, *secrets: str) -> None:
        text = self.devices.read_text(encoding="utf-8")
        for name in ("porch", "my castles", "the yard"):
            self.assertIn(name, text, "the owner's own inventory was rewritten")
        _, _, page = self.req("/studio/castle-key")
        for s in secrets:
            self.assertNotIn(s.encode(), page)

    def test_the_owners_whole_story(self) -> None:
        self.emu.key = KEY.encode()
        # Locked, and nothing remembered: the write is refused by the castle.
        code, _, body = self.req("/api/pir?armed=1", "POST")
        self.assertEqual((code, body.strip()), (401, b"castle key required"))
        code, st = self.json("/studio/castle-key")
        self.assertEqual(
            (code, st["host"], st["remembered"], st["pinned"]),
            (200, self.HOST_ENV, False, False),
        )

        # A wrong key is refused and not remembered; a malformed one never
        # reaches the castle; neither is echoed.
        code, r = self.key_route("use", "not-it")
        self.assertEqual((code, r["error"]), (401, "that is not this castle's key"))
        code, r = self.key_route("use", "two words")
        self.assertEqual(code, 400)
        self.assertNotIn("two words", str(r))
        self.assertEqual(self.stored(), "")

        # The right one: remembered, and the very next write lands.
        code, r = self.key_route("use", KEY)
        self.assertEqual((code, r["remembered"]), (200, True))
        self.assertNotIn(KEY, str(r))
        self.assertEqual(self.stored(), KEY)
        code, _, _ = self.req("/api/pir?armed=1", "POST")
        self.assertEqual(code, 200)

        # A new key: the castle takes it with the old one, the store follows.
        code, r = self.key_route("set", NEW)
        self.assertEqual(code, 200, r)
        self.assertEqual((self.emu.key, self.stored()), (NEW.encode(), NEW))
        self.assertEqual(self.req("/api/pir?armed=0", "POST")[0], 200)
        self.assert_never_said(KEY, NEW)

        # Cleared: the castle is open and the store holds nothing — not even
        # the table it had to add for a castle the inventory did not name.
        code, r = self.key_route("clear")
        self.assertEqual((code, r["remembered"]), (200, False))
        self.assertEqual(self.emu.key, b"")
        self.assertEqual(self.devices.read_text(encoding="utf-8"), self.DEVICES)
        self.assertEqual(self.req("/api/pir?armed=1", "POST")[0], 200)

    def test_a_key_set_elsewhere_is_said_in_the_desks_words(self) -> None:
        """The castle's key changed behind the studio's back (another
        computer, the castle's own page): set/clear answer the sentence the
        desk shows, and nothing is remembered."""
        self.emu.key = b"someone-elses"
        try:
            code, r = self.key_route("clear")
            self.assertEqual(
                (code, r["error"]),
                (401, "This castle has a key — enter it in Settings"),
            )
            code, r = self.key_route("set", NEW)
            self.assertEqual(code, 401)
            self.assertEqual(self.emu.key, b"someone-elses")
            self.assertEqual(self.stored(), "")
        finally:
            self.emu.key = b""

    def test_unknown_actions_and_bodies_are_the_callers_mistake(self) -> None:
        self.assertEqual(self.key_route("reveal")[0], 400)
        code, _, _ = self.req(
            "/studio/castle-key", "POST", {"Content-Type": "application/json"}, b"[1]"
        )
        self.assertEqual(code, 400)


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class CastleLess(StudioCase):
    """No castle configured: there is nothing to hold a key for."""

    def test_the_route_says_so(self) -> None:
        code, r = self.json("/studio/castle-key")
        self.assertEqual((code, r["error"]), (409, "no castle configured"))
        code, r = self.json("/studio/castle-key", "POST", {"action": "use", "key": "k"})
        self.assertEqual(code, 409)


if __name__ == "__main__":
    unittest.main()
