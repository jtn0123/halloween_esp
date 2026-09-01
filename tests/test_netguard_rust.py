"""castle-core's netguard against tools/netguard.py, refusal for refusal.

The SSRF guard was the one port held to a hand-copied snapshot rather than
a cross-language corpus (grade report 2026-08-31 A3): tighten the Python and the Rust
studio would keep the weaker policy silently, on the one seam where that
means a LAN visitor reaching an address only this machine can see.

So both implementations answer the SAME corpus here — the one
tests/test_netguard.py drives, widened with the shapes a URL parser gets
wrong (credentials, brackets, ports, case, v4-mapped v6) — and the
sentences must match, not just the verdicts: the desk shows the string.

DNS is mocked on both sides, from one table: the Python through
socket.getaddrinfo as its own tests do, the Rust through the table
`netguard_dump` takes on stdin. No test here touches the network.

Skipped, not failed, without cargo — except in CI.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import cargo_gate
import netguard as ng
from test_netguard import fake_dns

CARGO = cargo_gate.CARGO
IN_CI = bool(os.environ.get("CI"))
BIN = ROOT / "core" / "target" / "release" / "netguard_dump"

#: What the world resolves to for this corpus — split-horizon and
#: rebinding answers included.
DNS: dict[str, list[str]] = {
    "router.lan": ["192.168.1.1"],
    "castle.home": ["10.27.27.7"],
    "www.youtube.com": ["142.250.72.14", "2607:f8b0::1"],
    "two.faced": ["142.250.72.14", "10.0.0.5"],
    "rebound.example": ["10.0.0.5", "142.250.72.14"],
    "mapped.example": ["::ffff:192.168.0.1"],
    "sixer.example": ["2607:f8b0::1"],
    "cgnat.example": ["100.64.0.1"],
    "bench.example": ["198.18.0.1"],
    "testnet.example": ["203.0.113.9"],
    "zeroes.example": ["0.0.0.0"],
}

LAN = "192.168.1.20"

#: (url, caller ip). The caller matters as much as the URL: loopback is
#: exempt by design, and that exemption is part of what must match.
CASES: list[tuple[str, str]] = [
    # The studio's own machine may fetch anything at all.
    ("http://192.168.1.1/admin", "127.0.0.1"),
    ("http://localhost:8093/sd/x", "127.0.0.1"),
    ("http://10.27.27.7/api/status", "127.0.0.1"),
    ("http://router.lan/", "::1"),
    ("http://router.lan/", "127.0.0.53"),
    # ...and a caller whose address is not an address at all is not it.
    ("http://router.lan/", "garbage"),
    ("http://router.lan/", ""),
    ("http://router.lan/", "fe80::1%lo0"),
    # A LAN visitor is refused every private shape.
    ("http://192.168.1.1/admin", LAN),
    ("http://router.lan/", LAN),
    ("http://127.0.0.1:8765/studio/server/stop", LAN),
    ("http://[::1]/", LAN),
    ("http://localhost/", LAN),
    ("http://LocalHost/", LAN),
    ("http://localhost.localdomain/x", LAN),
    ("http://castle.local/", LAN),
    ("http://CASTLE.LOCAL/", LAN),
    ("http://castle.home/", LAN),
    ("http://169.254.9.9/", LAN),
    ("http://[fd00::1]/", LAN),
    ("http://[fe80::1]/", LAN),
    ("http://172.16.0.9/", LAN),
    ("http://10.0.0.1:8080/x?y=1#z", LAN),
    ("http://cgnat.example/", LAN),
    ("http://bench.example/", LAN),
    ("http://testnet.example/", LAN),
    ("http://zeroes.example/", LAN),
    ("http://mapped.example/", LAN),
    ("http://[::ffff:192.168.0.1]/", LAN),
    # Split horizon: the private answer is the one that decides, whichever
    # order it arrives in.
    ("http://two.faced/", LAN),
    ("http://rebound.example/", LAN),
    # The internet is fine.
    ("https://www.youtube.com/watch?v=abc", LAN),
    ("https://8.8.8.8/x", LAN),
    ("https://sixer.example/v", LAN),
    ("https://[2607:f8b0::1]/v", LAN),
    ("https://user:pw@www.youtube.com/watch?v=abc", LAN),
    # Credentials hiding a private target behind a public-looking name.
    ("http://www.youtube.com@10.0.0.5/x", LAN),
    ("http://user:pw@192.168.1.1/", LAN),
    # Unresolvable is left to yt-dlp to complain about.
    ("https://nope.test/v", LAN),
    ("https://nope.test:8080/v", LAN),
    # Hostless and broken.
    ("http:///x", LAN),
    ("http://[::1", LAN),
    ("notalink", LAN),
    ("", LAN),
    ("/relative/path", LAN),
    ("file:///etc/passwd", LAN),
]


def rust_answers() -> list[str | None]:
    """netguard_dump over the corpus: one entry per case, null = allowed."""
    built = cargo_gate.build("--bin", "netguard_dump")
    assert built.returncode == 0, built.stderr
    doc = {"dns": DNS, "cases": [{"url": u, "ip": ip} for u, ip in CASES]}
    r = subprocess.run(
        [str(BIN)],
        input=json.dumps(doc),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    out: list[str | None] = json.loads(r.stdout)
    return out


def python_answers() -> list[str | None]:
    with fake_dns(DNS):
        return [ng.refuse_reason(url, ip) for url, ip in CASES]


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class TestNetguardRustParity(unittest.TestCase):
    def test_the_corpus_is_refused_with_the_same_words(self) -> None:
        py = python_answers()
        rs = rust_answers()
        self.assertEqual(len(rs), len(CASES))
        for (url, ip), a, b in zip(CASES, py, rs, strict=True):
            self.assertEqual(a, b, f"{ip} -> {url!r}: {a!r} vs {b!r}")

    def test_the_corpus_actually_exercises_both_verdicts(self) -> None:
        """A gate that only ever saw allows would pass while refusing
        nothing — and one that only ever saw refusals would pass while
        refusing everything."""
        py = python_answers()
        self.assertGreaterEqual(sum(1 for r in py if r is None), 8)
        self.assertGreaterEqual(sum(1 for r in py if r is not None), 20)
        self.assertGreaterEqual(
            len({r for r in py if r is not None and "(" in r}), 8, "one shape only"
        )

    def test_every_name_in_the_table_is_actually_asked_for(self) -> None:
        """The two sides answer ONE world. A table entry no case reaches
        is a resolution the corpus never tests — and the first sign that a
        case was edited out from under it."""
        seen = {_host(url) for url, _ip in CASES}
        self.assertTrue(set(DNS) <= seen, f"unused table names: {set(DNS) - seen}")


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


if __name__ == "__main__":
    unittest.main()
