"""The studio's relay leg, end to end: desk-shaped requests → studio →
castle_link → the emulated castle — with hostile names and odd bodies.

The sandbox promise is the one that matters: whatever arrives on
/api/card/<name> or PUT /api/files/<name>, nothing is read or written
outside the card directory, and the studio's own track library
(CASTLE_TRACKS) is never touched by a relayed request.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import castle_emu
import castle_link as cl
from studio_case import ServerCase

TRAVERSAL = ["..%2F..%2Fetc%2Fpasswd", "%2Fetc%2Fpasswd", "..", "../x",
             "a%2F..%2F..%2Fx", ".hidden", "scenes%2F..%2Fsecret", "",
             "%2e%2e%2f%2e%2e%2fetc", "..%5C..%5Cx", "a%00%2F..%2Fx",
             "%2e%2e", "site%2F%2e%2e%2F%2e%2e%2Fetc%2Fhosts"]


class RelayCase(ServerCase):
    jail: Path
    card: Path
    emu: castle_emu.CastleEmu

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.jail = Path(tempfile.mkdtemp(prefix="relay-jail-"))
        cls.card = cls.jail / "card"
        (cls.jail / "secret.txt").write_text("outside the card")
        cls.emu = castle_emu.CastleEmu(port=0, sd_dir=cls.card, scenes=["vigil"])
        cls.emu.start()
        (cls.card / "song.mp3").write_bytes(b"\xff\xfbsong")
        (cls.card / "scenes").mkdir()
        (cls.card / "scenes" / "vigil.mp3").write_bytes(b"\xff\xfbvigil")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.emu.shutdown()
        cls.emu.server_close()
        super().tearDownClass()

    def setUp(self) -> None:
        os.environ["CASTLE_HOST"] = f"127.0.0.1:{self.emu.port}"
        cl._cache.clear()
        self.library_before = sorted(p.name for p in self.sandbox.rglob("*"))

    def tearDown(self) -> None:
        os.environ["CASTLE_HOST"] = "127.0.0.1:1"
        cl._cache.clear()
        self.assertEqual(sorted(p.name for p in self.sandbox.rglob("*")),
                         self.library_before, "a relayed request touched the library")
        self.assertEqual(sorted(p.name for p in self.jail.iterdir()),
                         ["card", "secret.txt"], "something landed beside the card")


class TestCardPull(RelayCase):
    def test_a_real_file_comes_back_through_the_relay(self) -> None:
        code, body = self.req("GET", "/api/card/song.mp3")
        self.assertEqual((code, body), (200, b"\xff\xfbsong"))
        code, body = self.req("GET", "/api/card/scenes%2Fvigil.mp3")
        self.assertEqual((code, body), (200, b"\xff\xfbvigil"))

    def test_traversal_corpus_never_reads_outside_the_card(self) -> None:
        for name in TRAVERSAL:
            code, body = self.req("GET", "/api/card/" + name)
            self.assertIn(code, (400, 404), f"{name!r} → {code} {body!r}")
            self.assertNotIn(b"outside the card", body)
            self.assertNotIn(b"root:", body)

    def test_header_injection_in_a_name_stays_in_the_name(self) -> None:
        r = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/card/a%0D%0AX-Injected:%201.mp3")
        try:
            with urllib.request.urlopen(r, timeout=10) as f:
                hdrs, code = dict(f.headers), f.status
        except urllib.error.HTTPError as e:
            with e:
                hdrs, code = dict(e.headers), e.code
        self.assertEqual(code, 404)
        self.assertNotIn("X-Injected", hdrs)

    def test_a_raw_control_character_in_the_request_line_is_a_clean_400(self) -> None:
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(b"GET /api/card/a\rX HTTP/1.1\r\nHost: x\r\n\r\n")
        s.shutdown(socket.SHUT_WR)
        data = s.recv(4096)
        s.close()
        self.assertTrue(data.startswith(b"HTTP/1.1 400") or data.startswith(b"HTTP/1.0 400"),
                        data[:60])
        self.assertEqual(self.req("GET", "/api/status")[0], 200)   # still alive


class TestCardPush(RelayCase):
    def put(self, name: str, body: bytes) -> tuple[int, bytes]:
        return self.req("PUT", f"/api/files/{name}", body,
                        {"Content-Type": "application/octet-stream"})

    def test_bodies_of_every_size_land_intact(self) -> None:
        for size in (0, 1, 8 * 1024, 2 * 1024 * 1024):
            payload = os.urandom(size)
            code, body = self.put(f"relay_{size}.bin", payload)
            self.assertEqual(code, 200, f"{size}: {body!r}")
            self.assertEqual(json.loads(body)["bytes"], size)
            self.assertEqual((self.card / f"relay_{size}.bin").read_bytes(), payload)
            self.assertEqual(self.req("DELETE", f"/api/files/relay_{size}.bin")[0], 200)

    def test_traversal_names_are_refused_by_the_castle_verbatim(self) -> None:
        for name in TRAVERSAL:
            code, body = self.put(name, b"x")
            self.assertIn(code, (400, 404, 405), f"{name!r} → {code} {body!r}")

    def test_method_confusion_is_answered_not_acted_on(self) -> None:
        """POST to a pull path, GET to a push path, PUT to a control path:
        each is relayed and gets the castle's own 404/405 — and nothing
        is written or played."""
        self.assertEqual(self.req("POST", "/api/card/song.mp3", b"")[0], 404)
        self.assertEqual(self.req("GET", "/api/files/song.mp3")[0], 405)
        self.assertEqual(self.req("PUT", "/api/status", b"x")[0], 405)
        self.assertEqual(self.req("DELETE", "/api/card/song.mp3")[0], 404)
        self.assertTrue((self.card / "song.mp3").is_file())
        self.assertEqual(self.req("GET", "/api/files")[0], 200)

    def test_firmware_verdicts_reach_the_desk_unchanged(self) -> None:
        code, body = self.req("POST", "/api/volume?v=abc", b"")
        self.assertEqual((code, body), (400, b"need ?v=0..100"))
        code, body = self.req("POST", "/api/scene?s=nope", b"")
        self.assertEqual((code, body), (404, b"unknown scene"))
        code, body = self.req("DELETE", "/api/files/never.mp3")
        self.assertEqual((code, body), (404, b"no such file"))


if __name__ == "__main__":
    unittest.main()
