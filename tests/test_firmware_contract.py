"""The emulator held to the firmware — by reading the firmware.

firmware/sd_web.h (+ sd_web_ota.h, sd_web_site.h, sd_web_remote.h,
sd_web_state.h) is the contract the studio speaks to; tools/castle_emu*.py is the stand-in every
hardware-free test drives. The two can only be trusted together if a change
to either is caught here. So these tests PARSE the C at test time — the
reg() table, every reply_err() string per handler, safe_name's rule,
query_param's buffer sizes, h_status's JSON keys — and hold the emulator's
port to what they find. Nothing below is hand-copied from the firmware;
that is the point.
"""

from __future__ import annotations

import http.client
import json
import random
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import castle_emu
import castle_emu_http
import castle_emu_wire as wire

FW = ROOT / "firmware"
SD_WEB = (FW / "sd_web.h").read_text()
SD_OTA = (FW / "sd_web_ota.h").read_text()
SD_SITE = (FW / "sd_web_site.h").read_text()
SD_REMOTE = (FW / "sd_web_remote.h").read_text()
SD_STATE = (FW / "sd_web_state.h").read_text()
EMU_HTTP = (ROOT / "tools" / "castle_emu_http.py").read_text()

#: reply_err strings the emulator has no way to produce: flash, heap and
#: FAT failures of the real board. Everything else must be mirrored.
HARDWARE_ONLY = {"no memory", "opendir failed", "no OTA slot",
                 "ota begin failed", "ota end failed", "could not select slot"}


def c_functions(*sources: str) -> dict[str, str]:
    """name → body for every `inline <type> name(` at column 0."""
    out: dict[str, str] = {}
    pat = re.compile(r"^inline [\w:]+(?: \*)? ?(\w+)\(", re.MULTILINE)
    for src in sources:
        hits = list(pat.finditer(src))
        for i, m in enumerate(hits):
            end = hits[i + 1].start() if i + 1 < len(hits) else len(src)
            out[m.group(1)] = src[m.start():end]
    return out


def reply_errs(body: str) -> set[tuple[int, str]]:
    return {(int(c), msg) for c, msg in
            re.findall(r'reply_err\(req, "(\d{3}) [^"]*", "([^"]*)"\)', body)}


def emu_errs(handler: str) -> set[tuple[int, str]]:
    """Every self._err(code, "msg") inside one emulator handler method."""
    m = re.search(rf"    def {handler}\(self.*?(?=\n    def |\Z)", EMU_HTTP, re.DOTALL)
    assert m, f"emulator has no {handler}"
    return {(int(c), msg) for c, msg in
            re.findall(r'self\._err\((\d{3}), "([^"]*)"\)', m.group(0))}


def firmware_routes() -> list[tuple[str, str, str]]:
    return [(p, m, h) for p, m, h in
            re.findall(r'reg\("([^"]+)", HTTP_(\w+), (\w+)\);', SD_WEB)]


FUNCS = c_functions(SD_WEB, SD_OTA, SD_SITE, SD_REMOTE)


def grab(pattern: str, text: str, group: int = 1) -> str:
    """One regex capture, or a loud failure naming what the parser expected."""
    m = re.search(pattern, text)
    assert m, f"firmware no longer matches /{pattern}/ — update the contract test"
    return m.group(group)


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
        body = FUNCS[handler]
        found = reply_errs(body)
        for helper in ("write_body", "send_sd_file"):
            if f"{helper}(" in body:
                found |= reply_errs(FUNCS[helper])
        return found

    def test_emulator_uses_the_firmwares_strings_and_codes(self) -> None:
        for _p, _m, h in firmware_routes():
            fw = {e for e in self.errs_for(h) if e[1] not in HARDWARE_ONLY}
            emu = emu_errs(h)
            self.assertEqual(emu, fw, f"{h}: emulator {emu} vs firmware {fw}")

    def test_no_emulator_error_string_is_invented(self) -> None:
        all_fw = set().union(*(reply_errs(b) for b in FUNCS.values()))
        for c, msg in re.findall(r'self\._err\((\d{3}), "([^"]*)"\)', EMU_HTTP):
            self.assertIn((int(c), msg), all_fw)


class TestNameRules(unittest.TestCase):
    """safe_name / safe_subpath / url_decode / query_param, re-derived."""

    def ref_safe_name(self):
        body = FUNCS["safe_name"]
        limit = int(grab(r"n\.size\(\) >= (\d+)", body))
        lead = grab(r"n\[0\] == '(.)'", body).encode()
        finds = [f.encode() for f in re.findall(r"""n\.find\(["'](.+?)["']\)""", body)]
        # The per-byte loop: `c < 0x20` and each `c == <literal>`, read off
        # the C so a new forbidden byte in the firmware fails here first.
        below = int(grab(r"c < (0x[0-9a-fA-F]+)", body), 16)
        bad = set()
        for lit in re.findall(r"c == (0x[0-9a-fA-F]+|'[^']+')", body):
            bad.add(int(lit, 16) if lit.startswith("0x")
                    else ord(lit[1:-1].encode().decode("unicode_escape")))
        self.assertEqual(limit, wire.NAME_MAX)
        self.assertEqual(bad, {0x7F, ord('"'), ord("\\")})
        return lambda n: (bool(n) and len(n) < limit and n[:1] != lead
                          and all(f not in n for f in finds)
                          and all(c >= below and c not in bad for c in n))

    def ref_safe_subpath(self):
        body = FUNCS["safe_subpath"]
        limit = int(grab(r"p\.size\(\) > (\d+)", body))
        leads = [c.encode() for c in re.findall(r"p\[0\] == '(.)'", body)]
        finds = [f.encode() for f in re.findall(r"""p\.find\(["'](.+?)["']\)""", body)]
        self.assertEqual(limit, wire.SUBPATH_MAX)
        return lambda p: (bool(p) and len(p) <= limit and p[:1] not in leads
                          and all(f not in p for f in finds))

    def corpus(self, seed: int = 7) -> list[bytes]:
        rng = random.Random(seed)
        alphabet = b"ab./\\?%+ \x00\xc3\xa9\"'\t\x1f\x7f"
        out = [b"", b".", b"..", b"a", b"a/b", b"/a", b".a", b"a..b", b"a" * 99,
               b"a" * 100, b"a" * 140, b"a" * 141, b"\xc3\xa9" * 50, b"\x00",
               b"a\x00/..", b"..\\x", b"a?b", b'a"b.mp3', b"a\\b.mp3", b"a\tb",
               b"a\x1fb", b"a\x7fb", b"a b", b"a'b", b"\xc3\xa9.mp3", b"a\x80b"]
        out += [bytes(rng.choice(alphabet) for _ in range(rng.randint(0, 150)))
                for _ in range(1500)]
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
        """60 accented characters are 120 UTF-8 bytes: the board says no."""
        self.assertFalse(wire.safe_name("é".encode() * 60))
        self.assertTrue(wire.safe_name("é".encode() * 40))

    def test_safe_name_refuses_what_would_break_the_json(self) -> None:
        """A quote, a backslash, a control byte or DEL never gets ONTO the
        card through us: safe_name says no at the door (and since v5.25
        json_escape keeps the parse alive for names that got there another
        way). High bytes (UTF-8) and spaces stay welcome."""
        for bad in (b'a"b.mp3', b"a\\b.mp3", b"a\tb", b"a\nb", b"a\rb", b"\x00",
                    b"ab\x00cd.mp3", b"a\x1fb", b"a\x7fb", b'"', b"\\"):
            self.assertFalse(wire.safe_name(bad), repr(bad))
        for good in (b"a b.mp3", b"a'b.mp3", "é.mp3".encode(), b"a\x80b",
                     b"x-y_z (1).mp3", b"a~b", b"a\xffb"):
            self.assertTrue(wire.safe_name(good), repr(good))

    def test_query_param_buffers_are_the_firmwares(self) -> None:
        body = FUNCS["query_param"]
        self.assertEqual(int(grab(r"char q\[(\d+)\]", body)), wire.QUERY_BUF)
        self.assertEqual(int(grab(r"char val\[(\d+)\]", body)), wire.VALUE_BUF)
        self.assertIn("url_decode(val)", body)   # values ARE decoded

    def test_url_decode_plus_and_bad_hex(self) -> None:
        """'+' is a space and "%zz" is strtol's 0 — both read off the C."""
        self.assertIn("'+'", FUNCS["url_decode"])
        self.assertIn("strtol", FUNCS["url_decode"])
        self.assertEqual(wire.url_decode(b"a+b%20c"), b"a b c")
        self.assertEqual(wire.url_decode(b"a%zzb"), b"a\x00b")
        self.assertEqual(wire.url_decode(b"a%4"), b"a%4")   # needs two chars
        self.assertEqual(wire.url_decode(b"%4g"), b"\x04")  # leading digit only

    def test_name_from_uri_cuts_at_the_decoded_question_mark(self) -> None:
        self.assertIn("n.find('?')", FUNCS["name_from_uri"])
        self.assertEqual(wire.name_from_uri(b"/api/files/a%3Fb.mp3", b"/api/files/"),
                         b"a")
        self.assertEqual(wire.name_from_uri(b"/api/files/a.mp3?x=1", b"/api/files/"),
                         b"a.mp3")

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
        self.assertEqual(wire.query_param(b"/api/scene?s=" + b"v" * 119, "s"),
                         b"v" * 119)
        self.assertEqual(wire.query_param(b"/api/scene?s=v&" + b"x" * 197, "s"), b"")


class TestValidatorConstants(unittest.TestCase):
    def test_volume_light_and_ota_limits(self) -> None:
        self.assertIn("v.size() <= 3", FUNCS["h_volume"])
        self.assertIn("pct > 100", FUNCS["h_volume"])
        self.assertIn("light_spec_ok(c)", FUNCS["h_light"])
        # The validator lives in sd_web_state.h; its shape is pinned by example.
        self.assertIn("spec.size() == 6", SD_STATE)
        self.assertIn("zone.size() > 16", SD_STATE)
        for c, ok in ((b"ff0000", True), (b"show", True), (b"towerL:off", True),
                      (b"door:00FF00", True), (b":ff0000", False), (b"tower-L:ff0000", False),
                      (b"towerL:ff00", False), (b"x" * 17 + b":show", False), (b"", False)):
            self.assertEqual(castle_emu_http.light_spec_ok(c), ok, c)
        pat = r"content_len < (\d+) \|\| req->content_len > ([\w>-]+)"
        self.assertEqual(int(grab(pat, FUNCS["h_ota"], 1)), castle_emu_http.OTA_MIN)
        self.assertEqual(grab(pat, FUNCS["h_ota"], 2), "part->size")   # the slot

    def test_status_keys_are_the_firmwares(self) -> None:
        fmt = FUNCS["h_status"]
        keys = set(re.findall(r'\\"(\w+)\\":', fmt))
        emu = castle_emu.CastleEmu(port=0)
        self.addCleanup(emu.server_close)
        st = emu.status_json()
        pir = st["pir"]
        assert isinstance(pir, dict)
        self.assertEqual(set(st) | set(pir), keys)

    def test_pending_mailbox_is_one_slot(self) -> None:
        """sd_web_state.h: set_pending overwrites; take_pending empties."""
        self.assertIn("g_pending = {type, std::move(arg)};", SD_STATE)
        self.assertIn("g_pending = {NONE, \"\"};", SD_STATE)


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
        for path in ("/api", "/api/", "/api/files2", "/apix/status", "/nope",
                     "/api/status/", "/remote/x", "/sd", "/site"):
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
        while time.monotonic() < deadline and json.loads(
                self.call("GET", "/api/status")[1])["volume"] != 33:
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
