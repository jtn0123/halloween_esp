"""The owner's settings and page (v5.75, v5.76), run twice: the real C and the emulator.

firmware/castle_owner.h gave the castle a timezone, a volume cap and quiet
hours, all set through POST /api/settings and all off until set; the owner's
page (sd_web_owner.h) is how someone who did not build the castle reaches
them, and the buyer build has no motion sensor to offer. Each class below is
a castle of its own — settings outlive a request, and quiet hours outlive a
tick — driven through the same requests and the same timeline on both sides
(tests/firmware_web_harness.py), with the C's main loop ticked explicitly and
the emulator's own ticker given the time to catch up.

The clock is the point of most of it, so the timeline is chosen to bite: a
US Eastern castle whose quiet hours start on Halloween night, the same night
daylight saving ends at 2 am — the window opens in EDT and closes in EST.
"""

from __future__ import annotations

import json
import re
import sys
import time
import unittest
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

from firmware_web_harness import WebPairCase

#: US Eastern, url-encoded the way the owner's page sends it.
EASTERN = b"EST5EDT%2CM3.2.0%2CM11.1.0"
#: 2026-10-31 23:30 EDT, inside a 22:00-07:00 window, 2.5 hours before
#: the clocks go back.
HALLOWEEN_2330 = 1793503800
#: 2026-11-01 07:00 EST: the window is over, an hour later in UTC than it
#: would have been the night before.
MORNING_0700 = 1793534400
#: 2026-10-31 12:00 EDT: daytime, and a set clock.
NOON = 1793462400
TICK_US = 200_000
#: Fields every class compares across the two castles.
OWNER_FIELDS = ("volume", "vol_max", "quiet_now", "tz", "quiet", "local")


class OwnerCase(WebPairCase):
    now: ClassVar[int] = 1_000_000

    def tick(self, epoch: int | None = None, n: int = 2) -> None:
        """`n` ticks of the C castle at wall-clock `epoch`, and the time for
        the emulator's ticker to do the same. Two by default: a pull lands
        on the player on one tick and in the mirror on the next."""
        for _ in range(n):
            type(self).now += TICK_US
            self.pair.tick(self.now, epoch=epoch)
        time.sleep(0.5)

    def owner(self) -> dict[str, object]:
        """The owner's fields of /api/status — the same on both castles."""
        c, e = self.pair.both("GET", b"/api/status")
        cj, ej = json.loads(c.body), json.loads(e.body)
        mine = {k: cj[k] for k in OWNER_FIELDS}
        self.assertEqual(mine, {k: ej[k] for k in OWNER_FIELDS})
        return mine

    def settings(self, query: bytes) -> dict[str, object]:
        r = self.same("POST", b"/api/settings?" + query)
        self.assertEqual(r.status, 200, r.body)
        body: dict[str, object] = json.loads(r.body)
        return body


class TestSettingsRoundTrip(OwnerCase):
    def test_every_setting_saves_reads_back_and_resets(self) -> None:
        self.assertEqual(
            self.settings(b"tz=" + EASTERN + b"&vol_max=55&quiet=22:00-07:00"),
            {
                "boot_play": True,
                "vol_max": 55,
                "tz": "EST5EDT,M3.2.0,M11.1.0",
                "quiet": "22:00-07:00",
            },
        )
        # One at a time leaves the others alone; "off" ends the window.
        self.assertEqual(self.settings(b"quiet=off")["quiet"], "")
        self.assertEqual(self.settings(b"vol_max=100")["tz"], "EST5EDT,M3.2.0,M11.1.0")
        st = self.owner()
        self.assertEqual(
            (st["vol_max"], st["tz"], st["quiet"]), (100, "EST5EDT,M3.2.0,M11.1.0", "")
        )
        self.settings(b"vol_max=30&quiet=23:15-06:45")
        r = self.same("POST", b"/api/factory-reset?confirm=yes")
        self.assertEqual(r.status, 200)
        st = self.owner()
        self.assertEqual((st["vol_max"], st["tz"], st["quiet"]), (100, "", ""))

    def test_what_the_castle_refuses_it_refuses_identically(self) -> None:
        for query, why in (
            (b"vol_max=0", b"bad vol_max"),
            (b"vol_max=101", b"bad vol_max"),
            (b"vol_max=1000", b"bad vol_max"),
            (b"vol_max=-5", b"bad vol_max"),
            (b"vol_max=5%25", b"bad vol_max"),
            (b"quiet=22:00-22:00", b"bad quiet"),
            (b"quiet=24:00-07:00", b"bad quiet"),
            (b"quiet=22:60-07:00", b"bad quiet"),
            (b"quiet=22:00", b"bad quiet"),
            (b"quiet=22:00-07:00x", b"bad quiet"),
            (b"quiet=2200-0700", b"bad quiet"),
            (b"quiet=OFF", b"bad quiet"),
            (b"tz=Europe/London", b"bad tz"),
            (b"tz=EST5EDT", b"bad tz"),
            (b"tz=EST5%00", b"bad tz"),
            (b"tz=" + b"A" * 3 + b"5" * 62, b"bad tz"),
            (b"tz=PST8PDT%2CM3.2.0%2CM11.1.0%20", b"bad tz"),
            # In the C's order: the first bad field is the one named, and
            # nothing of a refused request is kept.
            (b"boot_play=maybe&tz=nope", b"bad boot_play"),
            (b"tz=nope&vol_max=0", b"bad tz"),
            (b"vol_max=50&quiet=nope", b"bad quiet"),
            (b"nothing=1", b"need boot_play=, tz=, vol_max= or quiet="),
            (b"tz=&vol_max=", b"need boot_play=, tz=, vol_max= or quiet="),
        ):
            r = self.same("POST", b"/api/settings?" + query)
            self.assertEqual((r.status, r.body), (400, why), query)
        self.assertEqual(self.owner()["vol_max"], 100)  # the 50 was not kept


class TestVolumeCap(OwnerCase):
    def test_the_cap_pulls_the_speaker_down_and_holds_it(self) -> None:
        self.tick()
        self.assertEqual(self.owner()["volume"], 70)
        self.settings(b"vol_max=40")
        self.tick()
        self.assertEqual(self.owner()["volume"], 40)
        self.same("POST", b"/api/volume?v=90")
        self.tick()
        self.assertEqual(self.owner()["volume"], 40)
        self.same("POST", b"/api/volume?v=25")  # under it is the owner's call
        self.tick()
        self.assertEqual(self.owner()["volume"], 25)
        # Lifting the cap gives nothing back: it was a ceiling, not a level.
        self.settings(b"vol_max=100")
        self.tick()
        self.assertEqual(self.owner()["volume"], 25)


class TestQuietHours(OwnerCase):
    def test_halloween_night_goes_quiet_and_the_morning_gives_it_back(self) -> None:
        self.settings(b"tz=" + EASTERN + b"&quiet=22:00-07:00")
        self.tick(NOON)
        st = self.owner()
        self.assertEqual(
            (st["quiet_now"], st["volume"], st["local"]),
            (False, 70, "2026-10-31 12:00"),
        )
        self.tick(HALLOWEEN_2330)
        st = self.owner()
        self.assertEqual(
            (st["quiet_now"], st["volume"], st["local"]), (True, 0, "2026-10-31 23:30")
        )
        # Asked for during the window: held at 0, and remembered.
        self.same("POST", b"/api/volume?v=60")
        self.tick(HALLOWEEN_2330 + 60)
        self.assertEqual(self.owner()["volume"], 0)
        # 06:59 EST is still quiet — in EDT terms that is 07:59, so a castle
        # that forgot the clocks went back would already be loud.
        self.tick(MORNING_0700 - 60)
        self.assertEqual(self.owner()["quiet_now"], True)
        self.tick(MORNING_0700)
        st = self.owner()
        self.assertEqual(
            (st["quiet_now"], st["volume"], st["local"]),
            (False, 60, "2026-11-01 07:00"),
        )

    def test_a_castle_with_no_clock_is_never_quiet(self) -> None:
        # No SNTP answer means no local time, and no window: a castle on a
        # network with no internet must not go mute for ever.
        self.settings(b"tz=UTC0&quiet=00:00-23:59")
        self.tick(0)
        st = self.owner()
        self.assertEqual((st["quiet_now"], st["local"]), (False, ""))
        self.settings(b"quiet=off")


class TestNoMotionSensor(OwnerCase):
    """The buyer build: castle_buyer.yaml's pir_fitted: "false"."""

    env: ClassVar[dict[str, str]] = {"CASTLE_PIR_FITTED": "0"}

    def test_the_sensor_is_absent_and_cannot_be_armed(self) -> None:
        c, e = self.pair.both("GET", b"/api/status")
        self.assertFalse(json.loads(c.body)["pir"]["fitted"])
        self.assertFalse(json.loads(e.body)["pir"]["fitted"])
        for q in (b"armed=1", b"cooldown=30", b"scene=storm", b"armed=banana"):
            r = self.same("POST", b"/api/pir?" + q)
            self.assertEqual((r.status, r.body), (409, b"no motion sensor"), q)
        # The key still comes first: a locked castle does not say what it has.
        self.same("POST", b"/api/key?new=k3y")
        r = self.same("POST", b"/api/pir?armed=1")
        self.assertEqual(r.status, 401)
        self.pair.send_key(b"k3y")
        self.same("POST", b"/api/key?clear=1")
        self.pair.send_key(b"")


class TestOwnerPage(OwnerCase):
    def test_the_owner_page_is_served_from_flash_at_owner_and_at_root(self) -> None:
        page = self.same("GET", b"/owner")
        self.assertEqual(page.status, 200)
        self.assertIn("Content-Security-Policy", page.extra)
        for words in (b"No SD card", b"the show is on the card", b"Report a problem",
                      b"Motion sensor: not fitted", b"Quiet hours", b"Time zone",
                      b"No songs on the card yet"):  # fmt: skip
            self.assertIn(words, page.body)
        # v5.76: the song list is every format the Feather build can play —
        # 5.75's left .opus out, and a card of opus songs read as empty.
        feather = (ROOT / "firmware" / "castle_feather_s3.yaml").read_text(
            encoding="utf-8"
        )
        codecs = re.search(r"\n  codecs:\n((?:    \w+:\n)+)", feather)
        assert codecs, "castle_feather_s3.yaml declares its playback codecs"
        decoded = sorted(re.findall(r"(\w+):", codecs.group(1)))
        listed = re.search(rb"/\\\.\(([\w|]+)\)\$/i\.test\(f\.name\)", page.body)
        assert listed, "the owner's page filters the card listing by suffix"
        self.assertEqual(sorted(listed.group(1).decode().split("|")), decoded)
        # `/` is the card's own site while it has one...
        self.assertNotEqual(self.same("GET", b"/").body, page.body)
        # ...and the owner's page once it has not.
        for card in (self.pair.card_c, self.pair.card_e):
            for f in ("index.html", "index.html.gz"):
                (card / "site" / f).unlink()
        self.assertEqual(self.same("GET", b"/").body, page.body)


class TestCrashIsReported(OwnerCase):
    env: ClassVar[dict[str, str]] = {
        "CASTLE_RESET": "9",
        "CASTLE_BOOTS": "12",
        "CASTLE_CRASHES": "2",
    }

    def test_a_brownout_boot_reads_the_same_on_both_castles(self) -> None:
        r = self.same("GET", b"/api/health")
        h = json.loads(r.body)
        self.assertEqual(
            (h["boots"], h["crashes"], h["last_reset"], h["was_crash"]),
            (12, 2, "BROWNOUT", True),
        )


if __name__ == "__main__":
    unittest.main()
