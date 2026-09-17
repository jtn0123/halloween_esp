"""HTTP routes for the local Castle Radio demo."""

import json
import mimetypes
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

import desktop_tools
import device_bridge
import library_ops
import remote_library
from radio_jobs import (
    CATALOG,
    DATA,
    HERE,
    JOBS,
    LIBRARY,
    LOCK,
    POOL,
    catalog,
    cookie_audio_format,
    cookie_audio_quality,
    playback_format,
    playback_quality,
    prepare,
    reprocess_job,
    update,
)

OPUS_SUFFIX = ".opus"
MEDIA_SUFFIXES = (".mp3", OPUS_SUFFIX, ".wav", ".json")
UPLOAD_SUFFIXES = (".mp3", ".wav", ".flac", OPUS_SUFFIX, ".m4a", ".ogg", ".aac")
UPLOAD_LIMIT = 100 * 1024 * 1024
AUDIO_PREFIX = "/radio/audio/"
MEDIA_PREFIX = "/media/"
WAVEFORM_PREFIX = "/radio/waveform/"
DEVICE_AUDIO_PREFIX = "/radio/device/audio/"
LIBRARY_PREFIX = "/radio/library/"
RESTORE_PREFIX = "/radio/restore/"
STATIC_ROUTES = frozenset(
    {
        "/",
        "/index.html",
        "/style.css",
        "/device-tools.css",
        "/app.js",
        "/imports.js",
        "/preview.js",
        "/visuals.js",
        "/scenes.json",
        "/rig-options.js",
        "/device-link.js",
        "/remote-library.js",
        "/device-tools.js",
        "/device-words.js",
        "/desktop-tools.js",
        "/device-helper.js",
        "/companion.html",
        "/companion.js",
    }
)
REQUEST_ERRORS = (ValueError, OSError)
POST_ERRORS = (ValueError, TypeError, OSError)


def upload_suffix(head, declared):
    """The suffix an upload is stored under, read from its first bytes. The
    browser's filename only says which of the known suffixes to assume when
    the bytes are not recognisable; the header's text never names a file."""
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return ".wav"
    if head[:4] == b"fLaC":
        return ".flac"
    if head[:4] == b"OggS":
        return OPUS_SUFFIX if b"OpusHead" in head[:128] else ".ogg"
    if head[4:8] == b"ftyp":
        return ".m4a"
    if head[:2] in (b"\xff\xf1", b"\xff\xf9"):
        return ".aac"
    if declared not in UPLOAD_SUFFIXES:
        raise ValueError("Choose an MP3, WAV, FLAC, Opus, M4A, OGG, or AAC file.")
    return UPLOAD_SUFFIXES[UPLOAD_SUFFIXES.index(declared)]


def library_file(wanted):
    """The library file whose relative name is `wanted`, or None. The file
    served is one the library directory lists; the request string never
    becomes a path itself."""
    return next(
        (
            p
            for p in LIBRARY.rglob("*")
            if p.is_file()
            and p.suffix in MEDIA_SUFFIXES
            and p.relative_to(LIBRARY).as_posix() == wanted
        ),
        None,
    )


def byte_range(header, size):
    """The inclusive byte interval a Range header asks for; ValueError when
    it is not a satisfiable `bytes=a-b` interval."""
    unit, _, interval = header.partition("=")
    a, dash, b = interval.partition("-")
    if unit != "bytes" or not dash:
        raise ValueError(header)
    start = int(a) if a else max(0, size - int(b))
    end = min(int(b), size - 1) if a and b else size - 1
    if start < 0 or start > end:
        raise ValueError(header)
    return start, end


def song_key(route, prefix):
    """The imported-song key named after `prefix`, or None unless the route
    has that prefix and the key is spelled the way this server spells them."""
    key = route.removeprefix(prefix)
    if (
        not route.startswith(prefix)
        or not key.startswith("radio_")
        or not key.replace("_", "").isalnum()
    ):
        return None
    return key


def media_route(route):
    """Whether `route` names one file directly inside media/ (no subpaths)."""
    name = unquote(route).removeprefix(MEDIA_PREFIX)
    return route.startswith(MEDIA_PREFIX) and Path(name).name == name


def imported_show(body):
    """The cues of the imported song a `file` command plays, from the
    catalog's own row; None when the command is not one of ours."""
    key = str(body.get("key") or "")
    if body.get("action") != "file" or not key:
        return None
    with LOCK:
        row = next((item for item in catalog() if item["key"] == key), None)
    if not row:
        raise ValueError("That imported light show is unavailable.")
    return {"cues": row.get("cues", []), "duration": row.get("duration", 0)}


def new_job(tid, source, title, split, audio_format, audio_quality, source_name):
    return {
        "id": tid,
        "source": source,
        "title": title,
        "split": split,
        "audio_format": audio_format,
        "audio_quality": audio_quality,
        "source_name": source_name,
        "phase": "Queued",
        "done": False,
        "error": None,
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HERE), **kwargs)

    def handle(self):
        # A browser that navigates away mid-stream is not a server error.
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def reply(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def guard(self, action, status, errors=REQUEST_ERRORS):
        """Run a route; a refused or failed request becomes a JSON error."""
        try:
            action()
        except errors as exc:
            self.reply({"error": str(exc)}, status)

    # ---- GET -------------------------------------------------------------

    def media(self, relative):
        path = library_file(unquote(relative))
        if path is None:
            self.send_error(404)
            return
        size = path.stat().st_size
        partial = self.headers.get("Range")
        try:
            start, end = byte_range(partial, size) if partial else (0, size - 1)
        except ValueError:
            self.send_error(416)
            return
        self.send_response(206 if partial else 200)
        self.send_header(
            "Content-Type",
            mimetypes.guess_type(str(path))[0] or "application/octet-stream",
        )
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        self.stream(path, start, end)

    def stream(self, path, start, end):
        with path.open("rb") as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining:
                block = source.read(min(65536, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)

    def get_sync_status(self, parsed):
        key = (parse_qs(parsed.query).get("key") or [""])[0]
        self.guard(lambda: self.reply(remote_library.job(key)), 404)

    def get_device_library(self, _parsed):
        self.guard(
            lambda: self.reply(remote_library.inventory(HERE, LIBRARY, catalog())),
            502,
        )

    def get_device(self, _parsed):
        try:
            self.reply(device_bridge.status())
        except REQUEST_ERRORS as exc:
            self.reply(
                {"connected": False, "host": device_bridge.HOST, "error": str(exc)},
                502,
            )

    def get_device_events(self, _parsed):
        """C1: the castle's event ring, for the "Recent castle events" panel.

        device-tools.js has always had the button and castle-direct.js has
        always answered it on the castle-served build; the desktop control
        room had no route, so `do_GET` fell through to an HTML 404, the
        page's `response.json()` threw, and the catch-all told the user the
        running firmware lacked a feature it has had since 5.59.

        A constant path, like every other castle call here: nothing the
        browser sent reaches the castle's URL.
        """
        self.guard(lambda: self.reply(device_bridge.call("/api/events")), 502)

    def get_device_health(self, _parsed):
        """L7/L9 (v5.62): the season counters, the heap low-water mark and
        the card's last read error, for the row above the events panel.
        Same shape as get_device_events: a constant castle path."""
        self.guard(lambda: self.reply(device_bridge.call("/api/health")), 502)

    def get_library(self, _parsed):
        with LOCK:
            self.reply(desktop_tools.catalog(catalog(), LIBRARY))

    def get_jobs(self, _parsed):
        with LOCK:
            self.reply(list(JOBS.values()))

    def get_waveform(self, route):
        key = unquote(route.rsplit("/", 1)[1])

        def answer():
            with LOCK:
                self.reply(library_ops.waveform(HERE, LIBRARY, key))

        self.guard(answer, 404)

    GET_ROUTES: ClassVar[dict] = {
        "/radio/device/sync-status": get_sync_status,
        "/radio/device/library": get_device_library,
        "/radio/device/events": get_device_events,
        "/radio/device/health": get_device_health,
        "/radio/device": get_device,
        "/radio/library": get_library,
        "/radio/jobs": get_jobs,
        "/radio/tools": desktop_tools.get_status,
    }

    def do_GET(self):
        parsed = urlsplit(self.path)
        route = parsed.path
        handler = self.GET_ROUTES.get(route)
        if handler:
            handler(self, parsed)
        elif route.startswith(WAVEFORM_PREFIX):
            self.get_waveform(route)
        elif route.startswith(AUDIO_PREFIX):
            self.media(route.removeprefix(AUDIO_PREFIX))
        elif route in STATIC_ROUTES or media_route(route):
            super().do_GET()
        else:
            self.send_error(404)

    # ---- DELETE ----------------------------------------------------------

    def delete_device_audio(self, route):
        name = unquote(route.removeprefix(DEVICE_AUDIO_PREFIX))
        self.guard(lambda: self.reply(remote_library.delete_audio(name)), 400)

    def delete_song(self, key):
        with LOCK:
            if key in JOBS and not JOBS[key]["done"]:
                self.reply(
                    {
                        "error": "Wait for preparation to finish before deleting this song."
                    },
                    409,
                )
                return
            result = library_ops.remove(DATA, LIBRARY, CATALOG, key)
            JOBS.pop(key, None)
            self.reply(result)

    def do_DELETE(self):
        route = urlsplit(self.path).path
        key = song_key(route, LIBRARY_PREFIX)
        if route.startswith(DEVICE_AUDIO_PREFIX):
            self.delete_device_audio(route)
        elif key is None:
            self.reply({"error": "Unknown imported song"}, 404)
        else:
            self.guard(lambda: self.delete_song(key), 400)

    # ---- POST ------------------------------------------------------------

    def json_body(self, message, limit=4096):
        length = int(self.headers.get("Content-Length", 0))
        if not 0 < length <= limit:
            raise ValueError(message)
        return json.loads(self.rfile.read(length))

    def upload_length(self):
        """The declared body size, or None after refusing an oversize one."""
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > UPLOAD_LIMIT:
            self.reply({"error": "Choose a file smaller than 100 MB."}, 413)
            return None
        return length

    def playback_choices(self, audio_format, audio_quality):
        cookie = self.headers.get("Cookie")
        return (
            playback_format(audio_format or cookie_audio_format(cookie)),
            playback_quality(audio_quality or cookie_audio_quality(cookie)),
        )

    def queue(self, job):
        POOL.submit(
            prepare,
            job,
            job["source"],
            job["title"],
            job["split"],
            job.get("audio_format", "mp3"),
            job.get("audio_quality", "standard"),
        )
        self.reply(job, 202)

    def post_sync(self):
        body = self.json_body("Invalid sync request")
        self.reply(remote_library.start(HERE, LIBRARY, catalog(), body.get("key")), 202)

    def post_command(self):
        body = self.json_body("Invalid control request size.")
        self.reply(device_bridge.command(body, imported_show(body)))

    def post_restore(self, route):
        key = song_key(route, RESTORE_PREFIX)
        if key is None:
            self.reply({"error": "Unknown removed song"}, 404)
            return

        def answer():
            with LOCK:
                self.reply(library_ops.restore(DATA, CATALOG, key))

        self.guard(answer, 400)

    def post_retry(self):
        payload = self.json_body("Invalid retry request")
        with LOCK:
            job = JOBS.get(payload.get("id"))
            if not job or not job["done"]:
                raise ValueError("That job is not available to retry.")
            update(job, done=False, phase="Queued", error=None, result=None)
        self.queue(job)

    def post_reprocess(self):
        payload = self.json_body("Invalid reprocess request")
        job = reprocess_job(
            str(payload.get("key") or ""),
            payload.get("audio_format") or "mp3",
            payload.get("audio_quality") or "standard",
            payload.get("split"),
        )
        with LOCK:
            if job["id"] in JOBS and not JOBS[job["id"]]["done"]:
                raise ValueError("That song is already being processed.")
            JOBS[job["id"]] = job
        self.queue(job)

    def link_job(self, tid, length):
        payload = json.loads(self.rfile.read(length))
        source = payload.get("url", "").strip()
        parsed = urlsplit(source)
        if parsed.scheme not in ("https", "http") or not parsed.hostname:
            raise ValueError("Paste a complete http or https link.")
        audio_format, audio_quality = self.playback_choices(
            payload.get("audio_format"), payload.get("audio_quality")
        )
        title = str(payload.get("title", "")).strip()[:200]
        split = payload.get("split", True) is True
        return new_job(tid, source, title, split, audio_format, audio_quality, None)

    def upload_job(self, tid, length):
        name = Path(unquote(self.headers.get("X-Filename", "song.mp3"))).name
        body = self.rfile.read(length)
        ext = upload_suffix(body[:128], Path(name).suffix.lower())
        # basename: the name written is one component, never a path.
        source = str(DATA / os.path.basename(tid + ext))
        with open(source, "wb") as upload:
            upload.write(body)
        audio_format, audio_quality = self.playback_choices(
            self.headers.get("X-Audio-Format"), self.headers.get("X-Audio-Quality")
        )
        title = Path(name).stem[:200]
        split = self.headers.get("X-Split", "true") == "true"
        return new_job(tid, source, title, split, audio_format, audio_quality, name)

    def post_import(self):
        length = self.upload_length()
        if length is None:
            return
        tid = "radio_" + uuid4().hex[:12]
        if self.headers.get("Content-Type", "").startswith("application/json"):
            job = self.link_job(tid, length)
        else:
            job = self.upload_job(tid, length)
        with LOCK:
            JOBS[tid] = job
        self.queue(job)

    POST_ROUTES: ClassVar[dict] = {
        "/radio/device/sync": post_sync,
        "/radio/device/command": post_command,
        "/radio/import": post_import,
        "/radio/retry": post_retry,
        "/radio/reprocess": post_reprocess,
    }

    def do_POST(self):
        route = urlsplit(self.path).path
        handler = self.POST_ROUTES.get(route)
        if handler:
            self.guard(lambda: handler(self), 400, POST_ERRORS)
        elif route.startswith(RESTORE_PREFIX):
            self.post_restore(route)
        else:
            self.send_error(404)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8871
    print(
        f"Castle Radio: http://127.0.0.1:{port} — isolated imports, castle device bridge",
        flush=True,
    )
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
