"""The emulated castle, held to the firmware's contract.

Two audiences. First the emulator itself: every validation rule here is
copied from firmware/sd_web.h, and these tests pin the copy to the original
(the dogfood fuzz findings — digits-only volume, unknown-scene 404, safe
filenames — must fail the same way on both). Second the bridge: castle_link
pointed at the emulator by CASTLE_HOST proves the studio's relay leg without
a device on the bench.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest
import unittest.mock
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # studio_case

import castle_emu
import castle_emu_wire as wire
import castle_link
from studio_case import HostEnv


def _wait(cond: Callable[[], object], timeout_s: float = 2.0) -> bool:
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
        cls.emu = castle_emu.CastleEmu(
            port=0, sd_dir=cls.card, scenes=["vigil", "storm"]
        )
        cls.emu.start()
        cls.base = f"http://127.0.0.1:{cls.emu.port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.emu.shutdown()

    def http(self, method: str, path: str, body: bytes = b"") -> tuple[int, bytes]:
        req = urllib.request.Request(self.base + path, data=body or None, method=method)
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read()

    def status(self) -> dict[str, Any]:
        return dict(json.loads(self.http("GET", "/api/status")[1]))


class TestStatusShape(EmuCase):
    def test_has_every_key_the_desk_reads(self) -> None:
        """device.ts, device_panel.ts and castle_link between them read all
        of these; a missing one renders as a lie (dogfood ISSUE-001)."""
        st = self.status()
        for key in (
            "version",
            "uptime_s",
            "sd_mounted",
            "psram_free_kb",
            "heap_free_kb",
            "sd_total_kb",
            "sd_free_kb",
            "volume",
            "scene",
            "track",
            "show_on",
            "pir",
        ):
            self.assertIn(key, st)
        self.assertTrue(st["sd_mounted"])
        self.assertGreater(st["sd_free_kb"], 0)  # v5.23: the card reports room
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

    def test_volume_is_clamped_to_the_ceiling(self) -> None:
        """v5.36: castle_sd.yaml never asks the amps past kMaxVolumePct (static
        above 80 on the porch); the emulator must land on the same number."""
        self.assertEqual(self.http("POST", "/api/volume?v=100")[0], 200)
        time.sleep(castle_emu.APPLY_DELAY_S * 2)
        self.assertEqual(self.status()["volume"], castle_emu.MAX_VOLUME_PCT)

    def test_ceiling_matches_scenes_yaml(self) -> None:
        """The one number lives in scenes.yaml (hardware.audio.max_volume);
        rig.h gets it generated, the emulator pins it — hold them equal."""
        import yaml

        doc = yaml.safe_load(
            (
                Path(__file__).resolve().parent.parent / "scenes" / "scenes.yaml"
            ).read_text()
        )
        self.assertEqual(
            round(doc["hardware"]["audio"]["max_volume"] * 100),
            castle_emu.MAX_VOLUME_PCT,
        )

    def test_unknown_scene_is_404_not_queued(self) -> None:
        code, body = self.http("POST", "/api/scene?s=doesnotexist")
        self.assertEqual(code, 404)
        self.assertIn(b"unknown scene", body)

    def test_traversal_names_are_rejected(self) -> None:
        for bad in ("..%2F..%2Fetc", ".hidden", "a%2Fb"):
            self.assertEqual(
                self.http("PUT", f"/api/files/{bad}", b"x")[0],
                400,
                f"{bad} must be rejected",
            )
        self.assertEqual(self.http("POST", "/api/play?f=..%2Fconfig")[0], 400)


class TestQueuedSemantics(EmuCase):
    def test_scene_applies_after_the_tick_not_before(self) -> None:
        code, body = self.http("POST", "/api/scene?s=storm")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"queued": True})
        self.assertTrue(
            _wait(lambda: self.status()["scene"] == "storm"),
            "queued scene never applied",
        )
        self.http("POST", "/api/stop")
        self.assertTrue(_wait(lambda: self.status()["scene"] == ""))

    def test_play_sets_the_track_and_stop_clears_it(self) -> None:
        self.http("POST", "/api/play?f=wicked_winds.mp3")
        self.assertTrue(_wait(lambda: self.status()["track"] == "wicked_winds.mp3"))
        self.http("POST", "/api/stop")
        self.assertTrue(_wait(lambda: self.status()["track"] == ""))


class TestShowNightRoutes(EmuCase):
    def test_blackout_is_bookmarkable(self) -> None:
        """sd_web.h registers /api/blackout for GET as well as POST."""
        self.http("POST", "/api/scene?s=storm")
        self.assertTrue(_wait(lambda: self.status()["scene"] == "storm"))
        code, body = self.http("GET", "/api/blackout")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"queued": True})
        self.assertTrue(_wait(lambda: self.status()["scene"] == ""))


class TestRemotePage(EmuCase):
    def test_remote_is_the_firmware_page_byte_for_byte(self) -> None:
        """sd_web_remote.h embeds the phone remote in flash; the emulator
        serves the same bytes, lifted from the C raw string, so the relay
        and an e2e can exercise the real page (JB2-6)."""
        code, body = self.http("GET", "/remote")
        self.assertEqual(code, 200)
        page = body.decode()
        header = (
            Path(__file__).resolve().parent.parent / "firmware" / "sd_web_remote.h"
        ).read_text()
        start = header.index('R"HTML(') + len('R"HTML(')
        self.assertEqual(page, header[start : header.index(')HTML"', start)])
        for needle in (
            "<title>Castle Remote</title>",
            "id=show",
            "id=ambient",
            "id=scare",
            "id=black",
            "/api/status",
            # v5.38: the speaker strip — level, the test sweep, quiet
            "id=spk",
            "/api/volume?v=80",
            "test_sweep.mp3",
            "/api/stop",
        ):
            self.assertIn(needle, page)


class TestSceneSeeding(unittest.TestCase):
    """The emulator knows the SAME scene ids the firmware would: the show's
    scenes.yaml, not a hard-coded four (pass-1 J1-10 — five of nine desk
    picks 404ed on the bench)."""

    def test_ids_come_from_a_scenes_yaml(self) -> None:
        tmp = Path(tempfile.mkdtemp()) / "scenes.yaml"
        tmp.write_text("scenes:\n  - id: seance\n  - id: crypt\n")
        self.assertEqual(castle_emu.show_scene_ids(tmp), ["seance", "crypt", "stop"])

    def test_castle_scenes_env_is_honoured(self) -> None:
        tmp = Path(tempfile.mkdtemp()) / "scenes.yaml"
        tmp.write_text("scenes:\n  - id: only_this\n")
        with unittest.mock.patch.dict(os.environ, {"CASTLE_SCENES": str(tmp)}):
            emu = castle_emu.CastleEmu(port=0)
        self.addCleanup(emu.server_close)
        self.assertEqual(emu.scenes, ["only_this", "stop"])

    def test_an_empty_show_falls_back_too(self) -> None:
        """'scenes:' with nothing under it is what the e2e sandbox writes."""
        tmp = Path(tempfile.mkdtemp()) / "scenes.yaml"
        tmp.write_text("scenes:\n")
        self.assertIsNone(castle_emu.show_scene_ids(tmp))

    def test_unreadable_show_falls_back_to_the_defaults(self) -> None:
        self.assertIsNone(castle_emu.show_scene_ids(Path("/no/such/scenes.yaml")))
        with unittest.mock.patch.dict(
            os.environ, {"CASTLE_SCENES": "/no/such/scenes.yaml"}
        ):
            emu = castle_emu.CastleEmu(port=0)
        self.addCleanup(emu.server_close)
        self.assertEqual(emu.scenes, castle_emu.DEFAULT_SCENES)

    def test_the_repo_show_seeds_the_default_emulator(self) -> None:
        ids = castle_emu.show_scene_ids()
        assert ids is not None
        self.assertIn("vigil", ids)
        self.assertGreater(len(ids), 5)


class TestCardRoundTrip(EmuCase):
    def test_upload_list_fetch_delete(self) -> None:
        code, body = self.http("PUT", "/api/files/e2e_song.mp3", b"\xff\xfbdata")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["bytes"], 6)

        files = json.loads(self.http("GET", "/api/files")[1])
        names = {f["name"] for f in files if not f["dir"]}
        self.assertIn("e2e_song.mp3", names)

        self.assertEqual(self.http("GET", "/sd/e2e_song.mp3")[1], b"\xff\xfbdata")

        self.assertEqual(self.http("DELETE", "/api/files/e2e_song.mp3")[0], 200)
        self.assertEqual(self.http("DELETE", "/api/files/e2e_song.mp3")[0], 404)


class TestAtomicUpload(EmuCase):
    """sd_web.h write_body: `<name>.part`, then unlink + rename. The
    studio side got this in 3ccdd8b; the card side in v5.27 (B1)."""

    def test_a_short_upload_leaves_the_previous_copy_intact(self) -> None:
        good = b"\xff\xfb" + b"G" * 3000
        code, _ = self.http("PUT", "/api/files/keep.mp3", good)
        self.assertEqual(code, 200)
        # Declare 5000 bytes, send 1000, hang up: the board's recv times out.
        c = http.client.HTTPConnection("127.0.0.1", self.emu.port, timeout=10)
        c.putrequest("PUT", "/api/files/keep.mp3")
        c.putheader("Content-Length", "5000")
        c.endheaders()
        c.send(b"\xff\xfb" + b"B" * 998)
        c.sock.shutdown(1)
        r = c.getresponse()
        self.assertEqual((r.status, r.read()), (500, b"short write"))
        c.close()
        self.assertEqual((self.card / "keep.mp3").read_bytes(), good)
        self.assertFalse((self.card / "keep.mp3.part").exists())
        self.assertEqual(self.http("GET", "/sd/keep.mp3")[1], good)

    def test_a_full_upload_replaces_the_old_copy(self) -> None:
        self.http("PUT", "/api/files/swap.mp3", b"\xff\xfbold")
        code, body = self.http("PUT", "/api/files/swap.mp3", b"\xff\xfbnewer")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["bytes"], 7)
        self.assertEqual((self.card / "swap.mp3").read_bytes(), b"\xff\xfbnewer")
        self.assertFalse((self.card / "swap.mp3.part").exists())


class TestJsonEscaping(EmuCase):
    """sd_web.h json_escape + h_list's skip (B3): names the Mac put on the
    card without going through PUT, and a '"' in status strings, must not
    break the parse for every client."""

    def test_a_quoted_name_placed_on_the_card_does_not_break_the_list(self) -> None:
        (self.card / 'say "boo".mp3').write_bytes(b"x")
        (self.card / "back\\slash.mp3").write_bytes(b"y")
        (self.card / "plain.mp3").write_bytes(b"z")
        try:
            code, out = self.http("GET", "/api/files")
            self.assertEqual(code, 200)
            files = json.loads(out)  # the whole point: it parses
            names = [f["name"] for f in files if "name" in f]
            self.assertIn("plain.mp3", names)
            self.assertNotIn('say "boo".mp3', names)
            self.assertNotIn("back\\slash.mp3", names)
            self.assertEqual([f for f in files if "skipped" in f], [{"skipped": 2}])
        finally:
            (self.card / 'say "boo".mp3').unlink()
            (self.card / "back\\slash.mp3").unlink()
            (self.card / "plain.mp3").unlink()
        files = json.loads(self.http("GET", "/api/files")[1])
        self.assertFalse(any("skipped" in f for f in files))  # none → no trailer

    def test_status_escapes_a_quote_in_missing_and_track(self) -> None:
        self.emu.missing = 'a"b.mp3,c\\d.mp3'
        with self.emu.state.lock:
            self.emu.state.track = 'say "boo".mp3'
        try:
            st = self.status()
        finally:
            self.emu.missing = ""
            with self.emu.state.lock:
                self.emu.state.track = ""
        self.assertEqual(st["missing"], 'a"b.mp3,c\\d.mp3')
        self.assertEqual(st["track"], 'say "boo".mp3')

    def test_the_escape_table_is_the_firmwares(self) -> None:
        """Read the firmware's json_escape: every `case` it handles is one
        json.dumps short-escapes, and the fallback is \\u%04x below 0x20.
        (It lives in sd_web_util.h since the v5.42 helper-layer split.)"""
        src = (
            Path(__file__).resolve().parent.parent / "firmware" / "sd_web_util.h"
        ).read_text()
        body = src[src.index("inline std::string json_escape") :]
        body = body[: body.index("\n}\n")]
        cases = set(re.findall(r"case '(\\?.)': out \+= \"(\\\\.+?)\"; break;", body))
        self.assertEqual(
            cases,
            {
                ('"', '\\\\\\"'),
                ("\\\\", "\\\\\\\\"),
                ("\\n", "\\\\n"),
                ("\\r", "\\\\r"),
                ("\\t", "\\\\t"),
                ("\\b", "\\\\b"),
                ("\\f", "\\\\f"),
            },
        )
        self.assertIn("if (c < 0x20)", body)
        self.assertIn('"\\\\u%04x"', body)
        # and the Python half really is json.dumps' table for those bytes
        for ch in '"\\\n\r\t\b\f\x01\x1f\x7fé':
            self.assertEqual(json.loads('"' + wire.json_escape(ch) + '"'), ch)
        self.assertEqual(
            wire.json_escape("\x7f"), "\x7f"
        )  # DEL passes raw, as in the C


class TestBridge(HostEnv, EmuCase):
    """castle_link → emulator: the studio's relay leg, no hardware."""

    def setUp(self) -> None:
        self.host_env(f"127.0.0.1:{self.emu.port}")
        castle_link._cache.clear()
        self.addCleanup(castle_link._cache.clear)

    def test_status_arrives_bridged(self) -> None:
        st = castle_link.status()
        self.assertIsNotNone(st)
        assert st is not None
        self.assertEqual(st["version"], self.emu.version)
        self.assertEqual(st["bridged"], f"127.0.0.1:{self.emu.port}")

    def test_dead_primary_falls_through_to_a_live_fallback(self) -> None:
        """CASTLE_HOST can list addresses; a re-leased IP must not kill the
        bridge while a fallback still answers (devices.toml `fallbacks`)."""
        self.host_env(f"127.0.0.1:1,127.0.0.1:{self.emu.port}")
        castle_link._cache.clear()
        st = castle_link.status()
        self.assertIsNotNone(st)
        assert st is not None
        self.assertEqual(st["bridged"], f"127.0.0.1:{self.emu.port}")
        # Commands ride the same fallback.
        code, _, _ = castle_link.forward("POST", "/api/volume?v=55")
        self.assertEqual(code, 200)
        # The live host is remembered and tried first from now on.
        self.assertEqual(castle_link.castle_hosts()[0], f"127.0.0.1:{self.emu.port}")

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
            "PUT", "/api/files/bridged.mp3", b"\xff\xfbbridged"
        )
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["bytes"], 9)
        self.assertEqual((self.card / "bridged.mp3").read_bytes(), b"\xff\xfbbridged")
        (self.card / "bridged.mp3").unlink()


if __name__ == "__main__":
    unittest.main()


class TestShowFileGuard(unittest.TestCase):
    """CASTLE_SCENES is a path from outside, so it is checked before it is
    opened: a real file, with a YAML name, resolved first."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="castle-show-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_a_yaml_file_is_a_show(self) -> None:
        p = self.tmp / "scenes.yaml"
        p.write_text("scenes:\n  - {id: vigil}\n")
        self.assertEqual(castle_emu.a_show_file(p), p.resolve())
        self.assertEqual(castle_emu.show_scene_ids(p), ["vigil", "stop"])

    def test_a_directory_is_not(self) -> None:
        d = self.tmp / "scenes.yaml"
        d.mkdir()
        self.assertIsNone(castle_emu.a_show_file(d))
        self.assertIsNone(castle_emu.show_scene_ids(d))

    def test_something_that_is_not_yaml_is_not(self) -> None:
        p = self.tmp / "passwd"
        p.write_text("root:x:0:0\n")
        self.assertIsNone(castle_emu.a_show_file(p))
        self.assertIsNone(castle_emu.show_scene_ids(p))

    def test_a_parent_hop_is_resolved_before_it_is_used(self) -> None:
        (self.tmp / "sub").mkdir()
        p = self.tmp / "scenes.yaml"
        p.write_text("scenes:\n  - {id: storm}\n")
        hop = self.tmp / "sub" / ".." / "scenes.yaml"
        self.assertEqual(castle_emu.a_show_file(hop), p.resolve())

    def test_a_missing_file_is_not_a_show(self) -> None:
        self.assertIsNone(castle_emu.a_show_file(self.tmp / "nope.yaml"))
