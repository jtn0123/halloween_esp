#!/usr/bin/env python3
"""HTTP mechanics for the studio server: JSON in/out, range serving,
multipart parsing, and the error boundary.

Split from studio.py at the 500-line cap along the seam that was already
there — none of this knows a route, a track or a scene. studio.py keeps
what the endpoints MEAN; this is how bytes get on and off the socket.
"""

from __future__ import annotations

import collections
import gzip
import hashlib
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler
from pathlib import Path


def scrub(line: str, limit: int = 300) -> str:
    """A line of ours, with the caller's control characters taken out.

    Anything that reaches the console after passing through a request —
    a path, a header, a filename — comes through here first: newlines and
    carriage returns become escapes rather than new log lines, and the whole
    thing is capped so one request cannot scroll the console.
    """
    out = "".join(ch if ch.isprintable() else repr(ch)[1:-1] for ch in line)
    return out if len(out) <= limit else out[:limit] + "…"


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

    # StreamRequestHandler.setup() turns this into a socket timeout, and
    # handle_one_request already answers a timed-out read by closing. A
    # client that opens a socket and never finishes its head pins a thread
    # for as long as it likes otherwise — harmless on loopback, not on
    # --lan (grade report 2026-09-01 E3). Per socket operation, not per
    # request: a 512 MB upload arrives in pieces and never waits this long
    # between two of them. The Rust twin's Conn::new sets the same 30 s.
    timeout = 30

    def log_message(self, fmt, *a):  # quieter console
        # The request line is whatever the client sent, control bytes and
        # all. Written straight through, a carriage return in a URL forges
        # a second log line — someone else's tidy "200 OK" under our name.
        sys.stderr.write("  %s\n" % scrub(fmt % a))

    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, body: bytes, ctype: str, *, etag: str | None = None):
        """Whole-body response. HTML (the 2.4 MB desk page) is served the
        way the firmware already serves it: gzipped when the browser can
        take it, and with a validator so a reload is a 304, not a resend.
        Everything else stays `no-store`, which is right for API JSON.

        `etag` is the validator; send_file() derives it from the file's
        (mtime, size) without reading the file. Left None, an HTML body
        gets one from its content hash so the old `send_bytes(read_bytes())`
        call site gains the behaviour unchanged."""
        if etag is None and ctype.startswith("text/html"):
            etag = content_etag(body)
        if etag is None:
            self._send_plain(body, ctype, 200, [("Cache-Control", "no-store")])
            return
        if etag_matches(self.headers.get("If-None-Match"), etag):
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        hdrs = [
            ("ETag", etag),
            ("Cache-Control", "no-cache"),
            ("Vary", "Accept-Encoding"),
        ]
        if ctype.startswith("text/html"):
            # E4: depth behind the escaping — same policy as the firmware's
            # set_csp (sd_web_site.h). Inline script/style must stay: the
            # desk is deliberately one self-contained file.
            hdrs.append(("Content-Security-Policy", CSP))
        if accepts_gzip(self.headers.get("Accept-Encoding")):
            body = gzipped(etag, body)
            hdrs.append(("Content-Encoding", "gzip"))
        self._send_plain(body, ctype, 200, hdrs)

    def send_file(self, p: Path, ctype: str):
        """send_bytes for a file on disk: the validator is "<mtime>-<size>",
        so a matching If-None-Match answers 304 without reading the file."""
        st = p.stat()
        etag = f'"{st.st_mtime_ns}-{st.st_size}"'
        if etag_matches(self.headers.get("If-None-Match"), etag):
            self.send_bytes(b"", ctype, etag=etag)
            return
        self.send_bytes(p.read_bytes(), ctype, etag=etag)

    def _send_plain(
        self, body: bytes, ctype: str, code: int, extra: list[tuple[str, str]]
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
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
                    partial = True
                elif b:  # bytes=-500 -> the last 500
                    lo, hi = max(0, total - int(b)), total - 1
                    partial = True
                # Neither side present ("bytes=", "bytes=-") names no
                # range at all. The flag used to be set regardless, so
                # those answered 206 over the whole file — a partial
                # response that is not partial, where the Rust twin (and
                # the intent of the fall-through below) says 200.
            except ValueError:
                partial = False
        hi = min(hi, total - 1)
        if not partial or lo > hi:
            lo, hi, partial = 0, total - 1, False
        length = hi + 1 - lo
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {lo}-{hi}/{total}")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        # Streamed in 64 KB slices: the old read_bytes() pulled a whole
        # four-minute import into RAM to answer a 64 KB probe, and the first
        # fix still buffered the full file whenever no Range was sent.
        with p.open("rb") as fh:
            fh.seek(lo)
            left = length
            while left > 0:
                chunk = fh.read(min(65536, left))
                if not chunk:
                    # The file shrank mid-serve — an interrupted stem split,
                    # a re-import over the top. Content-Length promised bytes
                    # that no longer exist, so the connection has to close:
                    # kept alive, the next request on it would be read as the
                    # tail of this body (grade report 2026-09-01 B1).
                    self.close_connection = True
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def body(self) -> bytes:
        try:
            n = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            raise BadRequest("Content-Length is not a number") from None
        if n > MAX_BODY:
            # Unread body bytes would be parsed as the next request on a
            # kept-alive connection; drop it after the 400.
            self.close_connection = True
            raise BadRequest(
                f"request body too large ({n} bytes; the limit is {MAX_BODY})"
            )
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
            pass  # the client hung up mid-response
        except Exception as e:
            import traceback

            traceback.print_exc()
            self.send_json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)


# Compressed copies of recent bodies, keyed by their ETag — one entry is the
# desk page, a second appears only while a rebuild is in flight. Bounded so
# an unusual caller cannot grow it.
_GZ: collections.OrderedDict[str, bytes] = collections.OrderedDict()
KEEP_GZ = 4


def gzipped(etag: str, body: bytes) -> bytes:
    hit = _GZ.get(etag)
    if hit is None:
        hit = gzip.compress(body, 6)
        _GZ[etag] = hit
        while len(_GZ) > KEEP_GZ:
            _GZ.popitem(last=False)
    else:
        _GZ.move_to_end(etag)
    return hit


#: The studio's own route families. "/api/<x>" for any of these is the OLD
#: spelling (v5.23 and earlier), rewritten to "/studio/<x>" for one release
#: so a desk built before the move keeps working; every other /api/* path
#: is the castle's and relays untouched (/api/scene?s=<id> included).
STUDIO_ROUTES = frozenset(
    (
        "tracks",
        "import",
        "job",
        "refresh",
        "track",
        "waveform",
        "stems",
        "stem",
        "compare",
        "probe",
        "server",
        "scene",
        "rebuild",
        "card",
    )
)
_deprecated_seen: set[str] = set()


#: The castle's own prefix. Studio routes moved out from under it (they are
#: /studio/* now), and these two spellings are compared often enough that the
#: literal was drifting between them.
API = "/api/"


def studio_path(raw: str) -> str:
    """The request's path (no query), an old /api/ spelling of a studio
    route rewritten to its /studio/ home — logged once per route."""
    url = urllib.parse.urlparse(raw)
    path = url.path
    head = path[len(API) :].split("/", 1)[0] if path.startswith(API) else ""
    if head not in STUDIO_ROUTES or (
        head == "scene" and urllib.parse.parse_qs(url.query).get("s")
    ):
        return path
    if head not in _deprecated_seen:
        _deprecated_seen.add(head)
        sys.stderr.write(
            f"  DEPRECATED: /api/{head} is now /studio/{head} "
            "(docs/API.md) — the alias goes away next release\n"
        )
    return "/studio/" + path[5:]


#: sd_web_site.h set_csp(), the same policy on the studio's pages (E4).
CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "media-src 'self' data: blob:; connect-src 'self'"
)


def content_etag(body: bytes) -> str:
    """A validator for a body with no file behind it: hash plus length."""
    return f'"{hashlib.blake2b(body, digest_size=8).hexdigest()}-{len(body)}"'


def accepts_gzip(header: str | None) -> bool:
    """`gzip` named in Accept-Encoding with a non-zero q (or no q at all)."""
    for part in (header or "").split(","):
        enc, _, params = part.strip().partition(";")
        if enc.strip().lower() not in ("gzip", "*"):
            continue
        q = params.strip().lower().removeprefix("q=").strip() if params else ""
        try:
            return float(q) > 0 if q else True
        except ValueError:
            return False
    return False


def etag_matches(header: str | None, etag: str) -> bool:
    """If-None-Match: `*`, or a list of (possibly weak) validators."""
    if not header:
        return False
    if header.strip() == "*":
        return True
    for cand in header.split(","):
        if cand.strip().removeprefix("W/") == etag:
            return True
    return False


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
        name = Path(name).name
        # Path("..").name is ".." — the staging path would be tracks/_upload/..
        # (= tracks/) and write_bytes would 500. Say 400 here instead.
        if name in ("", ".", ".."):
            raise BadRequest(f"upload filename {name!r} is not a file name")
        # Strip exactly the CRLF before the boundary — rstrip(b"\r\n-")
        # also ate any REAL trailing 0x2D/0x0D/0x0A bytes of the file.
        return name, data[:-2] if data.endswith(b"\r\n") else data
    return "", b""
