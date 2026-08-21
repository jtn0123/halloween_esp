#!/usr/bin/env python3
"""HTTP mechanics for the studio server: JSON in/out, range serving,
multipart parsing, and the error boundary.

Split from studio.py at the 500-line cap along the seam that was already
there — none of this knows a route, a track or a scene. studio.py keeps
what the endpoints MEAN; this is how bytes get on and off the socket.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path


class BadRequest(Exception):
    """A client mistake the boundary turns into a 400 instead of a traceback."""


# Request bodies are buffered whole (the multipart path needs the file in
# RAM to find the part). Nothing legitimate is anywhere near this — the
# biggest import is a few tens of MB — and without a ceiling one header
# could ask the server to allocate whatever the client claims.
MAX_BODY = 512 * 1024 * 1024


class JsonHandler(BaseHTTPRequestHandler):
    """The transport half of the studio's Handler."""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):              # quieter console
        sys.stderr.write("  %s\n" % (fmt % a))

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_range(self, p: Path, ctype: str):
        """Serve an audio file, honouring a single Range request.

        Without this the browser has to pull the whole file before it will let
        you seek in it. That is invisible on a 20-second clip and very visible
        on a four-minute import, where "audition from 1:30" means waiting for
        3 MB first. Only the one-range form is handled — that is all a media
        element ever asks for — and anything else falls back to the whole file.
        """
        total = p.stat().st_size
        rng = (self.headers.get("Range") or "").strip()
        lo, hi = 0, total - 1
        partial = False
        if rng.startswith("bytes=") and "," not in rng:
            a, _, b = rng[6:].partition("-")
            try:
                if a:
                    lo, hi = int(a), (int(b) if b else total - 1)
                elif b:                       # bytes=-500 -> the last 500
                    lo, hi = max(0, total - int(b)), total - 1
                partial = True
            except ValueError:
                partial = False
        hi = min(hi, total - 1)
        if not partial or lo > hi:
            lo, hi, partial = 0, total - 1, False
        # Only the bytes asked for leave the disk: the old read_bytes() pulled
        # a whole four-minute import into RAM to answer a 64 KB probe.
        with p.open("rb") as fh:
            fh.seek(lo)
            chunk = fh.read(hi + 1 - lo)
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(chunk)))
        if partial:
            self.send_header("Content-Range", f"bytes {lo}-{hi}/{total}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(chunk)

    def body(self) -> bytes:
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            raise BadRequest("Content-Length is not a number") from None
        if n > MAX_BODY:
            # Unread body bytes would be parsed as the next request on a
            # kept-alive connection; drop it after the 400.
            self.close_connection = True
            raise BadRequest(f"request body too large ({n} bytes; "
                             f"the limit is {MAX_BODY})")
        return self.rfile.read(n) if n > 0 else b""

    def json_body(self, raw: bytes) -> dict:
        """The request body as a dict, or a 400 — not a dead connection.

        json.loads used to raise straight through the handler: the socket
        died with a server-side traceback and no response at all, and the
        browser reported the resulting SyntaxError as the operation's
        failure. Malformed input is the CLIENT's mistake; say so."""
        try:
            out = json.loads(raw or b"{}")
        except json.JSONDecodeError as e:
            raise BadRequest(f"request body is not valid JSON: {e}") from None
        if not isinstance(out, dict):
            raise BadRequest("request body must be a JSON object")
        return out

    def _guarded(self, handler) -> None:
        """B1: the error boundary — every route answers, even when it breaks."""
        try:
            handler()
        except BadRequest as e:
            self.send_json({"ok": False, "error": str(e)}, 400)
        except BrokenPipeError:
            pass                       # the client hung up mid-response
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.send_json({"ok": False,
                            "error": f"{type(e).__name__}: {e}"}, 500)


def parse_multipart(raw: bytes, ctype: str) -> tuple[str, bytes]:
    if "boundary=" not in ctype:
        return "", b""
    b = ("--" + ctype.split("boundary=")[1].strip().strip('"')).encode()
    for part in raw.split(b):
        if b"\r\n\r\n" not in part:
            continue
        head, data = part.split(b"\r\n\r\n", 1)
        if b"filename=" not in head:
            continue
        name = head.decode("utf-8", "replace").split("filename=")[1]
        name = name.split('"')[1] if '"' in name else name.strip()
        # Strip exactly the CRLF before the boundary — rstrip(b"\r\n-")
        # also ate any REAL trailing 0x2D/0x0D/0x0A bytes of the file.
        return Path(name).name, data[:-2] if data.endswith(b"\r\n") else data
    return "", b""
