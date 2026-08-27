"""URL import reaches the internet, not the LAN — unless you ARE the studio.

tools/netguard.py decides whether a pasted link may be handed to yt-dlp.
Resolution is mocked: no test here touches DNS.
"""

from __future__ import annotations

import ipaddress
import socket
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import netguard as ng


def fake_dns(table: dict[str, list[str]]):
    def getaddrinfo(host, port, *a, **k):
        if host not in table:
            raise socket.gaierror(8, "nodename nor servname provided")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in table[host]
        ]

    # netguard's `socket` is this very module object, so patching here lands there.
    return mock.patch.object(socket, "getaddrinfo", getaddrinfo)


class TestClassification(unittest.TestCase):
    def test_public_and_private_ranges(self) -> None:
        ip = ipaddress.ip_address
        for s in ("8.8.8.8", "142.250.72.14", "2607:f8b0::1"):
            self.assertTrue(ng.is_public(ip(s)), s)
        for s in (
            "127.0.0.1",
            "10.27.27.7",
            "192.168.1.1",
            "172.16.0.9",
            "169.254.1.1",
            "0.0.0.0",
            "224.0.0.1",
            "::1",
            "fe80::1",
            "fd00::1",
            "::ffff:192.168.0.1",
            "100.64.0.1",
        ):
            self.assertFalse(ng.is_public(ip(s)), s)

    def test_loopback_callers(self) -> None:
        self.assertTrue(ng.is_loopback("127.0.0.1"))
        self.assertTrue(ng.is_loopback("::1"))
        self.assertFalse(ng.is_loopback("192.168.1.20"))
        self.assertFalse(ng.is_loopback("not-an-ip"))

    def test_resolve_literal_name_and_unknown(self) -> None:
        with fake_dns({"example.test": ["93.184.216.34"]}):
            self.assertEqual([str(a) for a in ng.resolve("10.0.0.1")], ["10.0.0.1"])
            self.assertEqual(
                [str(a) for a in ng.resolve("example.test")], ["93.184.216.34"]
            )
            self.assertEqual(ng.resolve("nope.test"), [])


class TestRefuseReason(unittest.TestCase):
    LAN = "192.168.1.20"

    def test_the_studios_own_machine_may_fetch_anything(self) -> None:
        with fake_dns({}):
            for url in (
                "http://192.168.1.1/admin",
                "http://localhost:8093/sd/x",
                "http://10.27.27.7/api/status",
            ):
                self.assertIsNone(ng.refuse_reason(url, "127.0.0.1"), url)

    def test_a_lan_visitor_is_refused_private_targets(self) -> None:
        with fake_dns({"router.lan": ["192.168.1.1"], "castle.local": ["10.27.27.7"]}):
            for url in (
                "http://192.168.1.1/admin",
                "http://router.lan/",
                "http://127.0.0.1:8765/studio/server/stop",
                "http://[::1]/",
                "http://localhost/",
                "http://castle.local/",
                "http://169.254.9.9/",
            ):
                reason = ng.refuse_reason(url, self.LAN)
                self.assertIsNotNone(reason, url)
                self.assertIn("not a public address", reason or "")

    def test_a_lan_visitor_may_fetch_the_internet(self) -> None:
        with fake_dns({"www.youtube.com": ["142.250.72.14", "2607:f8b0::1"]}):
            self.assertIsNone(
                ng.refuse_reason("https://www.youtube.com/watch?v=abc", self.LAN)
            )
            self.assertIsNone(ng.refuse_reason("https://8.8.8.8/x", self.LAN))

    def test_a_name_with_one_private_answer_is_refused(self) -> None:
        """DNS that answers public AND private (split-horizon, rebinding):
        the private address is the one that matters."""
        with fake_dns({"two.faced": ["142.250.72.14", "10.0.0.5"]}):
            self.assertIn(
                "10.0.0.5", ng.refuse_reason("http://two.faced/", self.LAN) or ""
            )

    def test_unresolvable_is_left_to_ytdlp(self) -> None:
        with fake_dns({}):
            self.assertIsNone(ng.refuse_reason("https://nope.test/v", self.LAN))

    def test_hostless_and_broken_urls_are_refused(self) -> None:
        self.assertIsNotNone(ng.refuse_reason("http:///x", self.LAN))
        self.assertIsNotNone(ng.refuse_reason("http://[::1", self.LAN))


if __name__ == "__main__":
    unittest.main()
