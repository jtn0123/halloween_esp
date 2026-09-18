"""Thousands of hostile names at both castles, and the byte rules underneath.

The matrix next door (tests/test_firmware_web_cxx.py) asks the questions
someone thought of. This asks the ones nobody did: tools/fuzz_corpus.py's
alphabet of percent-escapes, dot segments, slashes, high bytes and invalid
hex, assembled into names and query values by a seeded `random.Random`, and
fired at the real compiled firmware and at the emulator together. Every pair
of answers must be the same bytes, and both cards must be in the same state
when the storm stops.

That last part matters as much as the replies: a name that decodes to a NUL
truncates the C string the handler hands to fopen, and a name the two sides
disagree about lands as a FILE, silently, on one card and not the other.

The rules half runs safe_name, url_decode, json_escape and url_encode in C
— the harness binary's `--rules` mode — against castle_emu_wire's port of the
same four, byte for byte. They decide what may reach the card at all and how
a name goes back out as a URL, and a fuzz that only compares HTTP answers can
be fooled by two handlers making the same wrong call for different reasons.

Knobs, read at run time: CASTLE_STORM_SEED (default 1234) and
CASTLE_STORM_CASES (default 2000). Fixed by default so a red run reproduces.
"""

from __future__ import annotations

import os
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

import castle_emu_wire as wire
import fuzz_corpus
from firmware_web_harness import COMPILER, IN_CI, CastleC, Pair, compiled

SEED = int(os.environ.get("CASTLE_STORM_SEED", "1234"))
CASES = int(os.environ.get("CASTLE_STORM_CASES", "2000"))

#: Where a fuzz name can be put, and what that costs. The weights keep the
#: storm spending most of itself on the routes that touch the card.
SHAPES: tuple[tuple[float, str, bytes, bool], ...] = (
    (0.24, "PUT", b"/api/files/", True),
    (0.32, "PUT", b"/api/site/", True),
    (0.40, "PUT", b"/api/scenes/", True),
    (0.58, "DELETE", b"/api/files/", False),
    (0.66, "DELETE", b"/api/site/", False),
    (0.74, "DELETE", b"/api/scenes/", False),
    (0.82, "GET", b"/sd/", False),
    (0.88, "GET", b"/site/", False),
    (0.94, "POST", b"/api/play?f=", False),
)


@unittest.skipIf(COMPILER is None and not IN_CI, "no host C++ compiler")
class TestNameStorm(unittest.TestCase):
    tmp: ClassVar[str]
    pair: ClassVar[Pair]

    @classmethod
    def setUpClass(cls) -> None:
        if COMPILER is None:
            raise AssertionError("CI is set and no host C++ compiler is on PATH")
        _, built = compiled()
        assert built.returncode == 0, built.stderr
        cls.tmp = tempfile.mkdtemp(prefix="castle-web-storm-")
        cls.pair = Pair(Path(cls.tmp), "storm")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.pair.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def request(self, rng: random.Random) -> tuple[str, bytes, bytes]:
        """One request: usually a fuzz name on a card route, sometimes a
        fuzz query on a validated one, sometimes a near-miss path."""
        roll = rng.random()
        if roll > 0.94:
            route, key, good = rng.choice(fuzz_corpus.QUERY_ROUTES)
            return "POST", (route + fuzz_corpus.query(rng, key, good)).encode(), b""
        name = fuzz_corpus.name(rng).encode()
        for cut, method, prefix, needs_body in SHAPES:
            if roll <= cut:
                return method, prefix + name, b"payload" if needs_body else b""
        raise AssertionError("unreachable")

    def test_the_two_castles_answer_a_storm_identically(self) -> None:
        rng = random.Random(SEED)
        fired = 0
        for i in range(CASES):
            method, target, body = self.request(rng)
            if len(target) > wire.MAX_URI:
                # A request line the httpd would 414 before parsing. Sent
                # on its own below rather than here, where the length is an
                # accident of the alphabet.
                continue
            c, e = self.pair.both(method, target, body)
            fired += 1
            self.assertEqual(
                (c.status, c.body),
                (e.status, e.body),
                f"case {i} (seed {SEED}): {method} {target!r}",
            )
            self.assertEqual(c.ctype, e.ctype, f"case {i}: {method} {target!r}")
            self.assertEqual(c.extra, e.extra, f"case {i}: {method} {target!r}")
        self.assertGreater(fired, CASES // 2, "the storm threw almost nothing")
        # The replies agreeing is half of it: a name the two sides read
        # differently would land as a file on one card and not the other.
        got_c, got_e = self.pair.cards()
        self.assertEqual(got_c, got_e, "the two cards drifted apart")

    def test_the_near_misses_and_every_verb_agree(self) -> None:
        rng = random.Random(SEED + 1)
        # Every verb the fuzz knows, HEAD/PATCH/OPTIONS included: none of
        # these paths is a route, so all seven must draw the same 404.
        for path in fuzz_corpus.NEAR_MISSES:
            for verb in fuzz_corpus.VERBS:
                c, e = self.pair.both(verb, path.encode())
                self.assertEqual(
                    (c.status, c.body), (e.status, e.body), f"{verb} {path}"
                )
                self.assertEqual(c.status, 404, f"{verb} {path}")
        for _ in range(20):
            target = b"/api/files/" + fuzz_corpus.name(rng).encode()
            if len(target) > wire.MAX_URI:
                continue
            c, e = self.pair.both("PATCH", target)
            self.assertEqual((c.status, c.body), (e.status, e.body))

    def test_a_long_spaced_name_reaches_the_handler_on_both(self) -> None:
        """A10: an 80-character name with 27 spaces is 134 bytes once it is
        URL-encoded, and query_param's value buffer held 120. The board
        answered ESP_ERR_HTTPD_RESULT_TRUNC, query_param swallowed it as an
        empty parameter, and /api/play said 400 "need ?f=<file>" for a file
        /api/files had just listed. Both castles must now queue it."""
        name = ("a " * 27) + "a" * 26
        self.assertEqual((len(name), name.count(" ")), (80, 27))
        encoded = name.replace(" ", "%20").encode()
        self.assertEqual(len(encoded), 134)
        c, e = self.pair.both("POST", b"/api/play?f=" + encoded)
        self.assertEqual((c.status, c.body), (e.status, e.body))
        self.assertEqual((c.status, c.body), (200, b'{"queued":true}'))

    #: Names safe_name admits, that the desk can PUT (it encodes them), that
    #: /api/files lists — and that could not be PLAYED before v5.64, because
    #: the loopback URL was built raw and the stream server decodes what it
    #: is given (grade report 2026-09-17 J1). A name with '?' in it is NOT
    #: here: h_sd_get decodes and only then cuts at the first '?', exactly as
    #: name_from_uri does for PUT, so such a file cannot be addressed on
    #: either route and never gets onto the card through us in the first place.
    AWKWARD = ("a+b.mp3", "100%.mp3", "a b.mp3", "x%4.mp3", "a#b.mp3", "a=b&c.mp3")
    #: The three of those the SERVER itself used to get wrong: '+' came back
    #: a space (404 for a file that is right there), and both '%' stubs made
    #: url_decode fail empty (400). The other three reached the right file
    #: raw and were fragile one layer up — '#' is a fragment and a raw space
    #: is not a legal URL character, both in the IDF client's hands.
    RAW_MISSES = ("a+b.mp3", "100%.mp3", "x%4.mp3")

    def test_an_encoded_name_reaches_the_file_the_raw_one_missed(self) -> None:
        """The stream server, asked for each of those names the way the
        media player now asks: url_encode'd. Every one must come back 200
        with the file's own bytes, where the raw spelling the two
        set_media_url lambdas used to build found the wrong file or none."""
        for i, name in enumerate(self.AWKWARD):
            raw = name.encode()
            self.assertTrue(wire.safe_name(raw), name)
            body = b"\xff\xfb" + bytes([i]) * 64
            (self.pair.card_c / name).write_bytes(body)
            encoded = wire.url_encode(raw)
            got = self.pair.c.http("GET", b"/sd/" + encoded, port=8080)
            self.assertEqual(got.status, 200, f"{name} as {encoded!r}")
            self.assertEqual(got.body, body, name)
            if name in self.RAW_MISSES:
                self.assertNotEqual(
                    self.pair.c.http("GET", b"/sd/" + raw, port=8080).status, 200, name
                )
            (self.pair.card_c / name).unlink()

    def test_an_overlong_target_is_414_on_both(self) -> None:
        """HTTPD_MAX_URI_LEN, from both sides. Below it the name is merely
        too long for safe_name (400); above it no handler runs at all."""
        prefix = b"/api/files/"
        for n, want in (
            (wire.MAX_URI - len(prefix), 400),
            (wire.MAX_URI - len(prefix) + 1, 414),
            (wire.MAX_URI + 100, 414),
        ):
            target = prefix + b"a" * n
            c, e = self.pair.both("PUT", target, b"x")
            self.assertEqual((c.status, c.body), (e.status, e.body), n)
            self.assertEqual(c.status, want, n)


@unittest.skipIf(COMPILER is None and not IN_CI, "no host C++ compiler")
class TestByteRules(unittest.TestCase):
    """safe_name, url_decode, json_escape and url_encode, in C against their
    port.

    sd_web_util.h's four functions are where a name stops being a string
    and becomes a decision: what may be written to the card, what may be
    played, and what may go out inside the unescaped JSON of /api/files.
    tests/test_firmware_names.py holds castle_emu_wire to them by reading
    the C; this runs it.
    """

    tmp: ClassVar[str]
    rules: ClassVar[CastleC]

    @classmethod
    def setUpClass(cls) -> None:
        if COMPILER is None:
            raise AssertionError("CI is set and no host C++ compiler is on PATH")
        binary, built = compiled()
        assert built.returncode == 0, built.stderr
        cls.tmp = tempfile.mkdtemp(prefix="castle-web-rules-")
        card = Path(cls.tmp) / "card"
        card.mkdir()
        cls.rules = CastleC(binary, card, ("--rules",))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.rules.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def names(self) -> list[bytes]:
        rng = random.Random(SEED)
        out = [fuzz_corpus.name(rng).encode() for _ in range(CASES * 2)]
        # The edges the alphabet alone would take a long time to assemble.
        out += [
            b"",
            b".",
            b"..",
            b"...",
            b"a" * 98,
            b"a" * 99,
            b"a" * 100,
            b"%00",
            b"a%00b",
            b"%zz",
            b"%4",
            b"%",
            b"%%",
            b"+",
            b"%2B",
            b"%2F",
            b"%2f",
            b"%C3%A9",
            b"%E5%90%8D",
            b"%80",
            b"%ff",
            b"%7f",
            b"%22",
            b"%5C",
            b"%0a",
            b"%0d",
            b"%09",
            b"%1f",
            b"%20",
            b'a"b',
            b"a\\b",
            b"\x7f",
            b"\xc3\xa9",
        ]
        return out

    def test_the_four_byte_rules_agree_name_for_name(self) -> None:
        names = self.names()
        answers = self.rules.rules(names)
        self.assertEqual(len(answers), len(names))
        for name, (safe, dec, esc, enc) in zip(names, answers, strict=True):
            want_dec = wire.url_decode(name)
            want_esc = wire.json_escape(name.decode("utf-8", "surrogateescape")).encode(
                "utf-8", "surrogateescape"
            )
            self.assertEqual(dec, want_dec, f"url_decode({name!r})")
            self.assertEqual(safe, wire.safe_name(want_dec), f"safe_name({name!r})")
            self.assertEqual(esc, want_esc, f"json_escape({name!r})")
            self.assertEqual(enc, wire.url_encode(want_dec), f"url_encode({name!r})")

    def test_encoding_a_name_and_decoding_it_again_gives_the_name_back(self) -> None:
        """The property the loopback URL needed and never had (grade report
        2026-09-17 J1): whatever the card is holding, url_encode it and the
        server's own url_decode must hand back exactly that. It did not for
        `a+b.mp3` (a space came back) or `100%.mp3` (nothing came back), and
        those two files could be listed, queued and never played."""
        names = [
            *self.names(),
            b"a+b.mp3",
            b"100%25.mp3",
            b"a%20b.mp3",
            b"x%254.mp3",
            b"%23hash%3Fquery.mp3",
            b"scenes%2F01_vigil.mp3",
        ]
        answers = self.rules.rules(names)
        # The encoded form goes back THROUGH the C: this is the firmware's
        # own encoder against the firmware's own decoder, not a Python one.
        back = self.rules.rules([enc for (_s, _d, _e, enc) in answers])
        urlsafe = set(wire.UNRESERVED) | {0x25}  # unreserved, '/' and '%'
        round_tripped = 0
        for name, (safe, dec, _esc, enc), (_s2, dec2, _e2, _n2) in zip(
            names, answers, back, strict=True
        ):
            if not safe:
                continue  # never reaches a media URL: /api/play refuses it
            self.assertEqual(dec2, dec, f"{name!r} encoded to {enc!r}")
            self.assertLessEqual(set(enc), urlsafe, f"{enc!r} is not URL-safe")
            round_tripped += 1
        self.assertGreater(round_tripped, 20, "the corpus admitted almost nothing")

    def test_the_poison_set_is_really_refused(self) -> None:
        """fuzz_corpus.POISON is the oracle's claim about what safe_name
        must not let through (grade report 2026-09-06 J1). Here the claim
        is put to the C rather than to a second Python copy of it."""
        names = [b"ok%%%02x.mp3" % b for b in fuzz_corpus.POISON]
        for name, (safe, dec, _esc, _enc) in zip(
            names, self.rules.rules(names), strict=True
        ):
            self.assertTrue(fuzz_corpus.poisoned_text(dec), name)
            self.assertFalse(safe, f"safe_name let {name!r} through")

    def test_every_byte_decides_the_same_way_on_its_own(self) -> None:
        """Percent-encoded, one byte at a time. Encoded rather than raw
        because url_decode reads a `const char *`: a raw NUL ends its input
        there, and on the device a raw NUL cannot be in a request target in
        the first place — %00 is the only way one gets into a name."""
        names = [b"a%%%02x.mp3" % b for b in range(256)]
        for name, (safe, dec, esc, _enc) in zip(
            names, self.rules.rules(names), strict=True
        ):
            self.assertEqual(dec, wire.url_decode(name), name)
            self.assertEqual(safe, wire.safe_name(dec), name)
            self.assertEqual(
                esc,
                wire.json_escape(name.decode("utf-8", "surrogateescape")).encode(
                    "utf-8", "surrogateescape"
                ),
                name,
            )


if __name__ == "__main__":
    unittest.main()
