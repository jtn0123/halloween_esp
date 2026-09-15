"""HTTP routes for the local Castle Radio demo."""

import json
import mimetypes
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4

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

MEDIA_SUFFIXES = (".mp3", ".opus", ".wav", ".json")
# The suffix written to disk is this table's value, never the header's text.
UPLOAD_SUFFIXES = {
    s: s for s in (".mp3", ".wav", ".flac", ".opus", ".m4a", ".ogg", ".aac")
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

    def media(self, relative):
        # The file served is one the library directory lists, matched by its
        # relative name; the request string never becomes a path itself.
        wanted = unquote(relative)
        path = next(
            (
                p
                for p in LIBRARY.rglob("*")
                if p.is_file()
                and p.suffix in MEDIA_SUFFIXES
                and p.relative_to(LIBRARY).as_posix() == wanted
            ),
            None,
        )
        if path is None:
            self.send_error(404)
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        partial = self.headers.get("Range")
        if partial:
            try:
                unit, interval = partial.split("=")
                a, b = interval.split("-")
                if unit != "bytes":
                    raise ValueError()
                start = int(a) if a else max(0, size - int(b))
                end = min(int(b), size - 1) if a and b else size - 1
                if start < 0 or start > end:
                    raise ValueError()
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
        with path.open("rb") as stream:
            stream.seek(start)
            remaining = end - start + 1
            while remaining:
                block = stream.read(min(65536, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)

    def do_GET(self):
        parsed = urlsplit(self.path)
        route = parsed.path
        if route == "/radio/device/sync-status":
            try:
                key = unquote(parsed.query.removeprefix("key="))
                self.reply(remote_library.job(key))
            except (ValueError, OSError) as exc:
                self.reply({"error": str(exc)}, 404)
            return
        if route == "/radio/device/library":
            try:
                self.reply(remote_library.inventory(HERE, LIBRARY, catalog()))
            except (ValueError, OSError) as exc:
                self.reply({"error": str(exc)}, 502)
        elif route == "/radio/device":
            try:
                self.reply(device_bridge.status())
            except (ValueError, OSError) as exc:
                self.reply(
                    {"connected": False, "host": device_bridge.HOST, "error": str(exc)},
                    502,
                )
        elif route.startswith("/radio/waveform/"):
            try:
                with LOCK:
                    self.reply(
                        library_ops.waveform(
                            HERE, LIBRARY, unquote(route.rsplit("/", 1)[1])
                        )
                    )
            except (ValueError, OSError) as exc:
                self.reply({"error": str(exc)}, 404)
        elif route == "/radio/library":
            with LOCK:
                self.reply(catalog())
        elif route == "/radio/jobs":
            with LOCK:
                self.reply(list(JOBS.values()))
        elif route.startswith("/radio/audio/"):
            self.media(route.removeprefix("/radio/audio/"))
        elif route.startswith("/radio/"):
            self.send_error(404)
        elif route in (
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
        ) or route.startswith("/media/"):
            if (
                route.startswith("/media/")
                and Path(unquote(route)).name != unquote(route)[7:]
            ):
                self.send_error(404)
            else:
                super().do_GET()
        else:
            self.send_error(404)

    def do_DELETE(self):
        route = urlsplit(self.path).path
        if route.startswith("/radio/device/audio/"):
            try:
                name = unquote(route.removeprefix("/radio/device/audio/"))
                self.reply(remote_library.delete_audio(name))
            except (ValueError, OSError) as exc:
                self.reply({"error": str(exc)}, 400)
            return
        key = route.removeprefix("/radio/library/")
        if (
            not route.startswith("/radio/library/")
            or not key.startswith("radio_")
            or not key.replace("_", "").isalnum()
        ):
            self.reply({"error": "Unknown imported song"}, 404)
            return
        try:
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
        except (ValueError, OSError) as exc:
            self.reply({"error": str(exc)}, 400)

    def do_POST(self):
        route = urlsplit(self.path).path
        if route == "/radio/device/sync":
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 4096:
                    raise ValueError("Invalid sync request")
                body = json.loads(self.rfile.read(length))
                self.reply(
                    remote_library.start(HERE, LIBRARY, catalog(), body.get("key")), 202
                )
            except (ValueError, TypeError, OSError) as exc:
                self.reply({"error": str(exc)}, 400)
            return
        if route == "/radio/device/command":
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 4096:
                    raise ValueError("Invalid control request size.")
                body = json.loads(self.rfile.read(length))
                imported_show = None
                key = str(body.get("key") or "")
                if body.get("action") == "file" and key:
                    row = next((item for item in catalog() if item["key"] == key), None)
                    if not row:
                        raise ValueError("That imported light show is unavailable.")
                    imported_show = {
                        "cues": row.get("cues", []),
                        "duration": row.get("duration", 0),
                    }
                self.reply(device_bridge.command(body, imported_show))
            except (ValueError, TypeError, OSError) as exc:
                self.reply({"error": str(exc)}, 400)
            return
        if route.startswith("/radio/restore/"):
            key = route.rsplit("/", 1)[1]
            if not key.startswith("radio_") or not key.replace("_", "").isalnum():
                self.reply({"error": "Unknown removed song"}, 404)
                return
            try:
                with LOCK:
                    self.reply(library_ops.restore(DATA, CATALOG, key))
            except (ValueError, OSError) as exc:
                self.reply({"error": str(exc)}, 400)
            return
        if route not in ("/radio/import", "/radio/retry", "/radio/reprocess"):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 100 * 1024 * 1024:
                self.reply({"error": "Choose a file smaller than 100 MB."}, 413)
                return
            if route == "/radio/retry":
                payload = json.loads(self.rfile.read(length))
                with LOCK:
                    job = JOBS.get(payload.get("id"))
                    if not job or not job["done"]:
                        raise ValueError("That job is not available to retry.")
                    update(job, done=False, phase="Queued", error=None, result=None)
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
                return
            if route == "/radio/reprocess":
                payload = json.loads(self.rfile.read(length))
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
                POOL.submit(
                    prepare,
                    job,
                    job["source"],
                    job["title"],
                    job["split"],
                    job["audio_format"],
                    job["audio_quality"],
                )
                self.reply(job, 202)
                return
            tid = "radio_" + uuid4().hex[:12]
            source_name = None
            if self.headers.get("Content-Type", "").startswith("application/json"):
                payload = json.loads(self.rfile.read(length))
                source = payload.get("url", "").strip()
                parsed = urlsplit(source)
                if parsed.scheme not in ("https", "http") or not parsed.hostname:
                    raise ValueError("Paste a complete http or https link.")
                title = str(payload.get("title", "")).strip()[:200]
                split = payload.get("split", True) is True
                audio_format = playback_format(
                    payload.get("audio_format")
                    or cookie_audio_format(self.headers.get("Cookie"))
                )
                audio_quality = playback_quality(
                    payload.get("audio_quality")
                    or cookie_audio_quality(self.headers.get("Cookie"))
                )
            else:
                name = Path(unquote(self.headers.get("X-Filename", "song.mp3"))).name
                source_name = name
                ext = UPLOAD_SUFFIXES.get(Path(name).suffix.lower())
                if ext is None:
                    raise ValueError(
                        "Choose an MP3, WAV, FLAC, Opus, M4A, OGG, or AAC file."
                    )
                source = str(DATA / (tid + ext))
                Path(source).write_bytes(self.rfile.read(length))
                title = Path(name).stem[:200]
                split = self.headers.get("X-Split", "true") == "true"
                audio_format = playback_format(
                    self.headers.get("X-Audio-Format")
                    or cookie_audio_format(self.headers.get("Cookie"))
                )
                audio_quality = playback_quality(
                    self.headers.get("X-Audio-Quality")
                    or cookie_audio_quality(self.headers.get("Cookie"))
                )
            job = dict(
                id=tid,
                source=source,
                title=title,
                split=split,
                audio_format=audio_format,
                audio_quality=audio_quality,
                source_name=source_name,
                phase="Queued",
                done=False,
                error=None,
            )
            with LOCK:
                JOBS[tid] = job
            POOL.submit(prepare, job, source, title, split, audio_format, audio_quality)
            self.reply(job, 202)
        except (ValueError, TypeError, OSError) as exc:
            self.reply({"error": str(exc)}, 400)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8871
    print(
        f"Castle Radio: http://127.0.0.1:{port} — isolated imports, castle device bridge",
        flush=True,
    )
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
