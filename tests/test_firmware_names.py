"""The helper layer's byte rules, re-derived from the firmware.

firmware/sd_web_util.h is what every handler in sd_web.h calls before it
trusts anything off the wire: url_decode, safe_name, safe_subpath,
name_from_uri, query_param. tools/castle_emu_wire.py is the emulator's port
of exactly those five, and the port is only worth anything if it cannot
drift — so these tests read the RULE out of the C (the length ceiling, the
lead byte, the find() calls, the per-byte bounds and literals) and hold the
Python to it over a corpus, rather than restating the rule in Python where
the two could quietly disagree.

Split from tests/test_firmware_contract.py (the routes, the reply_err
strings and the live-wire verdicts stayed there) on the seam the firmware
itself has between sd_web.h and sd_web_util.h; the parsing both suites do
is tests/firmware_source.py.
"""

from __future__ import annotations

import random
import re
import sys
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))  # firmware_source

import castle_emu_wire as wire
from firmware_source import FUNCS, grab


class TestNameRules(unittest.TestCase):
    """safe_name / safe_subpath / url_decode / query_param, re-derived."""

    def ref_safe_name(self) -> Callable[..., Any]:
        body = FUNCS["safe_name"]
        limit = int(grab(r"n\.size\(\) >= (\d+)", body))
        lead = grab(r"n\[0\] == '(.)'", body).encode()
        finds = [f.encode() for f in re.findall(r"""n\.find\(["'](.+?)["']\)""", body)]
        # The per-byte loop: `c < 0x20`, `c >= 0x80` and each
        # `c == <literal>`, read off the C so a new forbidden byte in the
        # firmware fails here first.
        below = int(grab(r"c < (0x[0-9a-fA-F]+)", body), 16)
        above = int(grab(r"c >= (0x[0-9a-fA-F]+)", body), 16)
        bad = set()
        for lit in re.findall(r"c == (0x[0-9a-fA-F]+|'[^']+')", body):
            bad.add(
                int(lit, 16)
                if lit.startswith("0x")
                else ord(lit[1:-1].encode().decode("unicode_escape"))
            )
        self.assertEqual(limit, wire.NAME_MAX)
        self.assertEqual(bad, {0x7F, ord('"'), ord("\\")})
        self.assertEqual(above, 0x80)  # ASCII only, since v5.46
        return lambda n: (
            bool(n)
            and len(n) < limit
            and n[:1] != lead
            and all(f not in n for f in finds)
            and all(below <= c < above and c not in bad for c in n)
        )

    def ref_safe_subpath(self) -> Callable[..., Any]:
        body = FUNCS["safe_subpath"]
        limit = int(grab(r"p\.size\(\) > (\d+)", body))
        leads = [c.encode() for c in re.findall(r"p\[0\] == '(.)'", body)]
        finds = [f.encode() for f in re.findall(r"""p\.find\(["'](.+?)["']\)""", body)]
        self.assertEqual(limit, wire.SUBPATH_MAX)
        return lambda p: (
            bool(p)
            and len(p) <= limit
            and p[:1] not in leads
            and all(f not in p for f in finds)
        )

    def corpus(self, seed: int = 7) -> list[bytes]:
        rng = random.Random(seed)
        alphabet = b"ab./\\?%+ \x00\xc3\xa9\"'\t\x1f\x7f"
        out = [
            b"",
            b".",
            b"..",
            b"a",
            b"a/b",
            b"/a",
            b".a",
            b"a..b",
            b"a" * 99,
            b"a" * 100,
            b"a" * 140,
            b"a" * 141,
            b"\xc3\xa9" * 50,
            b"\x00",
            b"a\x00/..",
            b"..\\x",
            b"a?b",
            b'a"b.mp3',
            b"a\\b.mp3",
            b"a\tb",
            b"a\x1fb",
            b"a\x7fb",
            b"a b",
            b"a'b",
            b"\xc3\xa9.mp3",
            b"a\x80b",
        ]
        out += [
            bytes(rng.choice(alphabet) for _ in range(rng.randint(0, 150)))
            for _ in range(1500)
        ]
        return out

    def test_safe_name_matches_the_c_rule_byte_for_byte(self) -> None:
        ref = self.ref_safe_name()
        for n in self.corpus():
            self.assertEqual(wire.safe_name(n), ref(n), repr(n))

    def test_safe_subpath_matches_the_c_rule(self) -> None:
        ref = self.ref_safe_subpath()
        for p in self.corpus(8):
            self.assertEqual(wire.safe_subpath(p), ref(p), repr(p))

    def test_safe_name_counts_bytes_not_characters(self) -> None:
        """The ceiling is 100 BYTES — the C counts std::string::size(), not
        characters. 99 ASCII bytes fit, 100 do not; and since v5.46 the
        accented name that used to prove this (40 é = 80 bytes) is refused
        for its bytes rather than its length."""
        self.assertTrue(wire.safe_name(b"a" * 99))
        self.assertFalse(wire.safe_name(b"a" * 100))
        self.assertFalse(wire.safe_name("é".encode() * 60))
        self.assertFalse(wire.safe_name("é".encode() * 40))

    def test_safe_name_refuses_what_would_break_the_json(self) -> None:
        """A quote, a backslash, a control byte or DEL never gets ONTO the
        card through us: safe_name says no at the door (and since v5.25
        json_escape keeps the parse alive for names that got there another
        way). Spaces stay welcome."""
        for bad in (
            b'a"b.mp3',
            b"a\\b.mp3",
            b"a\tb",
            b"a\nb",
            b"a\rb",
            b"\x00",
            b"ab\x00cd.mp3",
            b"a\x1fb",
            b"a\x7fb",
            b'"',
            b"\\",
        ):
            self.assertFalse(wire.safe_name(bad), repr(bad))
        for good in (
            b"a b.mp3",
            b"a'b.mp3",
            b"x-y_z (1).mp3",
            b"a~b",
        ):
            self.assertTrue(wire.safe_name(good), repr(good))

    def test_safe_name_refuses_the_whole_high_half(self) -> None:
        """grade report 2026-09-06 J1: json_escape passes bytes >= 0x20
        through raw, so ANY byte over ASCII — a lone 0x80, valid UTF-8 or
        not — made /api/status and /api/files invalid UTF-8 and every
        Python client of the castle raised instead of parsing. The door is
        where that is closed; h_list skips-and-counts what the Mac wrote
        onto the card directly."""
        for b in range(0x80, 0x100):
            self.assertFalse(wire.safe_name(bytes([b])), hex(b))
            self.assertFalse(wire.safe_name(b"song" + bytes([b]) + b".mp3"), hex(b))
        self.assertFalse(wire.safe_name("é.mp3".encode()))  # valid UTF-8, still no
        self.assertFalse(wire.safe_name("名.mp3".encode()))

    def test_query_param_buffers_are_the_firmwares(self) -> None:
        body = FUNCS["query_param"]
        self.assertEqual(int(grab(r"char q\[(\d+)\]", body)), wire.QUERY_BUF)
        self.assertEqual(int(grab(r"char val\[(\d+)\]", body)), wire.VALUE_BUF)
        self.assertIn("url_decode(val)", body)  # values ARE decoded

    def test_url_decode_plus_and_bad_hex(self) -> None:
        """'+' is a space and "%zz" is strtol's 0 — both read off the C."""
        self.assertIn("'+'", FUNCS["url_decode"])
        self.assertIn("strtol", FUNCS["url_decode"])
        self.assertEqual(wire.url_decode(b"a+b%20c"), b"a b c")
        self.assertEqual(wire.url_decode(b"a%zzb"), b"a\x00b")
        self.assertEqual(wire.url_decode(b"a%4"), b"a%4")  # needs two chars
        self.assertEqual(wire.url_decode(b"%4g"), b"\x04")  # leading digit only

    def test_name_from_uri_cuts_at_the_decoded_question_mark(self) -> None:
        self.assertIn("n.find('?')", FUNCS["name_from_uri"])
        self.assertEqual(
            wire.name_from_uri(b"/api/files/a%3Fb.mp3", b"/api/files/"), b"a"
        )
        self.assertEqual(
            wire.name_from_uri(b"/api/files/a.mp3?x=1", b"/api/files/"), b"a.mp3"
        )

    def test_query_param_semantics(self) -> None:
        """httpd_query_key_value: case-insensitive key, first '=' wins, a
        pair without '=' derails the scan, oversize → ""."""
        self.assertEqual(wire.query_param(b"/api/scene?s=vigil", "s"), b"vigil")
        self.assertEqual(wire.query_param(b"/api/scene?S=vigil", "s"), b"vigil")
        self.assertEqual(wire.query_param(b"/api/scene?s=a&s=b", "s"), b"a")
        self.assertEqual(wire.query_param(b"/api/scene?x&s=vigil", "s"), b"")
        self.assertEqual(wire.query_param(b"/api/scene?s=vigil&x", "s"), b"vigil")
        self.assertEqual(wire.query_param(b"/api/scene?s=", "s"), b"")
        self.assertEqual(wire.query_param(b"/api/scene?", "s"), b"")
        self.assertEqual(wire.query_param(b"/api/scene?s=" + b"v" * 120, "s"), b"")
        self.assertEqual(
            wire.query_param(b"/api/scene?s=" + b"v" * 119, "s"), b"v" * 119
        )
        self.assertEqual(wire.query_param(b"/api/scene?s=v&" + b"x" * 197, "s"), b"")
