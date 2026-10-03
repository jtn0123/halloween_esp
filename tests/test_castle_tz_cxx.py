"""The castle's clock, three ways: firmware/castle_tz.h run on the host,
tools/castle_emu_tz.py, and Python's zoneinfo as the oracle neither wrote.

v5.75 gave the owner a timezone (/api/settings?tz=, a POSIX TZ string) and
quiet hours measured in it. The board turns UTC into wall time with its own
parser rather than libc's (castle_tz.h says why), so this suite is what
stands between a castle and an hour's error twice a year: every zone in the
owner page's list, every DST change from 2024 to 2031 to the second, and a
year swept every few hours — the C, the port and the tz database must agree
on all of it. And the strings the castle REFUSES are refused by both.
"""

from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

import castle_emu_tz as tz
import cxx_compiler

SRC = ROOT / "tests" / "cxx" / "tz_check.cpp"
COMPILER = cxx_compiler.COMPILER
IN_CI = bool(os.environ.get("CI"))

#: POSIX string → the IANA zone it describes today. The owner page offers
#: most of these (sd_web_owner.h); the rest are the shapes worth having —
#: a half-hour offset, a 45-minute one with quoted names, a southern summer
#: across New Year, a zone whose DST changes at 01:00 UTC.
ZONES = {
    "UTC0": "UTC",
    "PST8PDT,M3.2.0,M11.1.0": "America/Los_Angeles",
    "MST7MDT,M3.2.0,M11.1.0": "America/Denver",
    "MST7": "America/Phoenix",
    "CST6CDT,M3.2.0,M11.1.0": "America/Chicago",
    "EST5EDT,M3.2.0,M11.1.0": "America/New_York",
    "AKST9AKDT,M3.2.0,M11.1.0": "America/Anchorage",
    "HST10": "Pacific/Honolulu",
    "NST3:30NDT,M3.2.0,M11.1.0": "America/St_Johns",
    "<-03>3": "America/Sao_Paulo",
    "GMT0BST,M3.5.0/1,M10.5.0": "Europe/London",
    "CET-1CEST,M3.5.0,M10.5.0/3": "Europe/Berlin",
    "EET-2EEST,M3.5.0/3,M10.5.0/4": "Europe/Helsinki",
    "IST-5:30": "Asia/Kolkata",
    "<+0530>-5:30": "Asia/Kolkata",
    "JST-9": "Asia/Tokyo",
    "AEST-10AEDT,M10.1.0,M4.1.0/3": "Australia/Sydney",
    "NZST-12NZDT,M9.5.0,M4.1.0/3": "Pacific/Auckland",
    "<+1245>-12:45<+1345>,M9.5.0/2:45,M4.1.0/3:45": "Pacific/Chatham",
}

#: Grammar the oracle cannot speak to (zoneinfo has no "J60" zone) but the
#: two implementations must still agree on, to the second.
EXOTIC = [
    "EST5EDT,J60,J300",
    "EST5EDT,0,365",
    "EST5EDT4,M3.2.0/-1,M11.1.0/26",
    "XXX3YYY,M1.1.0/0,M12.5.6/167",
    "ABC-14",
    "ABC+12:30:15",
]

REFUSED = [
    "",
    "UTC",  # no offset
    "EST5EDT",  # dst with no rules: refused, never guessed
    "PST8PDT,M3.2.0",
    "PST8PDT,M3.2.0,",
    "PST8PDT,M13.2.0,M11.1.0",
    "PST8PDT,M0.2.0,M11.1.0",
    "PST8PDT,M3.6.0,M11.1.0",
    "PST8PDT,M3.0.0,M11.1.0",
    "PST8PDT,M3.2.7,M11.1.0",
    "PST8PDT,M3.2.0/168,M11.1.0",
    "EST5EDT,J0,J365",
    "EST5EDT,366,1",
    "AB5",
    "<+5>-5",
    "<+05",
    "<+0 5>-5",
    "EST25",
    "EST5:60",
    "EST5:30:60",
    "EST5 ",
    "EST5\t",
    "Europe/London",
    "CET-1CEST,M3.5.0,M10.5.0/3,",
    "CET-1CEST,M3.5.0,M10.5.0/3x",
    "EéT5",
    "X" * 3 + "5" + "," * 60,
    "ABC" + "1" * 61,
]


def oracle(epoch: int, zone: str) -> tuple[int, ...]:
    t = dt.datetime.fromtimestamp(epoch, ZoneInfo(zone))
    dst = t.dst()
    return (
        t.year,
        t.month,
        t.day,
        t.hour,
        t.minute,
        t.second,
        (t.weekday() + 1) % 7,
        int(bool(dst)),
    )


def port(epoch: int, s: str) -> tuple[int, ...] | None:
    z = tz.parse(s)
    if z is None:
        return None
    t = tz.local(epoch, z)
    return (t.year, t.month, t.day, t.hour, t.minute, t.second, t.wday, int(t.dst))


def transitions(zone: str) -> list[int]:
    """Every UTC offset change in 2024..2031, found by walking the zone a
    quarter hour at a time and bisecting each step that changes."""
    zi = ZoneInfo(zone)

    def off(e: int) -> object:
        return dt.datetime.fromtimestamp(e, zi).utcoffset()

    out = []
    t = int(dt.datetime(2024, 1, 1, tzinfo=dt.UTC).timestamp())
    end = int(dt.datetime(2032, 1, 1, tzinfo=dt.UTC).timestamp())
    step = 900
    while t < end:
        if off(t) != off(t + step):
            lo, hi = t, t + step
            while hi - lo > 1:
                mid = (lo + hi) // 2
                lo, hi = (mid, hi) if off(mid) == off(lo) else (lo, mid)
            out.append(hi)
        t += step
    return out


def instants(zone: str) -> list[int]:
    """The moments worth asking about: around every change, and a sweep."""
    out: list[int] = []
    for edge in transitions(zone):
        out += [edge + d for d in (-3601, -1, 0, 1, 3600)]
    start = int(dt.datetime(2026, 1, 1, tzinfo=dt.UTC).timestamp())
    out += list(range(start, start + 366 * 86400, 7 * 3600 + 13))
    # New Year in every zone's own neighbourhood. Not the epoch: the tz
    # database knows what Alaska's clocks said in 1969, and the castle only
    # ever asks about a clock SNTP has set.
    out += [int(dt.datetime(y, 1, 1, tzinfo=dt.UTC).timestamp()) + h * 3600
            for y in (2025, 2026, 2027) for h in range(-14, 15)]  # fmt: skip
    return out


@unittest.skipIf(COMPILER is None and not IN_CI, "no host C++ compiler")
class TestCastleTz(unittest.TestCase):
    binary: Path
    tmp: tempfile.TemporaryDirectory[str]

    @classmethod
    def setUpClass(cls) -> None:
        if COMPILER is None:
            raise AssertionError("CI is set and no host C++ compiler is on PATH")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.tmp.name) / "tz_check"
        built = subprocess.run(
            [COMPILER, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
             "-I", str(ROOT / "firmware"), str(SRC), "-o", str(cls.binary)],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        assert built.returncode == 0, built.stderr

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def c(self, asks: list[tuple[int, str]]) -> list[tuple[int, ...] | None]:
        feed = "".join(f"{e} {s}\n" for e, s in asks)
        out = subprocess.run(
            [str(self.binary)], input=feed.encode("latin-1"), capture_output=True,
            check=True,
        ).stdout.decode().splitlines()  # fmt: skip
        self.assertEqual(len(out), len(asks))
        return [
            None if ln == "bad" else tuple(int(x) for x in ln.split()) for ln in out
        ]

    def test_every_zone_agrees_with_the_tz_database(self) -> None:
        for s, zone in ZONES.items():
            asks = [(e, s) for e in instants(zone)]
            got = self.c(asks)
            for (e, _), c_says in zip(asks, got, strict=True):
                want = oracle(e, zone)
                self.assertEqual(c_says, want, f"C: {s} at {e}")
                self.assertEqual(port(e, s), want, f"port: {s} at {e}")

    def test_the_grammar_the_oracle_cannot_check_still_agrees(self) -> None:
        start = int(dt.datetime(2027, 1, 1, tzinfo=dt.UTC).timestamp())
        epochs = range(start - 86400 * 400, start + 86400 * 400, 3 * 3600 + 7)
        for s in EXOTIC:
            asks = [(e, s) for e in epochs]
            for (e, _), c_says in zip(asks, self.c(asks), strict=True):
                self.assertIsNotNone(c_says, s)
                self.assertEqual(c_says, port(e, s), f"{s} at {e}")

    def test_the_same_strings_are_refused(self) -> None:
        # Strings with a newline cannot ride the line protocol; the C never
        # sees one either, because tz_ok's caller is a URL-decoded value and
        # castle_tz's grammar has no control characters in it.
        for s in REFUSED:
            self.assertIsNone(tz.parse(s), s)
            self.assertFalse(tz.tz_ok(s.encode("utf-8")), s)
            self.assertEqual(self.c([(0, s)]), [None], repr(s))
        for s in [*ZONES, *EXOTIC]:
            self.assertIsNotNone(tz.parse(s), s)
            self.assertTrue(tz.tz_ok(s.encode()), s)


if __name__ == "__main__":
    unittest.main()
