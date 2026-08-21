"""The emulated castle, held to the firmware's contract.

Two audiences. First the emulator itself: every validation rule here is
copied from firmware/sd_web.h, and these tests pin the copy to the original
(the dogfood fuzz findings — digits-only volume, unknown-scene 404, safe
filenames — must fail the same way on both). Second the bridge: castle_link
pointed at the emulator by CASTLE_HOST proves the studio's relay leg without
a device on the bench.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import castle_emu
import castle_link


def _wait(cond, timeout_s: float = 2.0) -> bool:
    """Queued actions apply on the emulator's tick, not on the reply."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


class EmuCase(unittest.TestCase):
    emu: castle_emu.CastleEmu
    card: Path
    base: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.card = Path(tempfile.mkdtemp(prefix="emu-test-sd-"))
        (cls.card / "wicked_winds.mp3").write_bytes(b"\xff\xfb" + b"\0" * 4000)
        cls.emu = castle_emu.CastleEmu(port=0, sd_dir=cls.card,
                                       scenes=["vigil", "storm"])
        cls.emu.start()
        cls.base = f"http://127.0.0.1:{cls.emu.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.emu.shutdown()

    def http(self, method: str, path: str,
             body: bytes = b"") -> tuple[int, bytes]:
        req = urllib.request.Request(self.base + path, data=body or None,
                                     method=method)
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def status(self) -> dict:
        return json.loads(self.http("GET", "/api/status")[1])


class TestStatusShape(EmuCase):
    def test_has_every_key_the_desk_reads(self) -> None:
        """device.ts, device_panel.ts and castle_link between them read all
        of these; a missing one renders as a lie (dogfood ISSUE-001)."""
        st = self.status()
        for key in ("version", "uptime_s", "sd_mounted", "psram_free_kb",
                    "heap_free_kb", "volume", "scene", "track", "show_on",
                    "pir"):
            self.assertIn(key, st)
        self.assertEqual(st["sd_mounted"], True)
        for key in ("armed", "cooldown_s", "scene"):
            self.assertIn(key, st["pir"])


class TestValidationParity(EmuCase):
    """The fuzz findings, pinned. Same inputs, same verdicts as sd_web.h."""

    def test_volume_rejects_garbage(self) -> None:
        for bad in ("abc", "50;reboot", "-1", "101", "", "0x20"):
            code, body = self.http("POST", f"/api/volume?v={bad}")
            self.assertEqual(code, 400, f"v={bad!r} must 400")
            self.assertIn(b"need ?v=0..100", body)

    def test_volume_accepts_the_range(self) -> None:
        for ok in ("0", "70", "100"):
            self.assertEqual(self.http("POST", f"/api/volume?v={ok}")[0], 200)

    def test_unknown_scene_is_404_not_queued(self) -> None:
        code, body = self.http("POST", "/api/scene?s=doesnotexist")
        self.assertEqual(code, 404)
        self.assertIn(b"unknown scene", body)

    def test_traversal_names_are_rejected(self) -> None:
        for bad in ("..%2F..%2Fetc", ".hidden", "a%2Fb"):
            self.assertEqual(self.http("PUT", f"/api/files/{bad}", b"x")[0],
                             400, f"{bad} must be rejected")
        self.assertEqual(self.http("POST", "/api/play?f=..%2Fconfig")[0], 400)


class TestQueuedSemantics(EmuCase):
    def test_scene_applies_after_the_tick_not_before(self) -> None:
        code, body = self.http("POST", "/api/scene?s=storm")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"queued": True})
        self.assertTrue(_wait(lambda: self.status()["scene"] == "storm"),
                        "queued scene never applied")
        self.http("POST", "/api/stop")
        self.assertTrue(_wait(lambda: self.status()["scene"] == ""))

    def test_play_sets_the_track_and_stop_clears_it(self) -> None:
        self.http("POST", "/api/play?f=wicked_winds.mp3")
        self.assertTrue(
            _wait(lambda: self.status()["track"] == "wicked_winds.mp3"))
        self.http("POST", "/api/stop")
        self.assertTrue(_wait(lambda: self.status()["track"] == ""))


class TestCardRoundTrip(EmuCase):
    def test_upload_list_fetch_delete(self) -> None:
        code, body = self.http("PUT", "/api/files/e2e_song.mp3", b"\xff\xfbdata")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["bytes"], 6)

        files = json.loads(self.http("GET", "/api/files")[1])
        names = {f["name"] for f in files if not f["dir"]}
        self.assertIn("e2e_song.mp3", names)

        self.assertEqual(self.http("GET", "/sd/e2e_song.mp3")[1],
                         b"\xff\xfbdata")

        self.assertEqual(self.http("DELETE", "/api/files/e2e_song.mp3")[0], 200)
        self.assertEqual(self.http("DELETE", "/api/files/e2e_song.mp3")[0], 404)


class TestBridge(EmuCase):
    """castle_link → emulator: the studio's relay leg, no hardware."""

    def setUp(self) -> None:
        self._env = castle_link.os.environ.get("CASTLE_HOST")
        castle_link.os.environ["CASTLE_HOST"] = f"127.0.0.1:{self.emu.port}"
        castle_link._cache.clear()

    def tearDown(self) -> None:
        if self._env is None:
            castle_link.os.environ.pop("CASTLE_HOST", None)
        else:
            castle_link.os.environ["CASTLE_HOST"] = self._env
        castle_link._cache.clear()

    def test_status_arrives_bridged(self) -> None:
        st = castle_link.status()
        self.assertIsNotNone(st)
        assert st is not None
        self.assertEqual(st["version"], "5.22")
        self.assertEqual(st["bridged"], f"127.0.0.1:{self.emu.port}")

    def test_forward_relays_verdicts_verbatim(self) -> None:
        code, _, _ = castle_link.forward("POST", "/api/volume?v=40")
        self.assertEqual(code, 200)
        code, body, _ = castle_link.forward("POST", "/api/volume?v=abc")
        self.assertEqual(code, 400)
        self.assertIn(b"need ?v=0..100", body)
        code, _, _ = castle_link.forward("POST", "/api/scene?s=nope")
        self.assertEqual(code, 404)

    def test_forward_uploads_a_file(self) -> None:
        code, body, _ = castle_link.forward(
            "PUT", "/api/files/bridged.mp3", b"\xff\xfbbridged")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["bytes"], 9)
        self.assertEqual((self.card / "bridged.mp3").read_bytes(),
                         b"\xff\xfbbridged")
        (self.card / "bridged.mp3").unlink()


if __name__ == "__main__":
    unittest.main()
