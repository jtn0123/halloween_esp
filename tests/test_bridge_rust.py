"""castle-core's bridge CLI against the emulator — B4's first verbs.

The `castle` binary speaks the firmware's HTTP (sd_web.h) over a bare
socket; castle_emu is the byte-level stand-in the whole desk chain is
tested against, so a green round-trip here is the same evidence the
Python bridge gets. One emulator per class, every verb exercised, and
the failure paths (unknown scene, nobody listening) checked for their
exit codes rather than just their absence of a crash.

Skipped, not failed, without cargo — except in CI.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import castle_emu

CARGO = shutil.which("cargo")
IN_CI = bool(os.environ.get("CI"))
BIN = ROOT / "core" / "target" / "release" / "castle"


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class TestBridgeVerbs(unittest.TestCase):
    emu: ClassVar[castle_emu.CastleEmu]
    card: ClassVar[Path]

    @classmethod
    def setUpClass(cls) -> None:
        assert CARGO is not None
        built = subprocess.run(
            [
                CARGO,
                "build",
                "--release",
                "--quiet",
                "--manifest-path",
                str(ROOT / "core" / "Cargo.toml"),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        assert built.returncode == 0, built.stderr
        cls.card = Path(tempfile.mkdtemp(prefix="bridge-sd-"))
        (cls.card / "tone.mp3").write_bytes(b"\xff\xfb" + b"\0" * 3000)
        cls.emu = castle_emu.CastleEmu(
            port=0, sd_dir=cls.card, scenes=["vigil", "storm"]
        )
        cls.emu.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.emu.shutdown()
        cls.emu.server_close()
        shutil.rmtree(cls.card, ignore_errors=True)

    def castle(self, *verb: str) -> tuple[int, str]:
        r = subprocess.run(
            [str(BIN), "--host", f"127.0.0.1:{self.emu.port}", *verb],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        return r.returncode, (r.stdout or r.stderr).strip()

    def status(self) -> dict[str, object]:
        code, body = self.castle("status")
        self.assertEqual(code, 0, body)
        return dict(json.loads(body))

    def wait_status(self, key: str, value: object, timeout: float = 4.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.status().get(key) == value:
                return
            time.sleep(0.05)
        self.fail(f"status.{key} never became {value!r}")

    def test_status_answers_the_castles_own_json(self) -> None:
        s = self.status()
        self.assertIn("version", s)
        self.assertTrue(s["sd_mounted"])

    def test_health_is_the_boot_counters(self) -> None:
        code, body = self.castle("health")
        self.assertEqual(code, 0)
        self.assertIn("boots", json.loads(body))

    def test_scene_switches_and_stop_stops(self) -> None:
        code, body = self.castle("scene", "storm")
        self.assertEqual(code, 0, body)
        self.wait_status("scene", "storm")
        code, _ = self.castle("stop")
        self.assertEqual(code, 0)
        self.wait_status("scene", "")

    def test_volume_lands_clamped_by_the_rig(self) -> None:
        code, _ = self.castle("volume", "60")
        self.assertEqual(code, 0)
        self.wait_status("volume", 60)

    def test_an_unknown_scene_is_a_refusal_not_a_crash(self) -> None:
        code, body = self.castle("scene", "no_such_scene")
        self.assertEqual(code, 2, body)

    def test_nobody_listening_is_exit_one_with_the_host_named(self) -> None:
        r = subprocess.run(
            [str(BIN), "--host", "127.0.0.1:1", "status"],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("127.0.0.1:1", r.stderr)


if __name__ == "__main__":
    unittest.main()
