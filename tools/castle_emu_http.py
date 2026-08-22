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

import json
import re
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import TYPE_CHECKING

import castle_emu_wire as wire

if TYPE_CHECKING:
    from castle_emu import CastleEmu

#: httpd_config_t recv_wait_timeout: how long httpd_req_recv waits for the
#: next body byte before giving up on the upload.
RECV_WAIT_S = 5.0
#: h_ota's plausibility window: under 64 KB is no firmware, over the OTA
#: partition (1.75 MB on the S2 layout) cannot fit.
OTA_MIN = 65536
OTA_SLOT = 0x1C0000
CHUNK = 8192
#: The one content type the API answers with — sd_web.h reply_json().
JSON_MIME = "application/json"

#: sd_web_site.h content_type(): suffix → MIME.
_TYPES = {".html": "text/html; charset=utf-8", ".js": "application/javascript",
          ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png",
          ".json": JSON_MIME, ".mp3": "audio/mpeg", ".wav": "audio/wav"}

#: firmware/sd_web_remote.h kRemotePage, byte for byte — the phone remote
#: is embedded in flash, so the emulator lifts it out of the C raw string
#: rather than keeping a placeholder nobody could test against (JB2-6).
_REMOTE_H = Path(__file__).resolve().parent.parent / "firmware" / "sd_web_remote.h"


def _remote_page() -> str:
    m = re.search(r'kRemotePage\[\] = R"HTML\((.*?)\)HTML";',
                  _REMOTE_H.read_text(), re.DOTALL)
    if not m:
        raise RuntimeError(f"no kRemotePage raw string in {_REMOTE_H}")
    return m.group(1)


REMOTE_PAGE = _remote_page()
FALLBACK_PAGE = ("<!doctype html><meta charset=utf-8><title>Castle</title>"
                 "<h1>Castle</h1><p>emulated fallback page</p>")


_ZONE_CHARS = set(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")


def light_spec_ok(c: bytes) -> bool:
    """sd_web_state.h light_spec_ok, byte for byte: "RRGGBB"|show|off with an
    optional "<zone>:" prefix that drives one strip (the desk's channel test)."""
    zone, sep, spec = c.partition(b":")
    if not sep:
        zone, spec = b"", c
    elif not zone or len(zone) > 16 or any(b not in _ZONE_CHARS for b in zone):
        return False
    spec, at, pct = spec.partition(b"@")
    if at:
        if not pct.isdigit() or len(pct) > 3 or not 1 <= int(pct) <= 100:
            return False
    hex6 = len(spec) == 6 and all(chr(b) in "0123456789abcdefABCDEF" for b in spec)
    return hex6 or spec in (b"white", b"bars", b"chase", b"ends", b"show", b"off")


class Handler(BaseHTTPRequestHandler):
    server: CastleEmu  # narrowed for handlers
    timeout = RECV_WAIT_S

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # tests and background use; the port banner is enough

    def handle(self) -> None:
        if self.server.serial is None:
            return super().handle()
        with self.server.serial:
            super().handle()

    def _json(self, body: dict[str, object] | list[object]) -> None:
        self._raw(200, json.dumps(body).encode(), JSON_MIME)

    def _raw(self, code: int, raw: bytes, ctype: str,
             extra: dict[str, str] | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _err(self, code: int, msg: str) -> None:
        """reply_err(): a status line and a one-line text/plain body."""
        self._raw(code, msg.encode(), "text/plain")

    def _idf(self, code: int) -> None:
        """esp_http_server's own verdicts, before any handler runs."""
        self._raw(code, wire.IDF_ERRORS[code].encode(), "text/html")

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
        # self.path is the request target decoded as latin-1, so this is
        # the exact byte string the board's parser would see.
        raw = self.path.encode("latin-1")
        self._wedge()
        if len(raw) > wire.MAX_URI:
            return self._idf(414)
        handler, err = wire.route(self.command, raw)
        if handler is None:
            return self._idf(err)
        try:
            getattr(self, handler)(raw)
        except Exception as e:  # the fuzz asserts this never happens
            self._err(500, f"emulator bug: {type(e).__name__}: {e}")

    do_GET = do_POST = do_PUT = do_DELETE = _dispatch
    do_HEAD = do_PATCH = do_OPTIONS = _dispatch

    def _content_len(self) -> int | None:
        """req->content_len, or None when http_parser would have 400'd the
        header (not a non-negative integer)."""
        raw = self.headers.get("Content-Length")
        if raw is None:
            return 0
        try:
            n = int(raw)
        except ValueError:
            return None
        return n if n >= 0 else None

    def _body_chunks(self, remaining: int):
        """httpd_req_recv in CHUNK-sized reads; stops short on a stall."""
        while remaining > 0:
            got = self.rfile.read(min(remaining, CHUNK))
            if not got:
                return
            remaining -= len(got)
            yield got

    # -- GET ---------------------------------------------------------------

    def h_status(self, raw: bytes) -> None:
        # surrogateescape: a track named by raw bytes goes out as raw bytes
        self._raw(200, self.server.status_text().encode("utf-8", "surrogateescape"),
                  JSON_MIME)

    def h_health(self, _raw: bytes) -> None:
        self._json({"boots": 3, "crashes": 0,
                    "last_reset": "power-on", "was_crash": False})

    def h_list(self, raw: bytes) -> None:
        if not self.server.sd_mounted:
            return self._err(503, "no SD card")
        items = []
        skipped = 0
        for p in sorted(self.server.sd_dir.iterdir()):
            if p.name.startswith("."):
                continue
            if not wire.safe_name(p.name.encode("utf-8", "surrogateescape")):
                skipped += 1          # the Mac's doing, not the desk's: counted
                continue
            try:                      # stat() failing is size -1 on the board
                size = p.stat().st_size if p.is_file() else 0
            except OSError:
                size = -1
            # The firmware's template: name through json_escape, the rest raw.
            items.append('{"name":"%s","size":%d,"dir":%s}'
                         % (wire.json_escape(p.name), size,
                            "true" if p.is_dir() else "false"))
        if skipped:
            items.append('{"skipped":%d}' % skipped)
        self._raw(200, ("[" + ",".join(items) + "]").encode("utf-8", "surrogateescape"),
                  "application/json")

    def h_bootlog(self, raw: bytes) -> None:
        self._raw(200, b"boot log: 2 lines, 0 dropped\n[I][emu] up\n", "text/plain")

    def h_remote(self, raw: bytes) -> None:
        self._raw(200, REMOTE_PAGE.encode(), "text/html; charset=utf-8")

    def _subpath(self, raw: bytes, prefix: bytes) -> bytes:
        rel = wire.url_decode(raw[len(prefix):])
        q = rel.find(b"?")
        return rel[:q] if q >= 0 else rel

    def _send_file(self, f: Path, encoding: str | None = None,
                   ctype: str | None = None) -> bool:
        if not f.is_file():
            return False
        extra = {"Content-Encoding": encoding} if encoding else None
        self._raw(200, f.read_bytes(),
                  ctype or _TYPES.get(f.suffix, "application/octet-stream"), extra)
        return True

    def h_sd_get(self, raw: bytes) -> None:
        if not self.server.sd_mounted:
            return self._err(503, "no SD card")
        rel = self._subpath(raw, b"/sd/")
        if not wire.safe_subpath(rel):
            return self._err(400, "bad path")
        if not self._send_file(self.server.sd_dir / wire.fs_name(rel)):
            return self._err(404, "no such file")

    def h_site(self, raw: bytes) -> None:
        rel = self._subpath(raw, b"/site/")
        if not wire.safe_subpath(rel):
            return self._err(400, "bad path")
        f = self.server.sd_dir / "site" / wire.fs_name(rel)
        if not self.server.sd_mounted or not self._send_file(f):
            return self._err(404, "not on card")

    def h_root(self, raw: bytes) -> None:
        site = self.server.sd_dir / "site"
        if self.server.sd_mounted and (
                self._send_file(site / "index.html.gz", "gzip", _TYPES[".html"])
                or self._send_file(site / "index.html")):
            return
        self._raw(200, FALLBACK_PAGE.encode(), "text/html; charset=utf-8")

    # -- POST: show control, all queued ------------------------------------

    def h_play(self, raw: bytes) -> None:
        f = wire.query_param(raw, "f")
        if not wire.safe_name(f):
            return self._err(400, "need ?f=<file>")
        self.server.queue("PLAY", wire.fs_name(f))
        self._json({"queued": True})

    def h_scene(self, raw: bytes) -> None:
        s = wire.query_param(raw, "s")
        if not s:
            return self._err(400, "need ?s=<scene>")
        ids = [i.encode() for i in self.server.scenes]
        if ids and s not in ids:
            return self._err(404, "unknown scene")
        self.server.queue("SCENE", wire.fs_name(s))
        self._json({"queued": True})

    def h_stop(self, raw: bytes) -> None:
        self.server.queue("STOP", "")
        self._json({"queued": True})

    def h_show_start(self, raw: bytes) -> None:
        self.server.queue("SHOW", "1")
        self._json({"queued": True})

    def h_show_stop(self, raw: bytes) -> None:
        self.server.queue("SHOW", "0")
        self._json({"queued": True})

    def h_blackout(self, raw: bytes) -> None:
        self.server.queue("BLACKOUT", "")
        self._json({"queued": True})

    def h_volume(self, raw: bytes) -> None:
        v = wire.query_param(raw, "v")
        digits = bool(v) and len(v) <= 3 and v.isdigit()
        pct = int(v) if digits else -1
        if pct < 0 or pct > 100:
            return self._err(400, "need ?v=0..100")
        self.server.queue("VOLUME", str(pct))
        self._json({"queued": True})

    def h_light(self, raw: bytes) -> None:
        c = wire.query_param(raw, "c")
        if not light_spec_ok(c):
            return self._err(400, "need ?c=[zone:]RRGGBB|white|bars|chase|ends|show|off[@pct]")
        self.server.queue("LIGHT", c.decode())
        self._json({"queued": True})

    def h_pir(self, raw: bytes) -> None:
        a, c, s = (wire.query_param(raw, k) for k in ("armed", "cooldown", "scene"))
        if not (a or c or s):
            return self._err(400, "need armed=, cooldown= or scene=")
        self.server.queue("PIRCFG", "|".join(wire.fs_name(x) for x in (a, c, s)))
        self._json({"queued": True})

    # -- PUT/DELETE: the card ----------------------------------------------

    def h_put(self, raw: bytes) -> None:
        if not self.server.sd_mounted:
            return self._err(503, "no SD card")
        sub, prefix = "", b"/api/files/"
        if raw.startswith(b"/api/site/"):
            sub, prefix = "site", b"/api/site/"
        if raw.startswith(b"/api/scenes/"):
            sub, prefix = "scenes", b"/api/scenes/"
        name = wire.name_from_uri(raw, prefix)
        if not wire.safe_name(name):
            return self._err(400, "bad filename")
        n = self._content_len()
        if n is None:
            return self._idf(400)
        dest = self.server.sd_dir / sub if sub else self.server.sd_dir
        dest.mkdir(parents=True, exist_ok=True)
        target = dest / wire.fs_name(name)
        # write_body: into the sidecar, then unlink + rename (FAT's rename
        # will not overwrite). A short upload costs the sidecar only; the
        # previous copy of `target` is untouched.
        part = target.with_name(target.name + ".part")
        try:
            f = open(part, "wb")
        except OSError:
            return self._err(500, "cannot create file")
        written = 0
        with f:
            try:
                for chunk in self._body_chunks(n):
                    f.write(chunk)
                    written += len(chunk)
            except OSError:            # TimeoutError is one of these
                pass
        if written != n:
            part.unlink(missing_ok=True)     # the sidecar only
            return self._err(500, "short write")
        try:
            target.unlink(missing_ok=True)
            part.rename(target)
        except OSError:
            part.unlink(missing_ok=True)
            return self._err(500, "rename failed")
        card = f"/sd/{sub}/{target.name}" if sub else f"/sd/{target.name}"
        self._json({"path": card, "bytes": written})

    def h_delete(self, raw: bytes) -> None:
        if not self.server.sd_mounted:
            return self._err(503, "no SD card")
        name = wire.name_from_uri(raw, b"/api/files/")
        if not wire.safe_name(name):
            return self._err(400, "bad filename")
        try:
            (self.server.sd_dir / wire.fs_name(name)).unlink()
        except OSError:
            return self._err(404, "no such file")
        self._json({"deleted": True})

    def h_ota(self, raw: bytes) -> None:
        n = self._content_len()
        if n is None:
            return self._idf(400)
        if n < OTA_MIN or n > OTA_SLOT:
            return self._err(400, "implausible image size")
        got, first = 0, True
        try:
            for chunk in self._body_chunks(n):
                if first and chunk[0] != 0xE9:      # app image magic
                    return self._err(500, "ota write failed")
                first = False
                got += len(chunk)
        except (TimeoutError, OSError):
            pass
        if got != n:
            return self._err(500, "ota write failed")
        self._json({"flashed": True, "rebooting": True})
        self.server.queue("RESTART", "")
