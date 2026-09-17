"""How the emulated castle answers: the reply plumbing under every handler.

Three files became four in v5.61, when the emulator followed the firmware's
own split (sd_web.h -> sd_web_upload.h) and the upload handlers moved next
door. Both halves need the same small layer beneath them — reply_json,
reply_err, esp_http_server's own verdicts, and the body reader that gives up
the way httpd's recv_wait_timeout does — and a base class is how two
handler modules share it without importing each other.

Nothing here decides anything. Every route's behaviour is in
castle_emu_http.py (reading, control, OTA) or castle_emu_upload.py (the
card's write plane); this is the shape of what they hand back.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING

import castle_emu_wire as wire

if TYPE_CHECKING:
    from castle_emu import CastleEmu

#: httpd_config_t recv_wait_timeout: how long httpd_req_recv waits for the
#: next body byte before giving up on the upload.
RECV_WAIT_S = 5.0
#: write_body / h_ota read the body this many bytes at a time.
CHUNK = 8192
#: The one content type the API answers with — sd_web.h reply_json().
JSON_MIME = "application/json"
#: sd_web.h's 503 for every route that needs the card, spelled once.
NO_SD = "no SD card"
#: query_ok()'s 414, likewise.
QUERY_TOO_LONG = "query too long"


class Replies(BaseHTTPRequestHandler):
    """The reply layer. Subclassed, never registered with a server itself."""

    server: CastleEmu  # narrowed for handlers
    timeout = RECV_WAIT_S

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # tests and background use; the port banner is enough

    def _json(self, body: dict[str, object] | list[object]) -> None:
        # Compact separators, because the firmware's replies are snprintf
        # templates with no room for pretty-printing: `{"queued":true}` on
        # the board against json.dumps's `{"queued": true}` here was two
        # bytes of drift in every queued answer, invisible to every test
        # that parsed the body instead of reading it
        # (tests/test_firmware_web_cxx.py now reads it).
        self._raw(200, json.dumps(body, separators=(",", ":")).encode(), JSON_MIME)

    def _raw(
        self, code: int, raw: bytes, ctype: str, extra: dict[str, str] | None = None
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _err(self, code: int, msg: str, extra: dict[str, str] | None = None) -> None:
        """reply_err(): a status line and a one-line text/plain body.

        `extra` carries headers a handler set BEFORE it decided to fail —
        the served pages set their CSP first thing, and httpd keeps a
        header once set, so the refusal goes out carrying it too.

        L11 (v5.62): the firmware leaves one rate-limited ring line per
        refusal (sd_web_util.h note_reply_error), so the emulator does too —
        a page that reads "http_err 404" off the ring must read it off both
        castles."""
        self.server.events.note_reply_error(code, self.server.uptime_ms())
        self._raw(code, msg.encode(), "text/plain", extra)

    def _idf(self, code: int) -> None:
        """esp_http_server's own verdicts, before any handler runs."""
        self._raw(code, wire.IDF_ERRORS[code].encode(), "text/html")

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

    def _body_chunks(self, remaining: int) -> Iterator[bytes]:
        """httpd_req_recv in CHUNK-sized reads; stops short on a stall."""
        while remaining > 0:
            got = self.rfile.read(min(remaining, CHUNK))
            if not got:
                return
            remaining -= len(got)
            yield got
