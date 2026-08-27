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
import zlib
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

    def test_show_start_and_stop_flip_the_status_flag(self) -> None:
        code, _ = self.castle("show", "start")
        self.assertEqual(code, 0)
        self.wait_status("show_on", True)
        code, _ = self.castle("show", "stop")
        self.assertEqual(code, 0)
        self.wait_status("show_on", False)

    def test_blackout_answers(self) -> None:
        code, body = self.castle("blackout")
        self.assertEqual(code, 0, body)

    def test_files_lists_the_card(self) -> None:
        code, body = self.castle("files")
        self.assertEqual(code, 0, body)
        names = [f["name"] for f in json.loads(body)]  # a bare array, like the card
        self.assertIn("tone.mp3", names)

    def test_bootlog_answers_text(self) -> None:
        code, body = self.castle("bootlog")
        self.assertEqual(code, 0, body)

    def test_put_lands_the_bytes_and_the_castle_proves_it(self) -> None:
        """The upload verbs carry sd_sync.upload's whole contract: the byte
        count must match, and the v5.42 crc32 field must agree with a CRC
        computed over what was sent (the emulator hashes what it wrote)."""
        src = self.card / "local_march.mp3"
        payload = bytes(range(256)) * 37  # not compressible, not trivial
        src.write_bytes(payload)
        code, body = self.castle("put", str(src), "march.mp3")
        self.assertEqual(code, 0, body)
        reply = json.loads(body)
        self.assertEqual(reply["bytes"], len(payload))
        self.assertEqual(int(str(reply["crc32"]), 16), zlib.crc32(payload))
        self.assertEqual((self.card / "march.mp3").read_bytes(), payload)

    def test_put_defaults_the_remote_name_to_the_basename(self) -> None:
        src = self.card / "dirge.mp3"
        src.write_bytes(b"\xff\xfb" + b"\x11" * 500)
        code, body = self.castle("put", str(src))
        self.assertEqual(code, 0, body)
        self.assertIn("/sd/dirge.mp3", body)

    def test_put_of_a_refused_name_is_exit_two(self) -> None:
        src = self.card / "innocent.mp3"
        src.write_bytes(b"x" * 64)
        code, body = self.castle("put", str(src), ".hidden")
        self.assertEqual(code, 2, body)
        self.assertIn("refused", body)

    def test_put_to_scenes_lands_in_the_subdirectory(self) -> None:
        src = self.card / "waltz_src.mp3"
        src.write_bytes(b"\xff\xfb" + b"\x22" * 700)
        code, body = self.castle("put", "--to", "scenes", str(src), "waltz.mp3")
        self.assertEqual(code, 0, body)
        self.assertEqual(
            (self.card / "scenes" / "waltz.mp3").read_bytes(), src.read_bytes()
        )

    def test_purge_clears_root_files_but_leaves_directories(self) -> None:
        """sd_sync's rule, ported: purge means "clear the music", so the
        site/ and scenes/ trees survive it."""
        (self.card / "doomed_a.mp3").write_bytes(b"a" * 32)
        (self.card / "doomed_b.mp3").write_bytes(b"b" * 32)
        keep = self.card / "site"
        keep.mkdir(exist_ok=True)
        (keep / "index.html").write_text("kept")
        code, body = self.castle("purge")
        self.assertEqual(code, 0, body)
        self.assertIn("deleted doomed_a.mp3", body)
        self.assertFalse((self.card / "doomed_a.mp3").exists())
        self.assertFalse((self.card / "doomed_b.mp3").exists())
        self.assertEqual((keep / "index.html").read_text(), "kept")
        code, body = self.castle("purge")
        self.assertEqual(code, 0, body)
        self.assertIn("no files", body)

    def test_rm_deletes_and_a_second_rm_is_exit_two(self) -> None:
        (self.card / "victim.mp3").write_bytes(b"x" * 64)
        code, body = self.castle("rm", "victim.mp3")
        self.assertEqual(code, 0, body)
        self.assertFalse((self.card / "victim.mp3").exists())
        code, body = self.castle("rm", "victim.mp3")
        self.assertEqual(code, 2, body)

    def test_ota_flashes_a_plausible_image_and_sees_the_device_return(self) -> None:
        """The reboot race is normal (the device restarts moments after the
        last byte), so the verb's verdict is the status poll — which the
        emulator answers immediately, keeping this test quick."""
        img = self.card / "firmware.bin"
        img.write_bytes(b"\xe9" + b"\x00" * 70000)
        r = subprocess.run(
            [str(BIN), "--host", f"127.0.0.1:{self.emu.port}", "ota", str(img)],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env={**os.environ, "CASTLE_OTA_WAIT_S": "5"},
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("flashed", r.stdout)
        self.assertIn("up — v", r.stdout)
        self.assertIn("CONFIRM", r.stdout)

    def test_ota_refuses_a_file_without_the_magic_before_sending(self) -> None:
        img = self.card / "not_firmware.bin"
        img.write_bytes(b"\x7fELF" + b"\x00" * 70000)
        code, body = self.castle("ota", str(img))
        self.assertEqual(code, 1, body)
        self.assertIn("0xE9", body)

    def test_ota_of_an_implausible_size_is_the_castles_refusal(self) -> None:
        img = self.card / "tiny.bin"
        img.write_bytes(b"\xe9" + b"\x00" * 100)  # under OTA_MIN
        code, body = self.castle("ota", str(img))
        self.assertEqual(code, 2, body)
        self.assertIn("refused the image", body)

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
