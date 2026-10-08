"""The light desk follows the castle Find my castle chose.

Inside the desktop app the studio runs with no CASTLE_HOST at all
(runtime.rs child_env, tools/desktop_env.py) — it used to get an empty one,
which is "explicitly no castle", so the desk in the app never had one. Now
it reads the per-user store, CASTLE_DEVICES, on every call: a castle adopted
after the studio started (tools/castle_address.py, which Castle Radio's
Your castle page drives) is the desk's castle without a restart, and one
found somewhere else later replaces it the same way.
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

import castle_address
import castle_emu
from studio_rs_case import CARGO, StudioCase

IN_CI = bool(os.environ.get("CI"))


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class FollowsTheStore(StudioCase):
    HOST_UNSET = True
    DEVICES = "# the owner's castles\n"
    card: ClassVar[Path]
    emu: ClassVar[castle_emu.CastleEmu]

    @classmethod
    def setUpClass(cls) -> None:
        cls.card = Path(tempfile.mkdtemp(prefix="finds-rs-card-"))
        cls.emu = castle_emu.CastleEmu(
            port=0, sd_dir=cls.card, scenes=["vigil"], version="5.75"
        )
        cls.emu.start()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls) -> None:
        super().tearDownClass()
        cls.emu.shutdown()
        cls.emu.server_close()
        shutil.rmtree(cls.card, ignore_errors=True)

    def test_a_castle_adopted_after_start_is_the_desks(self) -> None:
        status, body = self.json("/api/status")
        self.assertEqual((status, body), (200, {"studio": True}), "no castle yet")
        here = f"127.0.0.1:{self.emu.port}"
        castle_address.adopt(here, "castle-a1b2c3.local", self.devices)
        status, body = self.json("/api/status")
        self.assertEqual((status, body.get("version")), (200, "5.75"))
        self.assertIn("# the owner's castles", self.devices.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
