"""The emulator held to the firmware — by reading the firmware.

firmware/sd_web.h (+ sd_web_ota.h, sd_web_site.h, sd_web_remote.h,
sd_web_state.h, sd_web_util.h) is the contract the studio speaks to;
tools/castle_emu*.py is the stand-in every hardware-free test drives. The
two can only be trusted together if a change to either is caught here. So
these tests PARSE the C at test time — the reg() table, every reply_err()
string per handler, h_status's JSON keys, the validators' constants — and
hold the emulator's port to what they find, then drive a live emulator for
the verdicts the source cannot show (routing, 404/405, the OTA leg).
Nothing below is hand-copied from the firmware; that is the point.

The byte rules underneath the handlers — safe_name, safe_subpath,
url_decode, name_from_uri, query_param's buffers — are the same discipline
one header down, and live in tests/test_firmware_names.py. The parsing both
suites do is tests/firmware_source.py.
"""

from __future__ import annotations

import http.client
import json
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))  # firmware_source

import castle_emu
import castle_emu_events
import castle_emu_http
import castle_emu_wire as wire
from firmware_source import (
    EMU_CONSTS,
    EMU_HTTP,
    ERR_HELPERS,
    FUNCS,
    HARDWARE_ONLY,
    SD_EVENTS,
    SD_STATE,
    SD_STREAM,
    SD_WEB,
    emu_errs,
    firmware_routes,
    grab,
    reply_errs,
    stream_port,
)


class TestRouteTable(unittest.TestCase):
    def test_emulator_serves_exactly_the_firmwares_table(self) -> None:
        self.assertEqual(list(wire.ROUTES), firmware_routes())

    def test_every_firmware_handler_exists_in_the_emulator(self) -> None:
        for _p, _m, h in firmware_routes():
            self.assertTrue(hasattr(castle_emu_http.Handler, h), h)
            self.assertIn(h, FUNCS, f"{h} not found in the firmware sources")

    def test_the_max_uri_handlers_headroom_claim_holds(self) -> None:
        """sd_web.h: "MUST exceed the reg() count below"."""
        cap = int(grab(r"cfg\.max_uri_handlers = (\d+);", SD_WEB))
        self.assertGreater(cap, len(firmware_routes()))


class TestErrorStrings(unittest.TestCase):
    """Each handler's reply_err set, firmware vs emulator, string for string."""

    def errs_for(self, handler: str) -> set[tuple[int, str]]:
        """Every verdict this route can give, the helpers it delegates to
        included — and the helpers THEY delegate to, since v5.61: h_put
        answers through upload_offload, which answers through write_body."""
        seen: set[str] = set()
        todo = [handler]
        found: set[tuple[int, str]] = set()
        while todo:
            name = todo.pop()
            if name in seen:
                continue
            seen.add(name)
            body = FUNCS[name]
            found |= reply_errs(body)
            todo += [h for h in ERR_HELPERS if f"{h}(" in body]
        return found

    def test_emulator_uses_the_firmwares_strings_and_codes(self) -> None:
        for _p, _m, h in firmware_routes():
            fw = {e for e in self.errs_for(h) if e[1] not in HARDWARE_ONLY}
            emu = emu_errs(h)
            self.assertEqual(emu, fw, f"{h}: emulator {emu} vs firmware {fw}")

    def test_no_emulator_error_string_is_invented(self) -> None:
        all_fw = set().union(*(reply_errs(b) for b in FUNCS.values()))
        spelled = re.findall(r'self\._err\((\d{3}), "([^"]*)"', EMU_HTTP)
        named = [
            (c, EMU_CONSTS[n])
            for c, n in re.findall(
                r"self\._err\((\d{3}), ([A-Z][A-Z0-9_]*)[,)]", EMU_HTTP
            )
        ]
        for c, msg in spelled + named:
            self.assertIn((int(c), msg), all_fw)
        # And the exemptions must still be strings the firmware says: an
        # entry that outlives its message would exempt a renamed one
        # silently ("opendir failed" had, grade report 2026-09-06 J6).
        self.assertLessEqual(HARDWARE_ONLY, {msg for _c, msg in all_fw})


class TestValidatorConstants(unittest.TestCase):
    def test_volume_light_and_ota_limits(self) -> None:
        self.assertIn("v.size() <= 3", FUNCS["h_volume"])
        self.assertIn("pct > 100", FUNCS["h_volume"])
        self.assertIn("light_spec_ok(c)", FUNCS["h_light"])
        self.assertIn("S_ISDIR", FUNCS["h_list"])
        self.assertIn("content_len == 0", FUNCS["h_put"])
        self.assertIn("empty body", FUNCS["h_put"])
        # The validator lives in sd_web_state.h; its shape is pinned by example.
        self.assertIn("spec.size() == 6", SD_STATE)
        self.assertIn("zone.size() > 16", SD_STATE)
        self.assertIn("> 100)\n      return false", SD_STATE)
        for c, ok in (
            (b"ff0000", True),
            (b"show", True),
            (b"towerL:off", True),
            (b"door:00FF00", True),
            (b":ff0000", False),
            (b"tower-L:ff0000", False),
            (b"towerL:ff00", False),
            (b"x" * 17 + b":show", False),
            (b"", False),
            (b"white", True),
            (b"towerR:white@25", True),
            (b"ff0000@100", True),
            (b"ff0000@0", False),
            (b"ff0000@101", False),
            (b"ff0000@", False),
            (b"ff0000@5x", False),
            (b"show@50", True),
            (b"bars", True),
            (b"towerL:chase@75", True),
            (b"ends", True),
            (b"sparkle", False),
            (b"door:bars@0", False),
        ):
            self.assertEqual(wire.light_spec_ok(c), ok, c)
        pat = r"content_len < (\d+) \|\| req->content_len > ([\w>-]+)"
        self.assertEqual(int(grab(pat, FUNCS["h_ota"], 1)), castle_emu_http.OTA_MIN)
        self.assertEqual(grab(pat, FUNCS["h_ota"], 2), "part->size")  # the slot

    def test_status_keys_are_the_firmwares(self) -> None:
        fmt = FUNCS["h_status"]
        keys = set(re.findall(r'"(\w+)":', fmt))
        emu = castle_emu.CastleEmu(port=0)
        self.addCleanup(emu.server_close)
        st = emu.status_json()
        pir = st["pir"]
        assert isinstance(pir, dict)
        self.assertEqual(set(st) | set(pir), keys)

    def test_the_light_counters_are_spelled_in_both_status_replies(self) -> None:
        """v5.59. The key-set test above already compares the two sides; this
        names them, so a rename cannot pass by moving in both files."""
        emu = castle_emu.CastleEmu(port=0)
        self.addCleanup(emu.server_close)
        for key in ("light_applied", "light_evicted"):
            self.assertIn(f'"{key}":%u', FUNCS["h_status"])
            self.assertIn(f'"{key}":0', emu.status_text())
        st = emu.status_json()
        self.assertEqual((st["light_applied"], st["light_evicted"]), (0, 0))
        # The eviction is counted where the firmware counts it: in the
        # mailbox, for EVERY light frame the one slot loses (v5.60, A6) —
        # the frame a later LIGHT replaced, and the frame that arrived
        # behind another kind of command and was dropped instead.
        self.assertIn(
            "ActionType::NONE) {\n    g_light_evicted.fetch_add(1);", SD_STATE
        )
        emu.queue("LIGHT", "ff0000")
        emu.queue("LIGHT", "00ff00")
        self.assertEqual(emu.status_json()["light_evicted"], 1)
        emu.queue("STOP", "")
        emu.queue("LIGHT", "0000ff")
        self.assertEqual(emu.status_json()["light_evicted"], 2)
        # ...and the STOP is still what the main loop will run: the frame is
        # counted, never allowed to take the slot from another kind.
        self.assertEqual(emu._pending, ("STOP", ""))

    def test_status_is_read_as_one_snapshot(self) -> None:
        """A7. h_status used to copy the strings under g_state_mu, drop the
        lock, and only then load show_on / playing / position_ms / volume as
        independent atomics — and the 200 ms mirror lands in between often
        enough that a poll on the tick a track ended carried the track's
        name beside playing:false. Everything moves together now."""
        body = FUNCS["h_status"]
        self.assertIn("status_snapshot()", body)
        for atomic in (
            "g_playing.load()",
            "g_position_ms.load()",
            "g_show_on.load()",
            "g_volume.load()",
            "g_light_applied.load()",
            "g_pir_armed.load()",
            "g_scene",
            "g_track",
        ):
            self.assertNotIn(atomic, body, atomic)
        # And the one writer takes the one lock once, with the audio clock
        # already stored: the mirror tick's last act.
        publish = SD_STATE[SD_STATE.index("inline void mirror_show_state(") :]
        self.assertEqual(publish.count("std::scoped_lock lk(g_state_mu);"), 1)
        for field in ("playing", "position_ms", "show_on", "volume", "track"):
            self.assertIn(f"g_status.{field} =", publish)
        # h_status's fixed part must still fit the 240-byte buffers it fills.
        self.assertIn("std::array<char, 240> buf{}", body)

    def test_the_first_status_served_confirms_a_web_ota(self) -> None:
        """A2. CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE holds a freshly-OTA'd
        image in PENDING_VERIFY, and the only confirmation was `api:
        on_client_connected` — a Home Assistant this castle does not always
        have. A web-OTA'd image was therefore never confirmed and rolled
        back on the next power cycle, silently undoing a working update."""
        self.assertIn("g_status_served.store(true);", FUNCS["h_status"])
        boot = (ROOT / "firmware" / "castle_sd_common.yaml").read_text()
        self.assertIn("castle_web::g_status_served.load()", boot)
        self.assertIn("castle_sd::mark_firmware_healthy();", boot)
        # The log line that says it happened, and the one-shot that keeps it
        # from happening twice, both stay in flash_mode.h.
        flash = (ROOT / "firmware" / "flash_mode.h").read_text()
        self.assertIn("image confirmed — rollback cancelled", flash)

    def test_pending_mailbox_is_one_slot(self) -> None:
        """sd_web_state.h: set_pending overwrites; take_pending empties."""
        self.assertIn("g_pending = {type, std::move(arg)};", SD_STATE)
        self.assertIn('g_pending = {ActionType::NONE, ""};', SD_STATE)


class TestEventRing(unittest.TestCase):
    """/api/events (v5.59): the ring the main loop fills. Its SHAPE is the
    contract a page codes against, so the two castles are held to the same
    size, the same kind words and the same JSON template."""

    def test_the_ring_is_the_same_size_on_both_sides(self) -> None:
        self.assertEqual(
            int(grab(r"kEventRing = (\d+);", SD_STATE)), castle_emu_events.RING
        )
        # The C keeps the NUL; the emulator counts the bytes beside it.
        self.assertEqual(
            int(grab(r"kEventArgMax = (\d+);", SD_STATE)),
            castle_emu_events.ARG_MAX + 1,
        )
        # No heap: a fixed std::array, not a vector that grows per event.
        self.assertIn("std::array<Event, kEventRing> g_events{}", SD_STATE)

    def test_the_kind_words_are_the_firmwares(self) -> None:
        fw = set(re.findall(r'case EventKind::\w+: return "(\w+)";', SD_STATE))
        emu = set(castle_emu_events.ACTION_KIND.values()) | {
            "light_evicted",
            "sound",
            "silent",
        }
        self.assertEqual(emu, fw)

    def test_the_rate_limit_on_dropped_light_frames_matches(self) -> None:
        us = int(grab(r"g_light_evict_event_us < (\d+)\)", SD_STATE))
        self.assertEqual(us // 1000, castle_emu_events.EVICT_GAP_MS)

    def test_the_json_template_is_the_firmwares(self) -> None:
        self.assertIn(R'{"t":%lld,"e":"', SD_EVENTS)
        self.assertIn(R'","a":"', SD_EVENTS)
        ring = castle_emu_events.Events()
        ring.record("play", 'a"b.mp3', 7)
        self.assertEqual(ring.json(), R'[{"t":7,"e":"play","a":"a\"b.mp3"}]')
        self.assertEqual(json.loads(ring.json())[0]["a"], 'a"b.mp3')

    def test_the_handler_never_writes_the_card_or_blocks_the_loop(self) -> None:
        """A per-event SD write on the main loop stalls audio and pixels —
        the very glitch the ring exists to explain."""
        body = FUNCS["h_events"]
        for forbidden in ("fopen", "log_boot_to_sd", "/sd/"):
            self.assertNotIn(forbidden, body)
        self.assertIn("copy_events", body)  # a copy, then format outside the lock


class TestStreamServer(unittest.TestCase):
    """sd_web_stream.h is the second server — every note of audio in the
    show — and it is outside sd_web.h's reg() table, so this is where its
    port is held to every caller that spells it (grade report 2026-09-06
    J3): change it in one place and every scene goes silent with a green
    suite otherwise."""

    def test_every_loopback_url_names_the_stream_port(self) -> None:
        port = stream_port()
        for name in (
            "tools/gen_esphome_audio.py",
            "firmware/castle_sd_common.yaml",
            "firmware/sd_audio.h",
        ):
            text = (ROOT / name).read_text()
            spelled = {
                int(p) for p in re.findall(r"http://127\.0\.0\.1:(\d+)/sd/", text)
            }
            self.assertEqual(spelled, {port}, name)

    def test_the_stream_server_serves_the_card_and_nothing_else(self) -> None:
        self.assertEqual(re.findall(r'u\.uri = "([^"]+)";', SD_STREAM), ["/sd/*"])
        self.assertIn("castle_stream::start(h_sd_get);", SD_WEB)

    def test_health_keys_are_the_firmwares(self) -> None:
        """h_health is the one reply whose shape the emulator types by hand;
        h_status already had this check."""
        keys = set(re.findall(r'"(\w+)":', FUNCS["h_health"]))
        emu = castle_emu.CastleEmu(port=0)
        self.addCleanup(emu.server_close)
        emu.start()
        self.addCleanup(emu.shutdown)
        c = http.client.HTTPConnection("127.0.0.1", emu.port, timeout=5)
        c.request("GET", "/api/health")
        body = json.loads(c.getresponse().read())
        c.close()
        self.assertEqual(set(body), keys)


class TestWireBehaviour(unittest.TestCase):
    """Every firmware (route, method) answers; every other verb is a 405
    and every other path a 404 — the esp_http_server verdicts."""

    card: Path
    emu: castle_emu.CastleEmu

    @classmethod
    def setUpClass(cls) -> None:
        cls.card = Path(tempfile.mkdtemp(prefix="contract-sd-"))
        cls.emu = castle_emu.CastleEmu(port=0, sd_dir=cls.card, scenes=["vigil"])
        cls.emu.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.emu.shutdown()
        cls.emu.server_close()

    def call(self, method: str, path: str, body: bytes = b"") -> tuple[int, bytes]:
        c = http.client.HTTPConnection("127.0.0.1", self.emu.port, timeout=5)
        c.request(method, path, body=body or None)
        r = c.getresponse()
        out = r.status, r.read()
        c.close()
        return out

    def concrete(self, template: str) -> str:
        return template.replace("*", "probe.mp3")

    def test_every_registered_route_is_served(self) -> None:
        idf = {m.encode() for m in wire.IDF_ERRORS.values()}
        for path, method, h in firmware_routes():
            body = b"\xe9" + b"\0" * 70000 if h == "h_ota" else b"x"
            code, out = self.call(method, self.concrete(path), body)
            self.assertNotIn(out, idf, f"{method} {path} → {code} {out!r}")

    def test_wrong_verb_is_405_unknown_path_is_404(self) -> None:
        served = {(p, m) for p, m, _ in firmware_routes()}
        for path, _m, _h in firmware_routes():
            for verb in ("GET", "POST", "PUT", "DELETE", "HEAD"):
                if (path, verb) in served:
                    continue
                code, out = self.call(verb, self.concrete(path))
                want = b"" if verb == "HEAD" else wire.IDF_ERRORS[405].encode()
                self.assertEqual((code, out), (405, want), f"{verb} {path}")
        for path in (
            "/api",
            "/api/",
            "/api/files2",
            "/apix/status",
            "/nope",
            "/api/status/",
            "/remote/x",
            "/sd",
            "/site",
        ):
            code, out = self.call("GET", path)
            self.assertEqual((code, out), (404, wire.IDF_ERRORS[404].encode()), path)

    def test_an_overlong_request_line_is_414(self) -> None:
        code, _ = self.call("GET", "/api/status?" + "x" * 600)
        self.assertEqual(code, 414)

    def test_one_slot_mailbox_keeps_only_the_last_command(self) -> None:
        self.call("POST", "/api/volume?v=11")
        self.call("POST", "/api/volume?v=22")
        self.call("POST", "/api/volume?v=33")
        deadline = time.monotonic() + 2
        while (
            time.monotonic() < deadline
            and json.loads(self.call("GET", "/api/status")[1])["volume"] != 33
        ):
            time.sleep(0.05)
        self.assertEqual(json.loads(self.call("GET", "/api/status")[1])["volume"], 33)
        self.assertNotIn(("VOLUME", "11"), self.emu.applied[-3:])

    def test_a_quote_in_a_name_is_refused_so_the_list_json_survives(self) -> None:
        """safe_name refuses '"', '\\' and control bytes at the door: the
        PUT is a 400 "bad filename" and GET /api/files stays parseable.
        (v5.23 admitted them and one such file broke the list for every
        client; v5.25 also escapes at the exit — see the JSON tests in
        tests/test_castle_emu.py.)"""
        for enc in ("a%22b.mp3", "a%5Cb.mp3", "a%09b.mp3", "a%7Fb.mp3", "a%00b.mp3"):
            code, out = self.call("PUT", f"/api/files/{enc}", b"x")
            self.assertEqual((code, out), (400, b"bad filename"), enc)
        for enc in ("a%22b.mp3", "a%5Cb.mp3"):
            code, out = self.call("POST", f"/api/play?f={enc}")
            self.assertEqual((code, out), (400, b"need ?f=<file>"), enc)
        code, out = self.call("GET", "/api/files")
        self.assertEqual(code, 200)
        names = [e["name"] for e in json.loads(out)]
        self.assertFalse(any('"' in n or "\\" in n for n in names), names)
        code, out = self.call("GET", "/api/status")
        self.assertEqual(code, 200)
        json.loads(out)


if __name__ == "__main__":
    unittest.main()
