"""The Rust studio's relay leg, black box over HTTP — what it ANSWERS.

Ported from three suites that die with `tools/studio.py`
(docs/RETIREMENT.md phase 3): the live-twin comparison in
tests/test_studio_relay_rust.py, the hostile-name storm in
tests/test_studio_relay_fuzz.py (which drove the PYTHON server's
in-process fixture), and tests/test_castle_link.py's `TestStudioBridge`.
Every "the two servers agreed" is replaced by the answer itself — the
status code, the body, the bytes — because with one server left an
agreement is not evidence of anything.

The sandbox promise those suites carried comes across whole: whatever
arrives on /studio/card/<name> or PUT /api/files/<name>, nothing is read
or written outside the emulated card, and the studio's own track library
(CASTLE_TRACKS) is never touched by a relayed request. It is checked
after EVERY test here, not once.

The ops half — publish, probe, compare, restart and stop — is
tests/test_studio_ops_rs.py. The seam is the 500-line rule and it falls
where the fixtures do: this file wants an emulated castle whose card sits
inside a jail, that one wants a show it can rebuild and push.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).resolve().parent))

from studio_rs_case import CARGO, IN_CI, ROOT, StudioCase

sys.path.insert(0, str(ROOT / "tools"))
import castle_emu

#: Verbatim from tests/test_studio_relay_fuzz.py — encoded separators,
#: bare dots, a backslash, a NUL and a hidden name. The corpus is the
#: point, so it is copied rather than imported from a file being deleted.
TRAVERSAL = [
    "..%2F..%2Fetc%2Fpasswd",
    "%2Fetc%2Fpasswd",
    "..",
    "../x",
    "a%2F..%2F..%2Fx",
    ".hidden",
    "scenes%2F..%2Fsecret",
    "",
    "%2e%2e%2f%2e%2e%2fetc",
    "..%5C..%5Cx",
    "a%00%2F..%2Fx",
    "%2e%2e",
    "site%2F%2e%2e%2F%2e%2e%2Fetc%2Fhosts",
]

#: castle_link.KNOWN_API + KNOWN_PREFIX, sorted — what a 404 for an
#: unknown /api/* path hands back so the desk can say what IS served.
KNOWN_ROUTES = [
    "/api/blackout",
    "/api/bootlog",
    "/api/files",
    "/api/files/",
    "/api/health",
    "/api/light",
    "/api/ota",
    "/api/pir",
    "/api/play",
    "/api/scene",
    "/api/scenes/",
    "/api/show/start",
    "/api/show/stop",
    "/api/site/",
    "/api/status",
    "/api/stop",
    "/api/volume",
    "/remote",
    "/sd/",
]

JSON_HDRS = {"Content-Type": "application/json"}
OCTETS = {"Content-Type": "application/octet-stream"}


def raw_request(port: int, line: bytes) -> bytes:
    """One hand-written request, and every byte the server said back."""
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        s.sendall(line)
        s.shutdown(socket.SHUT_WR)
        out = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                return out
            out += chunk
    finally:
        s.close()


class CardCase(StudioCase):
    """A studio over a real emulated castle whose card lives in a jail.

    `secret.txt` sits beside the card and never inside it: the tearDown
    that every subclass inherits is the sandbox promise, and it needs a
    file outside the card worth stealing.
    """

    jail: ClassVar[Path]
    card: ClassVar[Path]
    emu: ClassVar[castle_emu.CastleEmu]
    library_before: list[str]

    @classmethod
    def setUpClass(cls) -> None:
        cls.jail = Path(tempfile.mkdtemp(prefix="relay-rs-jail-"))
        cls.card = cls.jail / "card"
        (cls.jail / "secret.txt").write_text("outside the card")
        cls.emu = castle_emu.CastleEmu(
            port=0, sd_dir=cls.card, scenes=["vigil", "storm"]
        )
        cls.emu.start()
        (cls.card / "song.mp3").write_bytes(b"\xff\xfbsong")
        (cls.card / "scenes").mkdir()
        (cls.card / "scenes" / "vigil.mp3").write_bytes(b"\xff\xfbvigil")
        # HOST_ENV is read by StudioCase.setUpClass, so the castle has to
        # exist and have a port before the studio is launched at it.
        cls.HOST_ENV = f"127.0.0.1:{cls.emu.port}"
        super().setUpClass()

    @classmethod
    def tearDownClass(cls) -> None:
        super().tearDownClass()
        cls.emu.shutdown()
        cls.emu.server_close()
        shutil.rmtree(cls.jail, ignore_errors=True)

    def library(self) -> list[str]:
        """Every name in the sandboxed library. `tracks.lock` is skipped:
        it is the manifest's flock sidecar, created the first time the
        studio's OWN track listing runs, and its coming and going says
        nothing about whether a relayed request reached the library."""
        return sorted(p.name for p in self.tracks.rglob("*") if p.name != "tracks.lock")

    def settled(self) -> None:
        """Wait out the emulator's mailbox tick, then forget what it ran.

        The castle applies a queued command ~200 ms after it answers
        {"queued": true}, so a verb sent by the PREVIOUS test can still be
        in flight — and it would turn up in `applied` as evidence against
        a test that never sent anything.
        """
        time.sleep(castle_emu.APPLY_DELAY_S * 3)
        self.emu.applied.clear()

    def setUp(self) -> None:
        self.library_before = self.library()

    def tearDown(self) -> None:
        self.assertEqual(
            self.library(),
            self.library_before,
            "a relayed request touched the library",
        )
        self.assertEqual(
            sorted(p.name for p in self.jail.iterdir()),
            ["card", "secret.txt"],
            "something landed beside the card",
        )


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class Bridge(CardCase):
    """The studio in front of a castle that is answering."""

    def test_01_status_is_the_castles_own_body(self) -> None:
        code, body = self.json("/api/status")
        self.assertEqual(code, 200)
        # The emulator's own fields, not a studio summary of them.
        self.assertEqual(body["version"], "5.40")
        self.assertEqual(body["compiled"], "emulated")
        self.assertEqual(body["scenes"], "vigil,storm")
        # `bridged` names WHO answered; the absence of `studio` is what
        # flips the desk out of offline mode.
        self.assertEqual(body["bridged"], self.HOST_ENV)
        self.assertNotIn("studio", body)

    def test_02_transport_verbs_relay_verbatim(self) -> None:
        for path in ("/api/scene?s=vigil", "/api/volume?v=40", "/api/stop"):
            code, hdrs, body = self.req(path, "POST")
            self.assertEqual((code, body), (200, b'{"queued": true}'), path)
            self.assertEqual(hdrs.get("content-type"), "application/json")

    def test_03_firmware_verdicts_reach_the_desk_unchanged(self) -> None:
        """The castle's own refusals, byte for byte — the desk's toasts
        are written against these strings."""
        for path, method, want in (
            ("/api/volume?v=abc", "POST", (400, b"need ?v=0..100")),
            ("/api/scene?s=nope", "POST", (404, b"unknown scene")),
            ("/api/scene?s=nosuch", "POST", (404, b"unknown scene")),
            ("/api/files/never.mp3", "DELETE", (404, b"no such file")),
        ):
            code, hdrs, body = self.req(path, method)
            self.assertEqual((code, body), want, path)
            self.assertEqual(hdrs.get("content-type"), "text/plain", path)

    def test_04_an_unclaimed_api_route_relays(self) -> None:
        code, hdrs, body = self.req("/api/bootlog")
        self.assertEqual(code, 200)
        self.assertEqual(body, b"boot log: 2 lines, 0 dropped\n[I][emu] up\n")
        self.assertEqual(hdrs.get("content-type"), "text/plain")

    def test_05_scene_with_a_query_fires_on_the_castle(self) -> None:
        self.settled()
        code, body = self.json("/api/scene?s=vigil", "POST")
        self.assertEqual((code, body), (200, {"queued": True}))
        # ~200 ms later the emulator's mailbox tick runs it, for real.
        end = time.monotonic() + 10
        while time.monotonic() < end and not self.emu.applied:
            time.sleep(0.05)
        self.assertEqual(self.emu.applied, [("SCENE", "vigil")])

    def test_06_scene_with_a_json_body_stays_the_studios_own(self) -> None:
        """The scenes.yaml editor and the castle's fire-a-scene share a
        path; a JSON body must never end up on the hardware."""
        self.settled()
        code, body = self.json("/api/scene", "POST", {"id": ""})
        self.assertEqual((code, body), (400, {"error": "need id and yaml"}))
        self.assertEqual(self.emu.applied, [])

    def test_07_the_studios_own_routes_are_not_relayed(self) -> None:
        """/api/tracks is the deprecated spelling of /studio/tracks — it
        answers out of the sandboxed library, never off the castle."""
        self.settled()
        code, body = self.json("/api/tracks")
        self.assertEqual(code, 200)
        self.assertEqual(
            [t["id"] for t in body["tracks"]],
            ["t_alpha", "t_beta", "t_del", "t_empty", "t_meta"],
        )
        self.assertEqual(body["scenes"], ["vigil", "storm"])
        self.assertEqual(self.emu.applied, [])

    def test_08_the_remote_page_relays(self) -> None:
        code, hdrs, body = self.req("/remote")
        self.assertEqual(code, 200)
        self.assertEqual(hdrs.get("content-type"), "text/html; charset=utf-8")
        self.assertIn(b"<title>Castle Remote</title>", body)

    def test_09_an_unknown_castle_route_is_a_404_not_a_502(self) -> None:
        """Refused HERE, before a socket is opened: a client typo used to
        read as an outage."""
        code, body = self.json("/api/nonsense")
        self.assertEqual(code, 404)
        self.assertEqual(body["error"], "unknown castle route")
        self.assertEqual(body["known"], KNOWN_ROUTES)

    def test_10_studio_and_root_misses_are_plain_404s(self) -> None:
        for path in ("/studio/nope", "/nope"):
            code, body = self.json(path)
            self.assertEqual((code, body), (404, {"error": "not found"}), path)


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class CardPull(CardCase):
    """GET /studio/card/<name> — the castle's /sd/<name>, name-stripped."""

    def test_a_real_file_comes_back_through_the_relay(self) -> None:
        code, hdrs, body = self.req("/studio/card/song.mp3")
        self.assertEqual((code, body), (200, b"\xff\xfbsong"))
        self.assertEqual(hdrs.get("content-type"), "audio/mpeg")
        code, _, body = self.req("/studio/card/scenes%2Fvigil.mp3")
        self.assertEqual((code, body), (200, b"\xff\xfbvigil"))

    def test_the_old_api_card_spelling_still_pulls(self) -> None:
        """studio_http.STUDIO_ROUTES keeps the alias alive for one more
        release, and a desk built before the move still uses it."""
        code, _, body = self.req("/api/card/song.mp3")
        self.assertEqual((code, body), (200, b"\xff\xfbsong"))

    def test_traversal_corpus_never_reads_outside_the_card(self) -> None:
        for name in TRAVERSAL:
            for prefix in ("/studio/card/", "/api/card/"):
                code, _, body = self.req(prefix + name)
                self.assertIn(code, (400, 404), f"{prefix}{name!r} → {code} {body!r}")
                self.assertNotIn(b"outside the card", body)
                self.assertNotIn(b"root:", body)
        # And the two shapes of refusal are stable, not incidental.
        self.assertEqual(self.req("/studio/card/")[2], b'{"error": "no file name"}')
        self.assertEqual(self.req("/studio/card/%2e%2e")[2], b"bad path")

    def test_header_injection_in_a_name_stays_in_the_name(self) -> None:
        code, hdrs, _ = self.req("/studio/card/a%0D%0AX-Injected:%201.mp3")
        self.assertEqual(code, 404)
        self.assertEqual(sorted(hdrs), ["content-length", "content-type"])

    def test_a_raw_control_character_is_a_clean_400(self) -> None:
        """A bare CR or NUL in the request line.

        `split_whitespace` treats a CR as a separator, so
        `GET /studio/card/a\rX` was once served as `/studio/card/a` — an
        answer to a question nobody asked. The Python studio's http.server
        refused the line outright and castle-core's `parse_head` does the
        same now (docs/RETIREMENT.md's port pass). What must hold either
        way: ONE response, no header the caller chose, and a server still
        serving afterwards.
        """
        for line in (
            b"GET /studio/card/a\rX HTTP/1.1\r\nHost: x\r\n\r\n",
            b"GET /studio/card/a\x00X HTTP/1.1\r\nHost: x\r\n\r\n",
            b"GET /api/status\rX HTTP/1.1\r\nHost: x\r\n\r\n",
        ):
            data = raw_request(self.port, line)
            self.assertTrue(data.startswith(b"HTTP/1.1 400 "), data[:60])
            self.assertEqual(data.count(b"HTTP/1."), 1, data[:200])
            self.assertNotIn(b"X-Injected", data)
            self.assertNotIn(b"outside the card", data)
            self.assertEqual(self.req("/api/status")[0], 200)  # still alive


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class CardPush(CardCase):
    """PUT/DELETE /api/files/<name> — the push leg, relayed verbatim."""

    def put(self, name: str, body: bytes) -> tuple[int, bytes]:
        code, _, out = self.req(f"/api/files/{name}", "PUT", OCTETS, body)
        return code, out

    def test_bodies_of_every_size_land_intact(self) -> None:
        for size in (0, 1, 8 * 1024, 2 * 1024 * 1024):
            payload = os.urandom(size)
            code, body = self.put(f"relay_{size}.bin", payload)
            self.assertEqual(code, 200, f"{size}: {body!r}")
            self.assertEqual(json.loads(body)["bytes"], size)
            self.assertEqual(json.loads(body)["path"], f"/sd/relay_{size}.bin")
            # On the card, byte for byte — and readable back through the
            # pull leg, which is the round trip the desk actually makes.
            self.assertEqual((self.card / f"relay_{size}.bin").read_bytes(), payload)
            code, _, got = self.req(f"/studio/card/relay_{size}.bin")
            self.assertEqual((code, got), (200, payload), size)
            listed = json.loads(self.req("/api/files")[2])
            self.assertIn(
                {"name": f"relay_{size}.bin", "size": size, "dir": False}, listed
            )
            code, _, out = self.req(f"/api/files/relay_{size}.bin", "DELETE")
            self.assertEqual((code, out), (200, b'{"deleted": true}'), size)
            self.assertFalse((self.card / f"relay_{size}.bin").exists())

    def test_traversal_names_are_refused_by_the_castle_verbatim(self) -> None:
        for name in TRAVERSAL:
            code, body = self.put(name, b"x")
            self.assertEqual((code, body), (400, b"bad filename"), name)
        self.assertEqual(
            sorted(str(p.relative_to(self.card)) for p in self.card.rglob("*")),
            ["scenes", "scenes/vigil.mp3", "song.mp3"],
        )

    def test_method_confusion_is_answered_not_acted_on(self) -> None:
        """POST to a pull path, GET to a push path, PUT to a control
        path, DELETE on a pull path: each is answered, and nothing is
        written or played."""
        self.settled()
        code, body = self.json("/studio/card/song.mp3", "POST", {})
        self.assertEqual((code, body), (404, {"error": "not found"}))
        code, body = self.json("/studio/card/song.mp3", "DELETE")
        self.assertEqual((code, body), (404, {"error": "not found"}))
        for path in ("/api/files/song.mp3", "/api/status"):
            method = "GET" if path.endswith(".mp3") else "PUT"
            code, _, out = self.req(
                path, method, None, b"x" if method == "PUT" else None
            )
            self.assertEqual(code, 405, path)
            self.assertEqual(
                out, b"Request method for this URI is not handled by server", path
            )
        self.assertEqual((self.card / "song.mp3").read_bytes(), b"\xff\xfbsong")
        self.assertEqual(self.emu.applied, [])
        self.assertEqual(self.req("/api/files")[0], 200)


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class NoCastle(StudioCase):
    """CASTLE_HOST="" — explicitly no castle, and no sockets opened."""

    HOST_ENV = ""

    def test_status_is_the_bare_studio_marker(self) -> None:
        code, body = self.json("/api/status")
        self.assertEqual((code, body), (200, {"studio": True}))

    def test_remote_is_a_502_naming_the_castle(self) -> None:
        code, body = self.json("/remote")
        self.assertEqual((code, body), (502, {"error": "no castle configured"}))


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class DeadCastle(StudioCase):
    """A configured castle that refuses instantly (port 1)."""

    HOST_ENV = "127.0.0.1:1"

    def test_status_names_who_is_not_answering(self) -> None:
        # `castle` names the configured host: the desk can say WHO is
        # silent instead of rendering a blank box.
        code, body = self.json("/api/status")
        self.assertEqual((code, body), (200, {"studio": True, "castle": "127.0.0.1:1"}))

    def test_remote_is_a_502_not_a_404(self) -> None:
        code, body = self.json("/remote")
        self.assertEqual((code, body), (502, {"error": "castle not reachable"}))


if __name__ == "__main__":
    unittest.main()
