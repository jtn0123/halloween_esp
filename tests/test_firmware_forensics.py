"""What the castle can still tell you the next morning (v5.62).

The read-only audit of 2026-09-16 graded "debuggable the next morning" a C:
the event ring, the card's error count and the boot-log ring were all plain
RAM, so the crash under investigation took its own explanation with it; the
PIR, the buttons and the evening playlist were recorded by nothing; there
was no wall clock, no radio signal, no heap low-water mark and no OTA
history. This suite is the other half of those fixes — the half that fails
when one of them is quietly removed again.

It parses the firmware the way tests/test_firmware_contract.py does (nothing
below is hand-copied from the C) and drives the emulator for the parts the
source cannot show. The HTTP-visible half of the same work — the new
/api/status and /api/health keys, the new ring kinds — is held to the
emulator over there, where the key-set comparisons already live.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import castle_emu
import castle_emu_events
from firmware_source import FUNCS, HEALTH, SD_RTC, SD_STATE, SD_UTIL

FW = ROOT / "firmware"
BOOT_LOG = (FW / "boot_log.h").read_text()
INPUTS = (FW / "castle_inputs.yaml").read_text()
SD_COMMON = (FW / "castle_sd_common.yaml").read_text()
CASTLE = (FW / "castle.yaml").read_text()
SCENES = (FW / "generated" / "scenes.yaml").read_text()


class TestTheRingSurvivesTheCrash(unittest.TestCase):
    """L1. 64 entries in RTC slow memory, checksummed, dumped once at boot."""

    def test_the_copy_is_in_rtc_memory_and_not_the_heap(self) -> None:
        # RTC_NOINIT, not RTC_DATA: the bootloader must not clear it on the
        # warm start, because surviving the warm start is the whole job.
        self.assertIn("RTC_NOINIT_ATTR Block g_rtc;", SD_RTC)
        self.assertNotIn("RTC_DATA_ATTR Block", SD_RTC)
        for heap in ("malloc(", "heap_caps_", "new ("):
            self.assertNotIn(heap, SD_RTC, heap)
        # 64 x 16 B = 1 KB of the S3's 8 KB RTC slow segment, and the ring
        # is the same depth as the one in RAM it mirrors.
        self.assertIn("sizeof(Slot) == 16", SD_RTC)
        self.assertIn("inline constexpr size_t kRing = 64;", SD_RTC)
        self.assertEqual(castle_emu_events.RING, 64)

    def test_a_cold_boot_is_told_from_a_warm_one(self) -> None:
        """A magic word alone would read uninitialised silicon as a story."""
        self.assertIn("g_prev_valid = g_rtc.magic == kMagic", SD_RTC)
        self.assertIn("g_rtc.check == compute_check()", SD_RTC)
        self.assertIn("if (!g_prev_valid) clear();", SD_RTC)

    def test_every_ring_line_is_mirrored_into_it(self) -> None:
        self.assertIn("castle_rtc::record(kind, small, now_us);", SD_STATE)

    def test_the_dump_happens_in_the_boot_window_and_nowhere_else(self) -> None:
        """The rule the three ring headers all state: no card write on the
        main loop or from a request handler. The boot-time one is the only
        one, and it is where the previous life's tail goes out."""
        self.assertIn("prev_slot(i)", HEALTH)
        self.assertIn("castle_health::log_boot_to_sd", SD_COMMON)
        for handler in ("h_events", "h_status", "h_health", "h_bootlog"):
            self.assertNotIn("fopen", FUNCS[handler], handler)
        # And a boot with no card still starts this life's window clean, or
        # the next reset dumps a life that is two boots old.
        self.assertIn("castle_health::finish_boot();", SD_COMMON)

    def test_the_boot_line_carries_the_previous_lifes_story(self) -> None:
        """L2/L4/L8 all land on the same line: when, how many card errors
        the life that just ended saw, and which slot this image runs from."""
        for token in ("sd_errors_prev=%u", "part=%s", "ota=%s", "reason=%s"):
            self.assertIn(token, HEALTH, token)
        self.assertIn("castle_rtc::prev_sd_errors()", HEALTH)
        self.assertIn("esp_ota_get_state_partition", HEALTH)
        self.assertIn("likely a rollback", HEALTH)


class TestTheClock(unittest.TestCase):
    """L2. Uptime cannot be lined up against "some time after nine"."""

    def test_sntp_is_configured_and_mdns_is_still_off(self) -> None:
        self.assertIn("platform: sntp", CASTLE)
        # The responder stays off — SNTP is a DNS name, not an mDNS one.
        self.assertIn("mdns:\n  disabled: true", CASTLE)
        self.assertIn('CONFIG_LWIP_DNS_SUPPORT_MDNS_QUERIES: "n"', CASTLE)
        # One more UDP socket against a budget that is counted, not guessed.
        self.assertIn('CONFIG_LWIP_MAX_SOCKETS: "16"', CASTLE)

    def test_the_stamp_falls_back_to_uptime_rather_than_lying(self) -> None:
        """An epoch in the seventies is worse than no stamp: it looks like
        one. Both the boot line and /api/status refuse to print it."""
        self.assertIn("1577836800", HEALTH)
        self.assertIn('"up+%llus"', HEALTH)
        self.assertIn("wall > 1577836800 ? wall : 0", FUNCS["h_status"])

    def test_the_emulator_answers_the_same_base(self) -> None:
        emu = castle_emu.CastleEmu(port=0)
        self.addCleanup(emu.server_close)
        self.assertIn('"epoch":', emu.status_text())
        self.assertGreater(int(str(emu.status_json()["epoch"])), 1577836800)


class TestEverythingThatDrivesTheShowIsRecorded(unittest.TestCase):
    """L3. record_action only ever saw the web mailbox."""

    def test_the_pir_records_all_three_of_its_outcomes(self) -> None:
        for kind in ("EventKind::PIR", "EventKind::PIR_COOLDOWN", "EventKind::PIR_OFF"):
            self.assertIn(kind, INPUTS, kind)
        # The suppressed trip used to return with no line at all — "nothing
        # happened" and "forty trips, all swallowed" read identically.
        self.assertIn("hold - (now - last_ms)", INPUTS)

    def test_every_button_leaves_its_name(self) -> None:
        # Eight scenes, blackout, the boot-log dump and the flash-mode
        # escape hatch — every `on_press:` in the file but ONE: the odd one out is the
        # PIR's binary_sensor, which records its own three outcomes above.
        self.assertEqual(
            INPUTS.count("EventKind::BUTTON"), INPUTS.count("on_press:") - 1
        )

    def test_run_scene_records_the_start_whoever_asked(self) -> None:
        """The generated dispatch, not a hand-edited copy of it."""
        self.assertIn("EventKind::SCENE_START", SCENES)
        gen = (ROOT / "tools" / "gen_show.py").read_text()
        self.assertIn("EventKind::SCENE_START", gen)

    def test_a_chattering_sensor_cannot_flush_the_ring(self) -> None:
        """The discipline note_light_evictions set: the loudest writers are
        rate-limited. The PIR's own cooldown does that job for motion; the
        server's refusals get an explicit gap of their own (L11)."""
        self.assertIn("now - last < 2000000", SD_UTIL)
        self.assertEqual(castle_emu_events.ERR_GAP_MS, 2000)


class TestTheBootLogStopsRolling(unittest.TestCase):
    """L5. The mount and the manifest check were gone by show time."""

    def test_capture_is_gated_and_the_gate_is_closed_after_start(self) -> None:
        self.assertIn("g_frozen", BOOT_LOG)
        self.assertIn("|| g_frozen) return;", BOOT_LOG)
        start = SD_COMMON.index("castle_web::start();")
        self.assertIn("castle_log::freeze();", SD_COMMON[start:])

    def test_the_dump_says_which_it_is(self) -> None:
        self.assertIn("frozen at the end of boot", BOOT_LOG)
        self.assertIn("still capturing", BOOT_LOG)


class TestTheRadioAndTheHeap(unittest.TestCase):
    """L6 and L7: the two numbers a fault is read with."""

    def test_wifi_transitions_reach_the_ring_and_the_status_reply(self) -> None:
        self.assertIn("EventKind::WIFI_UP", SD_STATE)
        self.assertIn("EventKind::WIFI_DOWN", SD_STATE)
        self.assertIn("castle_web::mirror_wifi(", SD_COMMON)
        self.assertIn('"rssi":%d', FUNCS["h_status"])

    def test_health_reports_the_low_water_mark_not_the_free_mark(self) -> None:
        """/api/status's heap_free_kb is what is free NOW — after the
        allocation that failed was handed back. It reads perfectly healthy
        at 23:00 on a castle that came within a hundred bytes at 21:40."""
        self.assertIn("heap_caps_get_minimum_free_size", FUNCS["h_health"])
        self.assertIn('"heap_min_kb":%u', FUNCS["h_health"])

    def test_the_runbook_points_at_a_panel_that_exists(self) -> None:
        runbook = (ROOT / "docs" / "RUNBOOK.md").read_text()
        self.assertIn("Recent castle events", runbook)
        self.assertIn("heap_min_kb", runbook)


class TestTheCardsOwnStory(unittest.TestCase):
    """L4. A count could not tell one bad file from a dying card."""

    def test_the_failing_path_and_offset_are_kept_beside_the_count(self) -> None:
        site = (FW / "sd_web_site.h").read_text()
        self.assertIn("note_sd_read_error(path, (unsigned long) out);", site)
        self.assertIn('"@"', HEALTH)
        self.assertIn('"sd_last_error":"', FUNCS["h_health"])
        # A card path is device-sourced and goes out inside a JSON string.
        self.assertIn("json_escape(castle_health::sd_last_error())", FUNCS["h_health"])

    def test_the_count_crosses_the_reset_that_the_ram_copy_cannot(self) -> None:
        self.assertIn("castle_rtc::note_sd_error();", HEALTH)
        self.assertIn("inline void note_sd_error()", SD_RTC)


class TestTheLogIsReadable(unittest.TestCase):
    """L9. /sd/logs/castle.log was HTTP-readable and nobody read it."""

    def test_sd_sync_has_a_logs_verb_and_documents_it(self) -> None:
        import sd_sync

        self.assertTrue(hasattr(sd_sync, "cmd_logs"))
        assert sd_sync.__doc__ is not None
        self.assertIn("logs", sd_sync.__doc__)
        # Both files, newest last, and the tail printed — the point is to
        # answer "what happened last night" without a browser.
        self.assertIn("logs/castle.log.1", sd_sync.LOG_FILES)

    def test_the_bridge_cli_mirrors_the_verb(self) -> None:
        castle_rs = (ROOT / "core" / "src" / "bin" / "castle.rs").read_text()
        self.assertIn('"logs"', castle_rs)
        self.assertIn("/sd/logs/castle.log", castle_rs)


class TestTheRefusalsAreRecorded(unittest.TestCase):
    """L11. reply_err logged nothing at all."""

    def test_the_line_carries_the_code_and_not_the_request(self) -> None:
        """Nothing request-derived may reach the ring: the ring is written
        to the card at the next boot."""
        self.assertIn("EventKind::HTTP_ERR", SD_UTIL)
        self.assertIn("note_reply_error(status);", SD_UTIL)
        note = SD_UTIL[SD_UTIL.index("inline void note_reply_error") :]
        note = note[: note.index("\n}")]
        for borrowed in ("req->", "httpd_req", "url_decode", "uri"):
            self.assertNotIn(borrowed, note, borrowed)

    def test_the_emulator_leaves_the_same_line(self) -> None:
        ring = castle_emu_events.Events()
        ring.note_reply_error(404, 1000)
        ring.note_reply_error(500, 1500)  # inside the gap: silent
        ring.note_reply_error(500, 3200)
        self.assertEqual(
            [(kind, arg) for _t, kind, arg, _trunc in ring.snapshot()],
            [("http_err", "404"), ("http_err", "500")],
        )
        self.assertEqual(json.loads(ring.json())[0]["e"], "http_err")


if __name__ == "__main__":
    unittest.main()
