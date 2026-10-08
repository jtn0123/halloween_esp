"""The castle's local time — firmware/castle_tz.h, ported line for line.

The owner sets a POSIX TZ string (/api/settings?tz=, v5.75) and the castle
turns UTC into the wall time quiet hours and the owner's page are measured
in. The firmware does that with its own ~150-line parser rather than libc
(castle_tz.h says why), so the emulator cannot lean on `time.tzset()`
either — and must not: it runs inside the test process, and TZ is process
global.

tests/test_castle_tz_cxx.py runs the C header and this module over the same
corpus and holds both to Python's zoneinfo, which is the independent oracle.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: castle_tz::kTzMax
TZ_MAX = 63

_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


@dataclass
class Rule:
    kind: str = "M"  # "M" (Mm.w.d), "J" (Jn, no Feb 29) or "n" (counts it)
    day: int = 0
    month: int = 0
    week: int = 0
    secs: int = 7200


@dataclass
class Zone:
    std_east: int = 0
    dst_east: int = 0
    has_dst: bool = False
    start: Rule = field(default_factory=Rule)
    end: Rule = field(default_factory=Rule)


@dataclass
class Local:
    year: int
    month: int
    day: int
    hour: int
    minute: int
    second: int
    wday: int  # 0 = Sunday
    dst: bool


class _Cursor:
    def __init__(self, s: str) -> None:
        self.s, self.i = s, 0

    def peek(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def take(self) -> str:
        c = self.peek()
        self.i += 1
        return c


def _alpha(c: str) -> bool:
    return len(c) == 1 and ("A" <= c <= "Z" or "a" <= c <= "z")


def _digit(c: str) -> bool:
    return len(c) == 1 and "0" <= c <= "9"


def _name(c: _Cursor) -> bool:
    start = c.i
    if c.peek() == "<":
        c.i += 1
        while c.peek() not in ("", ">"):
            ch = c.peek()
            if not (_alpha(ch) or _digit(ch) or ch in "+-"):
                return False
            c.i += 1
        if c.peek() == "" or c.i - start - 1 < 3:
            return False
        c.i += 1
        return True
    while _alpha(c.peek()):
        c.i += 1
    return c.i - start >= 3


def _number(c: _Cursor, max_digits: int, hi: int) -> int | None:
    start, out = c.i, 0
    while _digit(c.peek()) and c.i - start < max_digits:
        out = out * 10 + int(c.take())
    return out if c.i > start and out <= hi else None


def _hms(c: _Cursor, max_h: int) -> int | None:
    sign = 1
    if c.peek() in ("+", "-"):
        sign = -1 if c.take() == "-" else 1
    h = _number(c, 3, max_h)
    if h is None:
        return None
    m = s = 0
    if c.peek() == ":":
        c.i += 1
        got = _number(c, 2, 59)
        if got is None:
            return None
        m = got
        if c.peek() == ":":
            c.i += 1
            got = _number(c, 2, 59)
            if got is None:
                return None
            s = got
    return sign * (h * 3600 + m * 60 + s)


def _rule(c: _Cursor) -> Rule | None:
    r = Rule()
    if c.peek() == "M":
        c.i += 1
        month = _number(c, 2, 12)
        if month is None or month < 1 or c.take() != ".":
            return None
        week = _number(c, 1, 5)
        if week is None or week < 1 or c.take() != ".":
            return None
        day = _number(c, 1, 6)
        if day is None:
            return None
        r.kind, r.month, r.week, r.day = "M", month, week, day
    elif c.peek() == "J":
        c.i += 1
        day = _number(c, 3, 365)
        if day is None or day < 1:
            return None
        r.kind, r.day = "J", day
    else:
        day = _number(c, 3, 365)
        if day is None:
            return None
        r.kind, r.day = "n", day
    if c.peek() == "/":
        c.i += 1
        secs = _hms(c, 167)
        if secs is None:
            return None
        r.secs = secs
    return r


def parse(s: str) -> Zone | None:
    """castle_tz::parse — the whole string, or None."""
    if not s or len(s) > TZ_MAX:
        return None
    c = _Cursor(s)
    if not _name(c):
        return None
    west = _hms(c, 24)
    if west is None:
        return None
    z = Zone(std_east=-west)
    if c.i == len(s):
        return z
    if not _name(c):
        return None
    z.has_dst, z.dst_east = True, z.std_east + 3600
    if c.peek() not in ("", ","):
        west = _hms(c, 24)
        if west is None:
            return None
        z.dst_east = -west
    if c.take() != ",":
        return None  # a dst name with no rules is refused, never guessed
    start = _rule(c)
    if start is None or c.take() != ",":
        return None
    end = _rule(c)
    if end is None or c.i != len(s):
        return None
    z.start, z.end = start, end
    return z


def tz_ok(raw: bytes) -> bool:
    """What /api/settings asks of a tz= value: ASCII that parses."""
    try:
        return parse(raw.decode("ascii")) is not None
    except UnicodeDecodeError:
        return False


def days_from_civil(y: int, m: int, d: int) -> int:
    y -= m <= 2
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def civil_from_days(z: int) -> tuple[int, int, int]:
    z += 719468
    era = z // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    return yoe + era * 400 + (m <= 2), m, d


def _leap(y: int) -> bool:
    return (y % 4 == 0 and y % 100 != 0) or y % 400 == 0


def _month_days(y: int, m: int) -> int:
    return 29 if m == 2 and _leap(y) else _DAYS[m - 1]


def rule_day(r: Rule, y: int) -> int:
    jan1 = days_from_civil(y, 1, 1)
    if r.kind == "n":
        return jan1 + r.day
    if r.kind == "J":
        return jan1 + r.day - 1 + (1 if _leap(y) and r.day >= 60 else 0)
    first = days_from_civil(y, r.month, 1)
    wd_first = (first + 4) % 7
    mday = 1 + (r.day - wd_first + 7) % 7 + 7 * (r.week - 1)
    while mday > _month_days(y, r.month):
        mday -= 7
    return first + mday - 1


def local(utc: int, z: Zone) -> Local:
    """castle_tz::local — UTC seconds to wall time in `z`."""
    east, dst = z.std_east, False
    if z.has_dst:
        y, _m, _d = civil_from_days((utc + z.std_east) // 86400)
        on = rule_day(z.start, y) * 86400 + z.start.secs - z.std_east
        off = rule_day(z.end, y) * 86400 + z.end.secs - z.dst_east
        dst = (on <= utc < off) if on < off else (utc >= on or utc < off)
        if dst:
            east = z.dst_east
    days, rem = divmod(utc + east, 86400)
    y, m, d = civil_from_days(days)
    return Local(y, m, d, rem // 3600, rem % 3600 // 60, rem % 60, (days + 4) % 7, dst)
