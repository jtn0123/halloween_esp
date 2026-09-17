"""The castle's web API, run twice and compared: the real C, and the emulator.

Until this suite, nothing EXECUTED firmware/sd_web.h. The emulator
(tools/castle_emu*.py) is a re-implementation held to the C by PARSING it
(tests/test_firmware_contract.py, tests/test_firmware_names.py) — which
catches a renamed route or a changed error string, and cannot catch a
handler that decides differently. Every hardware-free test in the repo, the
studio's relay leg and the whole e2e suite believe the emulator; this is the
only thing that checks the belief.

tests/cxx/web_check.cpp compiles the real headers against a fake ESP-IDF
(tests/cxx/shim/) and answers requests on a pipe. Here both castles get the
same request over the same card contents and the two replies must be the
same bytes: status, body, content type and any header the handler chose.

Three deliberate exceptions, none of them about the firmware's logic:

  * /api/status's `compiled` (__DATE__/__TIME__) and `uptime_s`, and the
    heap and card numbers, which are the machine's rather than the code's.
  * /api/files entry ORDER — the device answers in FAT order and the
    emulator sorts, so the entries are compared as a set.
  * (a directory's `size` was a third one until v5.62 reported 0 for one
    on the board too — C10; the number is compared like every other now.)

This file is the READING half — routing, the pages served off the card and
out of flash, the JSON replies and the validators. Everything that changes
the card or the flash is tests/test_firmware_web_card.py, and the seeded
storm and the byte rules are tests/test_firmware_web_storm.py. All three
share tests/firmware_web_harness.py.

Skipped, not failed, where no host C++ compiler exists — except in CI.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

import castle_emu_flash
import castle_emu_wire as wire
from firmware_source import firmware_routes
from firmware_web_harness import COMPILER, IN_CI, Reply, WebPairCase, compiled

VERBS = ("GET", "POST", "PUT", "DELETE")


def concrete(template: str) -> bytes:
    """A route template as a URL something could actually be asked for."""
    return template.replace("*", "probe.mp3").encode()


def plausible_body(handler: str) -> bytes:
    if handler == "h_ota":
        return b"\xe9" + b"\x00" * 70000
    return b"payload"


class TestCompiles(unittest.TestCase):
    @unittest.skipIf(COMPILER is None and not IN_CI, "no host C++ compiler")
    def test_the_real_headers_compile_warning_free(self) -> None:
        """-Wall -Wextra -Werror over sd_web.h and everything it includes.
        The device build buries warnings in a wall of ESP-IDF output; here
        one is a failure."""
        if COMPILER is None:
            raise AssertionError("CI is set and no host C++ compiler is on PATH")
        _, built = compiled()
        self.assertEqual(built.returncode, 0, built.stderr)


class TestRouteMatrix(WebPairCase):
    """Every row of sd_web.h's reg() table, and every verb that is not it."""

    def test_every_registered_route_answers_identically(self) -> None:
        routes = firmware_routes()
        self.assertGreaterEqual(len(routes), 25, "the route table shrank")
        for path, method, handler in routes:
            with self.subTest(route=path, method=method):
                # The three whose bodies carry numbers off the machine or
                # a ring the two castles fill on their own clocks; they are
                # compared field by field (here, and _events_cxx for events).
                if handler in ("h_status", "h_list", "h_events"):
                    c, e = self.pair.both(method, concrete(path))
                    self.assertEqual((c.status, c.ctype), (e.status, e.ctype))
                    continue
                self.same(method, concrete(path), plausible_body(handler))

    def test_every_other_verb_on_every_route_is_the_same_405(self) -> None:
        served = {(p, m) for p, m, _ in firmware_routes()}
        for path, _m, _h in firmware_routes():
            for verb in VERBS:
                if (path, verb) in served:
                    continue
                with self.subTest(route=path, verb=verb):
                    r = self.same(verb, concrete(path))
                    self.assertEqual(r.status, 405)

    def test_the_router_edges_agree(self) -> None:
        """A trailing slash, a case change, one extra letter: the places a
        wildcard template is one character from meaning something else."""
        for path in (
            "/api",
            "/api/",
            "/api/files2",
            "/api/filesx",
            "/apix/status",
            "/nope",
            "/api/status/",
            "/API/status",
            "/remote/",
            "/remote/x",
            "/sd",
            "/sd/",
            "/site",
            "/site/",
            "/",
            "//",
            "/api/show",
            "/api/show/",
            "/api/show/startle",
        ):
            with self.subTest(path=path):
                self.same("GET", path.encode())

    def test_an_overlong_request_line_is_the_same_414(self) -> None:
        r = self.same("GET", b"/api/status?" + b"x" * 600)
        self.assertEqual(r.status, 414)

    def test_the_stream_server_serves_the_card_and_nothing_else(self) -> None:
        """sd_web_stream.h's second server (grade report 2026-09-06 J3) is
        outside the reg() table and carries every note of audio in the show.
        The emulator has no second port, so this half is the C alone."""
        audio = self.pair.c.http("GET", b"/sd/wicked_winds.mp3", port=8080)
        self.assertEqual(audio.status, 200)
        self.assertEqual(
            audio.body, (self.pair.card_c / "wicked_winds.mp3").read_bytes()
        )
        self.assertEqual(audio.ctype, "audio/mpeg")
        for path in (b"/api/status", b"/", b"/site/app.js", b"/remote"):
            self.assertEqual(self.pair.c.http("GET", path, port=8080).status, 404, path)


class TestServedPages(WebPairCase):
    """The bytes that go out: the flash pages, the card's files, the gzip
    preference and the CSP that rides on all of them."""

    def test_the_remote_page_is_the_flash_page(self) -> None:
        r = self.same("GET", b"/remote")
        self.assertEqual(r.ctype, "text/html; charset=utf-8")
        self.assertEqual(r.extra["Content-Security-Policy"], castle_emu_flash.CSP)
        self.assertIn(b"Castle Remote", r.body)

    def test_the_root_prefers_the_gzipped_desk(self) -> None:
        r = self.same("GET", b"/")
        self.assertEqual(r.extra["Content-Encoding"], "gzip")
        self.assertEqual(r.ctype, "text/html; charset=utf-8")

    def test_the_root_falls_back_to_the_plain_page_then_to_flash(self) -> None:
        gz = [
            c / "site" / "index.html.gz" for c in (self.pair.card_c, self.pair.card_e)
        ]
        plain = [
            c / "site" / "index.html" for c in (self.pair.card_c, self.pair.card_e)
        ]
        keep = (gz[0].read_bytes(), plain[0].read_bytes())
        try:
            for f in gz:
                f.unlink()
            r = self.same("GET", b"/")
            self.assertNotIn("Content-Encoding", r.extra)
            self.assertIn(b"desk", r.body)
            for f in plain:
                f.unlink()
            r = self.same("GET", b"/")
            self.assertIn(b"Castle", r.body)
            self.assertEqual(r.extra["Content-Security-Policy"], castle_emu_flash.CSP)
        finally:
            for f in gz:
                f.write_bytes(keep[0])
            for f in plain:
                f.write_bytes(keep[1])

    def test_site_and_sd_serving_including_the_refusals(self) -> None:
        for target in (
            b"/site/app.js",
            b"/site/index.html",
            b"/site/nope.js",
            b"/site/../wicked_winds.mp3",
            b"/site/.hidden",
            b"/sd/wicked_winds.mp3",
            b"/sd/scenes/vigil.mp3",
            b"/sd/site/app.js",
            b"/sd/nope.mp3",
            b"/sd/../etc/passwd",
            b"/sd/%2e%2e/x",
            b"/sd/scenes",
            b"/sd/",
            # A trailing separator, and a "." segment. Both name a file
            # POSIX would happily open and FatFs answers FR_NO_PATH /
            # FR_NO_FILE for (FF_FS_RPATH is 0) — the shim models FatFs and
            # the emulator now does too. `/sd/<file>/` served the file here
            # until the storm caught it.
            b"/sd/wicked_winds.mp3/",
            b"/sd/scenes/vigil.mp3/",
            b"/sd/scenes/./vigil.mp3",
            b"/site/app.js/",
            b"/site/./app.js",
        ):
            with self.subTest(target=target):
                r = self.same("GET", target)
                if target.endswith(b"/") or b"/./" in target:
                    self.assertEqual(r.status, 400, target)

    def test_only_the_page_routes_carry_a_csp(self) -> None:
        """E4: set_csp is on /, /remote and /site/*, and deliberately not on
        the streaming route the media pipeline pulls audio through."""
        self.assertIn(
            "Content-Security-Policy", self.same("GET", b"/site/app.js").extra
        )
        self.assertNotIn(
            "Content-Security-Policy", self.same("GET", b"/sd/wicked_winds.mp3").extra
        )


class TestJsonReplies(WebPairCase):
    """The two read-only JSON routes. Their own class because nothing here
    may have queued an action first: the emulator's main loop applies one
    200 ms later and /api/status would then describe a different castle."""

    def test_status_is_the_same_json(self) -> None:
        c, e = self.pair.both("GET", b"/api/status")
        self.assertEqual((c.status, c.ctype), (e.status, "application/json"))
        cj, ej = json.loads(c.body), json.loads(e.body)
        self.assertEqual(set(cj), set(ej))
        self.assertEqual(set(cj["pir"]), set(ej["pir"]))
        # Everything the firmware DECIDES, as opposed to reads off the board.
        for key in (
            "version",
            "sd_mounted",
            "missing",
            "scenes",
            "volume",
            "scene",
            "track",
            "show_on",
            "pir",
        ):
            self.assertEqual(cj[key], ej[key], key)
        self.assertEqual(cj["scenes"], "vigil,storm")

    def test_health_is_the_same_json(self) -> None:
        r = self.same("GET", b"/api/health")
        # sd_read_errors is this boot's torn card transfers (A8) and, since
        # v5.62, sd_last_error is where; heap_min_kb is the low-water mark.
        counters = {"boots": 3, "crashes": 0, "sd_read_errors": 0, "heap_min_kb": 64}
        rest = {"last_reset": "power-on", "was_crash": False, "sd_last_error": ""}
        self.assertEqual(json.loads(r.body), counters | rest)

    def test_the_boot_log_is_the_same_text(self) -> None:
        r = self.same("GET", b"/api/bootlog")
        self.assertEqual(r.ctype, "text/plain")
        head, *lines = r.body.decode().splitlines()
        self.assertEqual(head, f"boot log: {len(lines)} lines, 0 dropped")

    def test_the_listings_agree_on_every_entry(self) -> None:
        # `?d=` with an empty value is the root, the same as no query at
        # all — httpd_query_key_value hands back a zero-length value, not
        # a missing one.
        for target in (
            b"/api/files",
            b"/api/files?d=",
            b"/api/files?d=scenes",
            b"/api/files?d=site",
            b"/api/files?d=logs",
        ):
            with self.subTest(target=target):
                c, e = self.pair.both("GET", target)
                self.assertEqual((c.status, c.ctype), (e.status, e.ctype), target)
                self.assertEqual(entries(c), entries(e), target)

    def test_a_bad_or_missing_listing_directory(self) -> None:
        for target in (
            b"/api/files?d=nope",
            b"/api/files?d=../etc",
            b"/api/files?d=.hidden",
            b"/api/files?d=/sd",
            b"/api/files?d=scenes/../site",
            b"/api/files?d=scenes/",
            b"/api/files?d=scenes/.",
        ):
            with self.subTest(target=target):
                r = self.same("GET", target)
                self.assertIn(r.status, (400, 404))

    def test_a_name_safe_name_refuses_is_counted_not_listed(self) -> None:
        c, _e = self.pair.both("GET", b"/api/files")
        listed = json.loads(c.body)
        self.assertIn({"skipped": 1}, listed)
        self.assertNotIn("bad\x7fname.mp3", [x.get("name") for x in listed])


def entries(r: Reply) -> set[tuple[str, bool, int]]:
    """A listing as a comparable set: name, dir flag and size. A directory
    reports 0 on all three castles since v5.62 (C10) — the host filesystem
    used to answer this one with its own block bookkeeping, which is a
    third behaviour and made the field untestable."""
    out = set()
    for item in json.loads(r.body):
        if "name" not in item:
            out.add((f"skipped:{item['skipped']}", False, 0))
            continue
        out.add((item["name"], bool(item["dir"]), int(item["size"])))
    return out


class TestValidators(WebPairCase):
    """?f=, ?s=, ?v=, ?c= and ?armed= at their edges. light_spec_ok's
    branches are the long list: sd_web_state.h decides the desk's channel
    test, and every one of its refusals shows up as a toast."""

    def test_play_and_scene(self) -> None:
        for q in (
            b"?f=wicked_winds.mp3",
            b"?f=",
            b"",
            b"?f=.hidden",
            b"?f=a/b",
            b"?f=..",
            b"?f=%2e%2e",
            b"?f=a%22b",
            b"?f=a%5cb",
            b"?f=a%00b",
            b"?f=" + b"a" * 99,
            b"?f=" + b"a" * 100,
            b"?f=%C3%A9.mp3",
            b"?F=wicked_winds.mp3",
            b"?junk&f=wicked_winds.mp3",
        ):
            with self.subTest(q=q):
                self.same("POST", b"/api/play" + q)
        for q in (
            b"?s=vigil",
            b"?s=storm",
            b"?s=nope",
            b"?s=",
            b"",
            b"?s=VIGIL",
            b"?s=vigil&s=nope",
            b"?s=%76igil",
        ):
            with self.subTest(q=q):
                self.same("POST", b"/api/scene" + q)

    def test_volume(self) -> None:
        # No raw spaces: a request line with three words is rejected by the
        # transport on both sides and tests nothing about either handler.
        for v in (
            b"0",
            b"1",
            b"70",
            b"99",
            b"100",
            b"101",
            b"999",
            b"1000",
            b"-1",
            b"007",
            b"abc",
            b"1e2",
            b"",
            b"+5",
            b"%2050",
            b"50%25",
            b"0x10",
        ):
            with self.subTest(v=v):
                self.same("POST", b"/api/volume?v=" + v)
        self.same("POST", b"/api/volume")

    def test_light_covers_every_branch_of_light_spec_ok(self) -> None:
        specs = [
            b"ff0000",
            b"FF00AA",
            b"ff00a",
            b"ff00aaa",
            b"gggggg",
            b"",
            b"white",
            b"show",
            b"off",
            b"bars",
            b"chase",
            b"ends",
            b"sparkle",
            b"towerL:off",
            b"door:00FF00",
            b":ff0000",
            b"tower-L:ff0000",
            b"towerL:ff00",
            b"x" * 16 + b":show",
            b"x" * 17 + b":show",
            b"ff0000@1",
            b"ff0000@100",
            b"ff0000@0",
            b"ff0000@101",
            b"ff0000@",
            b"ff0000@5x",
            b"ff0000@007",
            b"ff0000@1000",
            b"show@50",
            b"towerR:white@25",
            b"towerL:chase@75",
            b"door:bars@0",
            b"a:b:ff0000",
            b"towerL:show@50@50",
        ]
        for spec in specs:
            with self.subTest(spec=spec):
                r = self.same("POST", b"/api/light?c=" + spec.replace(b"@", b"%40"))
                self.assertIn(r.status, (200, 400))
        self.same("POST", b"/api/light")

    def test_pir_takes_any_subset_of_three(self) -> None:
        for q in (
            b"?armed=1",
            b"?armed=0",
            b"?cooldown=30",
            b"?scene=vigil",
            b"?armed=1&cooldown=30&scene=storm",
            b"?armed=",
            b"?cooldown=",
            b"?scene=",
            b"",
            b"?nope=1",
            b"?armed=x",
        ):
            with self.subTest(q=q):
                self.same("POST", b"/api/pir" + q)

    def test_pir_refuses_a_pipe_and_an_unknown_scene(self) -> None:
        """A4/C5/C7. The three fields ride to the main loop packed "a|c|s",
        and the YAML unpacks them with find/rfind while the emulator splits
        — so a '|' inside a value is two different settings on the two
        castles. And the scene used to go straight through to pir_scene's
        select, where an unknown option is a log line nobody reads; it now
        faces the same list /api/scene checks."""
        for q, want in (
            (b"?scene=vi%7Cgil", 400),
            (b"?armed=1&scene=a%7Cb", 400),
            (b"?scene=%7C", 400),
            (b"?cooldown=30&scene=nope", 404),
            (b"?scene=storm", 200),
            (b"?armed=1&cooldown=60", 200),
        ):
            with self.subTest(q=q):
                self.assertEqual(self.same("POST", b"/api/pir" + q).status, want, q)

    def test_the_query_buffers_truncate_the_same_way(self) -> None:
        """query_param reads the whole query into 200 bytes and one value
        into 301 (v5.60, A10): the query ceiling is the only one a request
        can reach, and reaching it is a 414 on both sides."""
        for n in (117, 118, 119, 120, 134, 186, 187, 188, 197, 198, 199, 300):
            with self.subTest(n=n):
                self.same("POST", b"/api/play?f=" + b"a" * n)


class TestIdfVerdicts(unittest.TestCase):
    """The pages esp_http_server writes itself, which the emulator carries
    as a table. They are IDF's words, not the firmware's, so the check is
    that the two tables say the same thing — the C harness ports IDF 5.5.5's
    httpd_resp_send_err and castle_emu_wire.IDF_ERRORS must match it."""

    @unittest.skipIf(COMPILER is None and not IN_CI, "no host C++ compiler")
    def test_the_error_table_is_the_frameworks(self) -> None:
        shim = (ROOT / "tests" / "cxx" / "shim" / "esp_http_server.h").read_text()
        for code, text in wire.IDF_ERRORS.items():
            if code in (400, 408):
                continue  # no request the harness can send reaches these
            self.assertIn(f'r.body = "{text}"', shim, code)


if __name__ == "__main__":
    unittest.main()
