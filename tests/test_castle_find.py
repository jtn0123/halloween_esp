"""Find my castle — tools/castle_find.py, against a fake mDNS responder.

Nothing here multicasts: every browse is aimed at a responder on 127.0.0.1
(castle_find's `group` argument), which answers the way a castle's ESP-IDF
responder does a one-shot query — unicast, back to the asking port, with
compressed names. The probe that follows goes to castle_emu, the castle
the rest of the suite uses, and to a web server that is not a castle.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import castle_emu
import castle_find as cf
import helpers  # noqa: F401  (the sandbox scrub: CASTLE_HOST / CASTLE_DEVICES)


def label(name: str) -> bytes:
    return b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\0"


def rr(owner: bytes, rtype: int, rdata: bytes) -> bytes:
    return owner + struct.pack("!2HIH", rtype, 0x8001, 120, len(rdata)) + rdata


def castle_answer(host: str, port: int, ip: str) -> bytes:
    """`_http._tcp.local` PTR -> <host>._http._tcp.local, its SRV and its A,
    every name after the first a compression pointer, as ESP-IDF writes."""
    head = struct.pack("!6H", 0, 0x8400, 0, 1, 0, 2)
    service = label("_http._tcp.local")  # at offset 12
    local_at = 12 + len(b"\x05_http\x04_tcp")
    own = bytes([len(host)]) + host.encode()
    ptr = rr(service, cf._PTR, own + b"\xc0\x0c")
    instance_at = 12 + len(service) + 10
    target_at = 12 + len(ptr) + 2 + 10 + 6
    srv_data = struct.pack("!3H", 0, 0, port) + own + bytes([0xC0, local_at])
    srv = rr(bytes([0xC0, instance_at]), cf._SRV, srv_data)
    a = rr(bytes([0xC0, target_at]), cf._A, socket.inet_aton(ip))
    return head + ptr + srv + a


class FakeResponder:
    """A UDP 'mDNS responder' on loopback: records every query it is sent and
    answers each with whatever `reply(query)` returns."""

    def __init__(self, reply: Any) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.1)
        self.addr = self.sock.getsockname()
        self.reply = reply
        self.queries: list[bytes] = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self) -> None:
        while not self.stop.is_set():
            try:
                data, peer = self.sock.recvfrom(9000)
            except TimeoutError:
                continue
            self.queries.append(data)
            for packet in self.reply(data):
                self.sock.sendto(packet, peer)

    def close(self) -> None:
        self.stop.set()
        self.thread.join(timeout=5)
        self.sock.close()


class NotACastle(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps({"printer": "ready"}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


def question_names(query: bytes) -> list[str]:
    count = struct.unpack("!H", query[4:6])[0]
    at, names = 12, []
    for _ in range(count):
        name, at = cf._name(query, at)
        names.append(name)
        at += 4
    return names


class TestParsing(unittest.TestCase):
    def test_a_compressed_answer_reads_back_whole(self) -> None:
        recs = cf.records(castle_answer("castle-a1b2c3", 8080, "192.168.1.20"))
        self.assertIn(
            ("_http._tcp.local", cf._PTR, "castle-a1b2c3._http._tcp.local"), recs
        )
        self.assertIn(
            ("castle-a1b2c3._http._tcp.local", cf._SRV, (8080, "castle-a1b2c3.local")),
            recs,
        )
        self.assertIn(("castle-a1b2c3.local", cf._A, "192.168.1.20"), recs)
        self.assertEqual(
            cf.candidates([("192.168.1.50", recs)]),
            {"192.168.1.20:8080": "castle-a1b2c3.local"},
        )

    def test_junk_and_queries_carry_nothing(self) -> None:
        good = castle_answer("castle-a1b2c3", 80, "192.168.1.20")
        loop = struct.pack("!6H", 0, 0x8400, 0, 1, 0, 0) + b"\xc0\x0c"
        for junk in (b"", good[:20], good[:-3], loop, cf.query([("x.local", 1)])):
            with self.subTest(junk=junk[:16]):
                self.assertEqual(cf.records(junk), [])

    def test_a_bare_ptr_is_its_source_named_after_its_instance(self) -> None:
        recs = [
            ("_esphomelib._tcp.local", cf._PTR, "castle-a1b2c3._esphomelib._tcp.local")
        ]
        self.assertEqual(
            cf.candidates([("10.0.0.5", recs)]), {"10.0.0.5": "castle-a1b2c3.local"}
        )

    def test_only_this_lan_is_ever_probed(self) -> None:
        for addr in ("192.168.1.20", "10.0.0.5:8080", "169.254.3.4", "127.0.0.1:9"):
            self.assertTrue(cf.on_this_lan(addr), addr)
        for addr in ("8.8.8.8", "1.1.1.1:80", "castle.local", ""):
            self.assertFalse(cf.on_this_lan(addr), addr)


class TestFind(unittest.TestCase):
    def setUp(self) -> None:
        self.card = Path(tempfile.mkdtemp(prefix="castle-find-"))
        self.addCleanup(shutil.rmtree, self.card, ignore_errors=True)
        self.emu = castle_emu.CastleEmu(
            port=0, sd_dir=self.card, scenes=["vigil"], board="feather-s3-4m2p"
        )
        self.emu.start()
        self.addCleanup(self.emu.server_close)
        self.addCleanup(self.emu.shutdown)
        self.web = http.server.ThreadingHTTPServer(("127.0.0.1", 0), NotACastle)
        threading.Thread(target=self.web.serve_forever, daemon=True).start()
        self.addCleanup(self.web.server_close)
        self.addCleanup(self.web.shutdown)
        self.store = self.card / "devices.toml"
        self.store.write_text(
            '[yard]\nhost = "10.9.9.9"\nfallbacks = ["yard.local"]\n', encoding="utf-8"
        )
        env = mock.patch.dict(os.environ, {"CASTLE_DEVICES": str(self.store)})
        env.start()
        self.addCleanup(env.stop)

    def responder(self, reply: Any) -> FakeResponder:
        fake = FakeResponder(reply)
        self.addCleanup(fake.close)
        return fake

    def test_the_castle_is_found_and_the_printer_and_the_internet_are_not(self) -> None:
        castle = castle_answer("castle-a1b2c3", self.emu.port, "127.0.0.1")
        printer = castle_answer("printer", self.web.server_port, "127.0.0.1")
        outside = castle_answer("elsewhere", 80, "8.8.8.8")
        fake = self.responder(lambda _q: [castle, printer, outside])
        asked: list[str] = []

        def ask(addr: str) -> dict[str, Any] | None:
            asked.append(addr)
            return cf.probe(addr, timeout=2)

        found = cf.find(timeout=0.7, group=fake.addr, ask=ask)
        self.assertEqual(
            found,
            [
                {
                    "name": "castle-a1b2c3.local",
                    "address": f"127.0.0.1:{self.emu.port}",
                    "version": self.emu.version,
                    "board": "feather-s3-4m2p",
                    "fw_variant": "yard",
                }
            ],
        )
        self.assertNotIn(
            "8.8.8.8", " ".join(asked), "a responder steered the probe out"
        )
        self.assertEqual(len(asked), 2, "one probe per address, duplicates merged")
        # Asked more than once (lost packets are the norm on Wi-Fi), and for
        # the services plus the name the store already knows.
        self.assertGreaterEqual(len(fake.queries), 2)
        self.assertEqual(
            question_names(fake.queries[0]),
            ["_esphomelib._tcp.local", "_http._tcp.local", "yard.local"],
        )

    def test_silence_is_an_empty_list_and_no_network_is_said(self) -> None:
        fake = self.responder(lambda _q: [])
        self.assertEqual(cf.find(timeout=0.3, group=fake.addr), [])
        broken = mock.MagicMock()
        broken.sendto.side_effect = OSError(51, "Network is unreachable")
        broken.recvfrom.side_effect = TimeoutError
        with (
            mock.patch.object(cf.socket, "socket", return_value=broken),
            self.assertRaisesRegex(OSError, "could not ask the network"),
        ):
            cf.browse(timeout=0.3, group=fake.addr)
        broken.close.assert_called_once()

    def test_the_cli_prints_what_it_found(self) -> None:
        castle = castle_answer("castle-a1b2c3", self.emu.port, "127.0.0.1")
        fake = self.responder(lambda _q: [castle])
        with (
            mock.patch.object(cf, "GROUP", fake.addr),
            mock.patch("sys.stdout.write") as out,
        ):
            self.assertEqual(cf.main(["--json", "--timeout", "0.4"]), 0)
        printed = "".join(c.args[0] for c in out.call_args_list)
        self.assertEqual(json.loads(printed)[0]["name"], "castle-a1b2c3.local")
        quiet = self.responder(lambda _q: [])
        with (
            mock.patch.object(cf, "GROUP", quiet.addr),
            mock.patch("sys.stdout.write") as out,
        ):
            cf.main(["--timeout", "0.3"])
        self.assertIn(
            "no castle answered", "".join(c.args[0] for c in out.call_args_list)
        )


if __name__ == "__main__":
    unittest.main()
