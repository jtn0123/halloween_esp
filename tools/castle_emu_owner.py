"""The owner's clock and limits — firmware/castle_owner.h, ported (v5.75).

A timezone, a volume cap and quiet hours: NVS prefs on the board, set by
POST /api/settings, all OFF until set. castle_owner.h is the whole story
(what quiet hours silence and what they leave running, why no clock means
no quiet hours); this is the same arithmetic on the emulated castle, so a
pair test can drive both through one window and read the same answers.

The emulator's settings live as long as it does, which is its boot — the
same lifetime castle_emu.py gives the key and boot_play.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import castle_emu_tz as ctz

#: castle_owner.h kQuietOff / kClockSet.
QUIET_OFF = -1
CLOCK_SET = 1577836800


def vol_max_ok(v: bytes) -> int | None:
    """castle_web::vol_max_ok: 1..100, digits only, or None."""
    if not v or len(v) > 3 or not all(0x30 <= b <= 0x39 for b in v):
        return None
    n = int(v)
    return n if 1 <= n <= 100 else None


def quiet_ok(q: bytes) -> int | None:
    """castle_web::quiet_ok: "HH:MM-HH:MM" packed as from*1440+to, "off" as
    QUIET_OFF, anything else None."""
    if q == b"off":
        return QUIET_OFF
    if len(q) != 11 or q[2:3] != b":" or q[5:6] != b"-" or q[8:9] != b":":
        return None
    pairs = [q[i : i + 2] for i in (0, 3, 6, 9)]
    if not all(0x30 <= b <= 0x39 for p in pairs for b in p):
        return None
    h1, m1, h2, m2 = (int(p) for p in pairs)
    if h1 > 23 or m1 > 59 or h2 > 23 or m2 > 59:
        return None
    start, end = h1 * 60 + m1, h2 * 60 + m2
    return start * 1440 + end if start != end else None


def in_quiet(minute: int, packed: int) -> bool:
    if packed < 0:
        return False
    start, end = divmod(packed, 1440)
    return start <= minute < end if start < end else (minute >= start or minute < end)


@dataclass
class Owner:
    """castle_owner.h's globals, and the functions that read them."""

    tz: str = ""
    zone: ctz.Zone = field(default_factory=ctz.Zone)
    vol_max: int = 100
    quiet: int = QUIET_OFF
    quiet_now: bool = False
    local: str = ""
    was_quiet: bool = False
    quiet_took: int = -1

    def set_tz(self, s: str) -> None:
        z = ctz.parse(s) if s else ctz.Zone()
        if z is not None:
            self.tz, self.zone = s, z

    def quiet_str(self) -> str:
        if self.quiet < 0:
            return ""
        start, end = divmod(self.quiet, 1440)
        return "%02d:%02d-%02d:%02d" % (start // 60, start % 60, end // 60, end % 60)

    def ceiling(self) -> int:
        """castle_web::volume_ceiling_pct."""
        return 0 if self.quiet_now else self.vol_max

    def volume_for(self, asked: int) -> int:
        """castle_web::volume_for — the VOLUME action's level for now."""
        if self.was_quiet:
            self.quiet_took = asked
        return min(asked, self.ceiling())

    def tick(self, utc: int, volume: int) -> int:
        """castle_web::owner_tick: the level to pull the speaker to, or -1."""
        quiet = False
        if utc > CLOCK_SET:
            t = ctz.local(utc, self.zone)
            self.local = "%04d-%02d-%02d %02d:%02d" % (
                t.year,
                t.month,
                t.day,
                t.hour,
                t.minute,
            )
            quiet = in_quiet(t.hour * 60 + t.minute, self.quiet)
        else:
            self.local = ""
        self.quiet_now = quiet
        pull = -1
        if quiet and not self.was_quiet:
            self.quiet_took = volume
        if not quiet and self.was_quiet:
            if volume == 0 and self.quiet_took > 0:
                pull = min(self.quiet_took, self.vol_max)
            self.quiet_took = -1
        self.was_quiet = quiet
        if pull < 0 and volume > self.ceiling():
            pull = self.ceiling()
        return pull

    def reset(self) -> None:
        """castle_web::owner_reset — factory reset."""
        self.set_tz("")
        self.vol_max, self.quiet = 100, QUIET_OFF

    def settings_json(self, boot_play: bool) -> bytes:
        """sd_web_prefs.h settings_json, byte for byte."""
        return (
            '{"boot_play":%s,"vol_max":%d,"tz":"%s","quiet":"%s"}'
            % (
                "true" if boot_play else "false",
                self.vol_max,
                self.tz,  # tz_ok admits nothing json_escape would change
                self.quiet_str(),
            )
        ).encode()
