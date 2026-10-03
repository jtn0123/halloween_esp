"""The soak's arithmetic on made-up nights: tools/soak_track.py + soak_verdict.py.

No castle and no clock here — every reply is a dict and every time a number
of seconds, so a 72-hour night with a reboot hidden inside a Wi-Fi outage is
a dozen lines and runs in a millisecond. tests/test_soak.py runs the loop
itself against the emulator.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import castle_probe as probe
from soak_track import EVENT_RING, Tracker
from soak_verdict import Limits, limit_fields, passed, verdict

HEALTH = {
    "boots": 3,
    "crashes": 0,
    "last_reset": "power-on",
    "was_crash": False,
    "sd_read_errors": 0,
    "heap_min_kb": 64,
    "sd_last_error": "",
}


def status(up: int, **more: object) -> dict[str, object]:
    return {"version": "5.75", "uptime_s": up, "sd_mounted": True, **more}


def health(**more: object) -> dict[str, object]:
    return {**HEALTH, **more}


def ev(t: int, kind: str = "scene", arg: str = "") -> dict[str, object]:
    return {"t": t, "e": kind, "a": arg}


class TestReboots(unittest.TestCase):
    def test_a_steady_castle_never_reboots(self) -> None:
        t = Tracker()
        for i in range(10):
            self.assertEqual(t.sample(i * 10.0, status(1000 + i * 10)), [])
        self.assertEqual(t.summary(100.0)["reboots"], 0)

    def test_uptime_going_back_is_a_reboot_with_its_reason(self) -> None:
        t = Tracker()
        t.sample(0.0, status(5000))
        t.health(0.0, health())
        notes = t.sample(10.0, status(4))
        self.assertIn("REBOOT detected (uptime went back)", notes)
        # The boot counter rising beside it is the SAME reboot, not a second.
        notes = t.health(
            10.0, health(boots=4, last_reset="task-watchdog", was_crash=True)
        )
        self.assertEqual(notes, ["reset reason: task-watchdog — a CRASH"])
        s = t.summary(20.0)
        self.assertEqual((s["reboots"], s["crashes"]), (1, 1))
        self.assertEqual(s["reset_reasons"], ["task-watchdog"])
        self.assertEqual(s["first_boot_reason"], "power-on")

    def test_a_reboot_hidden_in_an_outage_is_still_seen(self) -> None:
        # Gone at 100 s, back at 1100 s with an uptime that GREW — but by
        # 200 s, not the 1000 that passed: it restarted while unreachable.
        t = Tracker()
        t.sample(90.0, status(100))
        self.assertEqual(t.miss(100.0, "refused"), "castle NOT answering: refused")
        self.assertIsNone(t.miss(110.0, "refused"))
        notes = t.sample(1100.0, status(300))
        self.assertIn("REBOOT detected (uptime went back)", notes)
        self.assertIn("castle back after 1000 s (it had rebooted)", notes)
        self.assertTrue(t.gaps[-1].rebooted)

    def test_the_boot_counter_catches_a_reboot_uptime_cannot(self) -> None:
        t = Tracker()
        t.sample(0.0, status(50))
        t.health(0.0, health())
        notes = t.health(30.0, health(boots=5, last_reset="BROWNOUT"))
        self.assertEqual(notes.count("REBOOT detected (boot counter rose)"), 2)
        self.assertEqual(t.summary(30.0)["crashes"], 1)  # the reason says crash

    def test_v575_reset_reason_is_read_first(self) -> None:
        self.assertEqual(
            probe.reset_reason({"reset_reason": "PANIC"}, health()), "PANIC"
        )
        self.assertEqual(probe.reset_reason(None, health(reset_reason="sw")), "sw")
        self.assertEqual(probe.reset_reason(None, health()), "power-on")
        self.assertEqual(probe.reset_reason(None, None), "")
        self.assertTrue(probe.is_crash("int-watchdog"))
        self.assertFalse(probe.is_crash("software"))


class TestOutages(unittest.TestCase):
    def test_blips_and_outages_are_told_apart(self) -> None:
        t = Tracker()
        t.sample(0.0, status(1000))
        t.miss(10.0, "timed out")
        t.sample(15.0, status(1015))  # 5 s: a blip
        t.miss(20.0, "refused")
        notes = t.sample(140.0, status(1140))  # 120 s: an outage, no reboot
        self.assertIn("castle back after 120 s (it stayed up)", notes)
        s = t.summary(150.0, outage_min_s=20.0)
        self.assertEqual(s["blips"], 1)
        self.assertEqual(len(s["outages"]), 1)
        self.assertEqual(s["longest_outage_s"], 120.0)
        self.assertTrue(s["answering_at_end"])

    def test_a_castle_that_never_came_back(self) -> None:
        t = Tracker()
        t.sample(0.0, status(1000))
        t.miss(10.0, "refused")
        s = t.summary(610.0)
        self.assertFalse(s["answering_at_end"])
        self.assertEqual(s["longest_outage_s"], 600.0)


class TestCounters(unittest.TestCase):
    def test_card_errors_count_from_the_start_of_the_run(self) -> None:
        t = Tracker()
        t.sample(0.0, status(1000))
        t.health(0.0, health(sd_read_errors=5))  # before the soak: not ours
        notes = t.health(30.0, health(sd_read_errors=7, sd_last_error="a.mp3@4096"))
        self.assertEqual(notes, ["card read errors: 2 this boot"])
        t.sample(40.0, status(3))  # a reboot zeroes the board's counter
        t.health(40.0, health(boots=4, sd_read_errors=1))
        s = t.summary(50.0)
        self.assertEqual(s["sd_read_errors"], 3)
        self.assertEqual(s["sd_last_error"], "a.mp3@4096")

    def test_status_readings(self) -> None:
        t = Tracker()
        t.sample(0.0, status(1, rssi=-60, heap_free_kb=90, light_evicted=7))
        t.sample(10.0, status(11, rssi=0, sync_drift_ms=-1, sync_lead_ms=-1))
        notes = t.sample(20.0, status(21, rssi=-85, sd_mounted=False, missing="a,b"))
        self.assertEqual(
            notes, ["card UNMOUNTED", "signal weak: -85 dBm", "card missing: a,b"]
        )
        notes = t.sample(30.0, status(31, rssi=-88, version="5.76", light_evicted=9))
        self.assertEqual(
            notes, ["firmware changed: 5.75 -> 5.76", "card mounted again"]
        )
        t.sample(40.0, status(41, version="5.76", sync_drift_ms=40, sync_lead_ms=300))
        t.health(40.0, health(heap_min_kb=31))
        t.health(50.0, health(heap_min_kb=44))
        s = t.summary(50.0)
        self.assertEqual(s["unmounted_samples"], 1)
        self.assertEqual(
            (s["rssi_min"], s["rssi_drops"], s["rssi_weak_pct"]), (-88, 1, 66.7)
        )
        self.assertEqual(s["missing"], ["a", "b"])
        self.assertEqual(s["light_evicted"], 2)
        self.assertEqual((s["sync_drift_ms_max"], s["sync_lead_ms_max"]), (40, 300))
        self.assertEqual(s["heap_min_kb"], 31)
        self.assertEqual(s["versions"], ["5.75", "5.76"])

    def test_heap_trend_is_per_boot(self) -> None:
        t = Tracker()
        for h in range(11):  # ten hours losing 3 KB an hour
            t.sample(h * 3600.0, status(h * 3600 + 10, heap_free_kb=100 - 3 * h))
        trend = t.summary(36000.0)["heap_trend_kb_h"]
        self.assertEqual(trend, {"kb_per_h": -3.0, "hours": 10.0, "samples": 11.0})
        t.sample(36010.0, status(2, heap_free_kb=120))  # a reboot: a new life
        self.assertEqual(t.summary(36010.0)["heap_trend_kb_h"], trend)
        self.assertIsNone(Tracker().summary(0.0)["heap_trend_kb_h"])


class TestEvents(unittest.TestCase):
    def test_only_new_lines_come_back_and_history_is_not_counted(self) -> None:
        t = Tracker()
        self.assertEqual(len(t.events_in([ev(1), ev(2, "wifi_down")])), 2)
        new = t.events_in([ev(1), ev(2, "wifi_down"), ev(3, "wifi_up", "-60")])
        self.assertEqual(new, [{"t": 3, "e": "wifi_up", "a": "-60"}])
        self.assertEqual(t.summary(0.0)["events"], {"wifi_up": 1})

    def test_a_full_ring_slides_and_overflows(self) -> None:
        t = Tracker()
        t.events_in([ev(i) for i in range(EVENT_RING)])
        self.assertEqual(len(t.events_in([ev(i) for i in range(3, EVENT_RING + 3)])), 3)
        t.events_in([ev(i) for i in range(1000, 1000 + EVENT_RING)])
        self.assertEqual(t.event_overflows, 1)
        t.events_in([{"bad": 1}, "junk"])  # malformed lines are skipped


class TestVerdict(unittest.TestCase):
    def night(self, **change: object) -> dict[str, object]:
        t = Tracker()
        for i in range(20):
            t.sample(i * 10.0, status(1000 + i * 10, rssi=-60, heap_free_kb=90))
        t.health(0.0, health())
        return {**t.summary(200.0), **change}

    def test_a_good_night_passes(self) -> None:
        checks = verdict(self.night(), Limits())
        self.assertTrue(passed(checks), [c.line() for c in checks])

    def test_each_limit_fails_when_crossed(self) -> None:
        bad: dict[str, tuple[dict[str, object], str]] = {
            "reboots": ({"reboots": 1, "reset_reasons": ["software"]}, "reboots"),
            "crash": ({"crashes": 1}, "crashes"),
            "outages": ({"outages": [{"rebooted": False}] * 4}, "Wi-Fi outages"),
            "long": ({"longest_outage_s": 900.0}, "longest outage"),
            "end": ({"answering_at_end": False}, "answering at the end"),
            "card": ({"sd_read_errors": 2, "sd_last_error": "x@1"}, "card read errors"),
            "heap": ({"heap_min_kb": 12}, "heap low-water"),
            "leak": (
                {"heap_trend_kb_h": {"kb_per_h": -5.0, "hours": 9.0}},
                "heap trend",
            ),
            "drift": ({"sync_drift_ms_max": 400}, "sync drift"),
            "starts": (
                {"starts_ok": 3, "starts_failed": ["vigil: HTTP 503"]},
                "failed show starts",
            ),
            "disrupt": (
                {"disruptions": [{"at_h": 1, "exit": 1}]},
                "disruption command",
            ),
        }
        for what, (change, name) in bad.items():
            with self.subTest(what):
                checks = verdict(self.night(**change), Limits())
                self.assertFalse(passed(checks))
                self.assertEqual([c.name for c in checks if c.state == "FAIL"], [name])

    def test_unreported_and_report_only_readings_are_info(self) -> None:
        s = self.night(
            heap_min_kb=None,
            rssi_weak_pct=None,
            heap_trend_kb_h={"kb_per_h": -9.0, "hours": 1.0},
            missing=["a"],
            monitor_paused_s=60.0,
            event_overflows=2,
            light_evicted=40,
        )
        checks = verdict(s, Limits())
        self.assertTrue(passed(checks))
        info = {c.name for c in checks if c.state == "INFO"}
        for name in (
            "heap low-water",
            "weak signal",
            "heap trend",
            "sync drift",
            "light frames evicted",
            "card reports missing",
            "monitor paused",
            "event ring overflows",
        ):
            self.assertIn(name, info)
        self.assertFalse(passed(verdict(s, Limits(max_light_evicted=10))))

    def test_overrides_and_an_early_end(self) -> None:
        s = self.night(reboots=1, reset_reasons=["software"])
        self.assertTrue(passed(verdict(s, Limits(max_reboots=1))))
        self.assertEqual(
            verdict(s, Limits(max_reboots=1), full=False)[-1].state, "FAIL"
        )
        self.assertEqual(verdict({"samples": 0}, Limits())[0].name, "castle answered")
        self.assertEqual(len(limit_fields()), 15)


if __name__ == "__main__":
    unittest.main()
