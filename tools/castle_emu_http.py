"""The emulated castle's request handlers — one per sd_web.h handler.

Routing, decoding and validation come from castle_emu_wire (the verbatim
port); this file is the handler bodies: what each route does to the card
directory and the pending-action mailbox, and the exact reply_err strings.
Split from castle_emu.py along the firmware's own seam (sd_web.h handlers
vs the state they act on) when the two together passed the 500-line cap.

Fidelity choices worth knowing when a test surprises you:
  - The handler socket times out after RECV_WAIT_S like the httpd's
    recv_wait_timeout: a PUT whose body stops arriving is unlinked and
    answered 500 "short write", not left hanging.
  - /api/files and /api/status build their JSON like the firmware: numbers
    through the same formats, strings through json_escape (json.dumps's
    table on both sides). A file the Mac wrote straight onto the card with
    a name safe_name would refuse is counted in a trailing {"skipped":N}
    element of /api/files, not listed — the desk could never play it.
  - PUT writes `<name>.part` and renames over the old copy only once every
    byte is in; a short upload leaves the PREVIOUS file exactly as it was.
  - Unknown path → 404, known path with the wrong verb → 405, request line
    over 512 bytes → 414: esp_http_server's verdicts and its own text.
"""

from __future__ import annotations

import time
from pathlib import Path

import castle_emu_wire as wire
from castle_emu_flash import BOOTLOG, CSP, FALLBACK_PAGE, REMOTE_PAGE, TYPES
from castle_emu_reply import JSON_MIME, NO_SD, QUERY_TOO_LONG
from castle_emu_upload import Uploads

#: h_ota's plausibility window: under 64 KB is no firmware, over the OTA
#: partition cannot fit. The board compares against its own partition
#: (part->size, sd_web_ota.h), so the emulator carries one per TARGET —
#: keyed by the build, not by the chip, because both builds are ESP32-S3 and
#: what differs is the flash: the Feather's 4 MB layout gives 1.75 MB slots
#: and the WROOM carrier's 8 MB gives 3.75 MB (grade report 2026-09-06 J5).
#: The keys were "s2"/"s3" until 2026-09-17, when the S2's retirement made
#: that spelling a lie about which difference is being expressed.
#: CastleEmu(ota_slot=...) picks; the default is the castle in the yard.
OTA_MIN = 65536
OTA_SLOTS = {"feather": 0x1C0000, "carrier": 0x3C0000}
OTA_SLOT = OTA_SLOTS["feather"]


class Handler(Uploads):
    """Every route the castle serves. The reply layer is castle_emu_reply's
    and the two card-writing routes are castle_emu_upload's; what is left
    here is reading, control and the flasher."""

    # -- plumbing ----------------------------------------------------------

    def handle(self) -> None:
        if self.server.serial is None:
            return super().handle()
        with self.server.serial:
            super().handle()

    def _wedge(self) -> None:
        """Pre-v5.22: the single HTTP task is busy streaming the song."""
        if not self.server.wedge:
            return
        while True:
            with self.server.state.lock:
                playing = bool(self.server.state.track)
            if not playing:
                return
            time.sleep(0.25)

    def _dispatch(self) -> None:
        # The target off the REQUEST LINE, not self.path: http.server
        # collapses a leading "//" to "/" before it hands the path over,
        # and esp_http_server does not — so "GET //" served the desk here
        # and 404'd on the board. Everything after this is the exact byte
        # string the board's parser would see.
        words = self.requestline.split()
        raw = (words[1] if len(words) > 1 else self.path).encode("latin-1")
        self._wedge()
        if len(raw) > wire.MAX_URI:
            return self._idf(414)
        handler, err = wire.route(self.command, raw)
        if handler is None:
            return self._idf(err)
        try:
            getattr(self, handler)(raw)
        except (BrokenPipeError, ConnectionResetError):
            # The client hung up before the reply was written - castle_link's
            # 2 s read budget for a POST runs out under a loaded test run and
            # it closes the socket. That is the client's verdict, not an
            # emulator bug, and there is nobody left to send a 500 to: the
            # 500 below would EPIPE on the same dead socket and socketserver
            # would print both tracebacks into the test log (seen 2026-09-06).
            self.close_connection = True
        except Exception as e:  # the fuzz asserts this never happens
            self._err(500, f"emulator bug: {type(e).__name__}: {e}")

    do_GET = do_POST = do_PUT = do_DELETE = _dispatch
    do_HEAD = do_PATCH = do_OPTIONS = _dispatch

    # -- GET ---------------------------------------------------------------

    def h_status(self, raw: bytes) -> None:
        # surrogateescape: a track named by raw bytes goes out as raw bytes
        self._raw(
            200, self.server.status_text().encode("utf-8", "surrogateescape"), JSON_MIME
        )

    def h_health(self, _raw: bytes) -> None:
        # sd_read_errors (A8, v5.61): transfers off the card that failed and
        # were torn down instead of being framed as a short success. Always
        # 0 here — the emulated card is a host directory, and a read of one
        # does not NAK a sector — but the KEY is part of the reply's shape,
        # and a desk that shows the number must find it on both castles.
        # L7 (v5.62): heap_min_kb is the LOW-WATER mark of internal heap —
        # the number that explains a crash, where /api/status's heap_free_kb
        # is only what is free now, after the allocation that failed was
        # given back. Fixed here, like heap_free_kb, and equal to what the
        # C harness's shim reports so the two replies stay byte-identical.
        # L4: sd_last_error is "<path>@<offset>" of the last torn transfer,
        # "" on a healthy castle — which a host directory always is.
        self._json(
            {
                "boots": 3,
                "crashes": 0,
                "last_reset": "power-on",
                "was_crash": False,
                "sd_read_errors": 0,
                "heap_min_kb": 64,
                "sd_last_error": "",
            }
        )

    def h_events(self, _raw: bytes) -> None:
        """The main loop's own record (castle_emu_events.py), oldest first."""
        self._raw(200, self.server.events.json().encode(), JSON_MIME)

    def h_list(self, raw: bytes) -> None:
        if not self.server.sd_mounted:
            return self._err(503, NO_SD)
        if wire.query_truncated(raw):
            return self._err(414, QUERY_TOO_LONG)
        # B2: ?d=<subdir> lists inside the card, validated like /sd/ paths.
        sub = wire.query_param(raw, "d")
        base = self.server.sd_dir
        if sub:
            if not wire.safe_subpath(sub):
                return self._err(400, "bad path")
            name = wire.fat_path(sub)
            if name is None:
                return self._err(404, "no such directory")
            base = base / name
            if not base.is_dir():
                return self._err(404, "no such directory")
        items = []
        skipped = 0
        for p in sorted(base.iterdir()):
            if p.name.startswith("."):
                continue
            if not wire.safe_name(p.name.encode("utf-8", "surrogateescape")):
                skipped += 1  # the Mac's doing, not the desk's: counted
                continue
            try:  # stat() failing is size -1 on the board
                size = p.stat().st_size if p.is_file() else 0
            except OSError:
                size = -1
            # The firmware's template: name through json_escape, the rest raw.
            items.append(
                '{"name":"%s","size":%d,"dir":%s}'
                % (wire.json_escape(p.name), size, "true" if p.is_dir() else "false")
            )
        if skipped:
            items.append('{"skipped":%d}' % skipped)
        self._raw(
            200,
            ("[" + ",".join(items) + "]").encode("utf-8", "surrogateescape"),
            "application/json",
        )

    def h_bootlog(self, _raw: bytes) -> None:
        self._raw(200, BOOTLOG, "text/plain")

    def h_remote(self, _raw: bytes) -> None:
        self._raw(
            200,
            REMOTE_PAGE.encode(),
            "text/html; charset=utf-8",
            {"Content-Security-Policy": CSP},
        )

    def _subpath(self, raw: bytes, prefix: bytes) -> bytes:
        rel = wire.url_decode(raw[len(prefix) :])
        q = rel.find(b"?")
        return rel[:q] if q >= 0 else rel

    def _send_file(
        self,
        f: Path,
        encoding: str | None = None,
        ctype: str | None = None,
        csp: bool = False,
    ) -> bool:
        if not f.is_file():
            return False
        extra: dict[str, str] = {}
        if encoding:
            extra["Content-Encoding"] = encoding
        if csp:
            extra["Content-Security-Policy"] = CSP
        self._raw(
            200,
            f.read_bytes(),
            ctype or TYPES.get(f.suffix, "application/octet-stream"),
            extra or None,
        )
        return True

    def h_sd_get(self, raw: bytes) -> None:
        if not self.server.sd_mounted:
            return self._err(503, NO_SD)
        rel = self._subpath(raw, b"/sd/")
        if not wire.safe_subpath(rel):
            return self._err(400, "bad path")
        name = wire.fat_path(rel)
        if name is None or not self._send_file(self.server.sd_dir / name):
            return self._err(404, "no such file")

    def h_site(self, raw: bytes) -> None:
        # set_csp() runs FIRST in the firmware, so the refusals below carry
        # the header too — httpd holds a header once set, whatever the
        # handler decides afterwards.
        rel = self._subpath(raw, b"/site/")
        if not wire.safe_subpath(rel):
            return self._err(400, "bad path", {"Content-Security-Policy": CSP})
        name = wire.fat_path(rel)
        f = self.server.sd_dir / "site" / (name or "")
        if (
            not self.server.sd_mounted
            or name is None
            or not self._send_file(f, csp=True)
        ):
            return self._err(404, "not on card", {"Content-Security-Policy": CSP})

    def h_root(self, _raw: bytes) -> None:
        site = self.server.sd_dir / "site"
        if self.server.sd_mounted and (
            self._send_file(site / "index.html.gz", "gzip", TYPES[".html"], csp=True)
            or self._send_file(site / "index.html", csp=True)
        ):
            return
        self._raw(
            200,
            FALLBACK_PAGE.encode(),
            "text/html; charset=utf-8",
            {"Content-Security-Policy": CSP},
        )

    # -- POST: show control, all queued ------------------------------------

    def h_play(self, raw: bytes) -> None:
        if wire.query_truncated(raw):
            return self._err(414, QUERY_TOO_LONG)
        f = wire.query_param(raw, "f")
        if not wire.safe_name(f):
            return self._err(400, "need ?f=<file>")
        self.server.queue("PLAY", wire.fs_name(f))
        self._json({"queued": True})

    def h_scene(self, raw: bytes) -> None:
        if wire.query_truncated(raw):
            return self._err(414, QUERY_TOO_LONG)
        s = wire.query_param(raw, "s")
        if not s:
            return self._err(400, "need ?s=<scene>")
        if not self.server.scenes:
            return self._err(503, "scene list not ready")
        if wire.fs_name(s) not in self.server.scenes:
            return self._err(404, "unknown scene")
        self.server.queue("SCENE", wire.fs_name(s))
        self._json({"queued": True})

    def h_stop(self, _raw: bytes) -> None:
        self.server.queue("STOP", "")
        self._json({"queued": True})

    def h_show_start(self, _raw: bytes) -> None:
        self.server.queue("SHOW", "1")
        self._json({"queued": True})

    def h_show_stop(self, _raw: bytes) -> None:
        self.server.queue("SHOW", "0")
        self._json({"queued": True})

    def h_blackout(self, _raw: bytes) -> None:
        self.server.queue("BLACKOUT", "")
        self._json({"queued": True})

    def h_volume(self, raw: bytes) -> None:
        if wire.query_truncated(raw):
            return self._err(414, QUERY_TOO_LONG)
        v = wire.query_param(raw, "v")
        digits = bool(v) and len(v) <= 3 and v.isdigit()
        pct = int(v) if digits else -1
        if pct < 0 or pct > 100:
            return self._err(400, "need ?v=0..100")
        self.server.queue("VOLUME", str(pct))
        self._json({"queued": True})

    def h_light(self, raw: bytes) -> None:
        if wire.query_truncated(raw):
            return self._err(414, QUERY_TOO_LONG)
        c = wire.query_param(raw, "c")
        if not wire.light_spec_ok(c):
            return self._err(
                400, "need ?c=[zone:]RRGGBB|white|bars|chase|ends|show|off[@pct]"
            )
        self.server.queue("LIGHT", c.decode())
        self._json({"queued": True})

    def h_pir(self, raw: bytes) -> None:
        if wire.query_truncated(raw):
            return self._err(414, QUERY_TOO_LONG)
        a, c, s = (wire.query_param(raw, k) for k in ("armed", "cooldown", "scene"))
        if not (a or c or s):
            return self._err(400, "need armed=, cooldown= or scene=")
        ok, a = wire.pir_armed_ok(a)
        if not ok:
            return self._err(400, "bad armed")
        if not wire.pir_cooldown_ok(c):
            return self._err(400, "bad cooldown")
        # A4/C5/C7: the fields ride packed with '|' and the YAML unpacks
        # them with find/rfind while this splits, so a '|' in a value is
        # refused here; and the scene faces the list /api/scene checks.
        if any(b"|" in x for x in (a, c, s)):
            return self._err(400, "bad separator")
        if s and not self.server.scenes:
            return self._err(503, "scene list not ready")
        if s and wire.fs_name(s) not in self.server.scenes:
            return self._err(404, "unknown scene")
        self.server.queue("PIRCFG", "|".join(wire.fs_name(x) for x in (a, c, s)))
        self._json({"queued": True})

    # -- PUT/DELETE: the card ----------------------------------------------

    def h_ota(self, _raw: bytes) -> None:
        n = self._content_len()
        if n is None:
            return self._idf(400)
        if n < OTA_MIN or n > self.server.ota_slot:
            return self._err(400, "implausible image size")
        # Nothing else may touch the card or burn CPU while flash is being
        # written, and J3 (grade report 2026-09-17 pm) made that a gate rather
        # than a convention: the flag refuses the next scene start, and "halt"
        # stops the one already on the strips — a looping scene would
        # otherwise re-fire its own audio thirty seconds into the upload.
        # Both before the first byte, exactly as sd_web_ota.h orders them.
        self.server.quiesce = True
        self.server.queue("SCENE", "halt")
        got, first = 0, True
        try:
            for chunk in self._body_chunks(n):
                if first and chunk[0] != 0xE9:  # app image magic
                    self.server.quiesce = False
                    return self._err(500, "ota write failed")
                first = False
                got += len(chunk)
        except OSError:  # TimeoutError is one of these
            pass
        if got != n:
            self.server.quiesce = False
            return self._err(500, "ota write failed")
        self._json({"flashed": True, "rebooting": True})
        self.server.queue("RESTART", "")
        # Flash is written, so the flag comes down — the reboot is in a latch
        # of its own and cannot be talked out of it (sd_web_state.h).
        self.server.quiesce = False
