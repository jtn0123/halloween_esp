#!/usr/bin/env python3
"""Local companion server for the cue desk.

The previewer is a single static HTML file, which is exactly what makes it
portable — but a static file cannot run yt-dlp, cannot write to tracks/, and
cannot edit scenes.yaml. This puts a small server behind it so the Tracks
panel can actually do those things on your own machine.

    make studio          -> http://127.0.0.1:8765

Endpoints (all JSON, all local-only):

    GET    /api/tracks              list imported tracks + their onsets
    POST   /api/import              {url} or multipart file  -> import
    DELETE /api/tracks/<id>         remove a track
    POST   /api/scene               add/replace a scene in scenes.yaml
    POST   /api/rebuild             re-run audio + generators
    GET    /api/track/<id>          stream a track to audition (ext optional)
    GET    /api/card/<name>         pull a file off the castle's SD card

Binds to 127.0.0.1 by default. This drives ffmpeg and yt-dlp on your machine
and edits files in the repo; `--lan` opens it to the local network for the
phone/iPad remote — do that only on a network you control.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import castle_link as cl
import manifest as mf
import stems as st
import studio_http as sh
import studio_jobs as sj
import studio_media as sm
import studio_scenes as ss

# Re-exported: TRACKS and these helpers are the track vocabulary, and
# callers (including the tests) reach for them through this module.
from studio_tracks import (
    AUDIO_EXT,
    MIME,
    TRACKS,
    parse_sensitivity,
    track_files,
    track_info,
    track_path,
)

ROOT = Path(__file__).resolve().parent.parent
# CASTLE_SCENES redirects scene writes the way CASTLE_TRACKS redirects the
# track library — a sandboxed studio (tests, UX sessions on a scratch copy)
# must not be able to edit the real show. Unset means the real file.
SCENES = Path(os.environ.get("CASTLE_SCENES") or (ROOT / "scenes" / "scenes.yaml"))
HTML = ROOT / "previewer" / "castle-cue-desk.html"
PY = str(ROOT / ".venv" / "bin" / "python")

_lock = threading.Lock()          # ffmpeg/yt-dlp jobs are serialised
_runner = sj.JobRunner()          # long imports run in the background


def _restart() -> None:
    """Replace this process with a fresh copy of itself.

    os.execv keeps the same PID, so whatever launched us — a double-clicked
    launcher, or launchd — neither notices nor needs to re-parent anything.
    """
    time.sleep(0.4)               # let the HTTP response actually go out
    os.execv(sys.executable, [sys.executable, *sys.argv])


def run(cmd: list[str], timeout: int = 900) -> tuple[bool, str]:
    # The ceiling matters more than its exact value: these run holding
    # _lock, so one hung ffmpeg/yt-dlp used to wedge every later import
    # and rebuild for the life of the process.
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, check=False,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"gave up after {timeout}s — the job stalled"
    return p.returncode == 0, (p.stdout + p.stderr)[-4000:]


def safe_id(raw: str) -> str | None:
    """A track id as the importer would mint it — or None.

    The browser's id lands in `--id`/`--refresh` and then in a filesystem
    path, so this is the write-side twin of the `Path(...).name` guard the
    serving routes use: "../../audio/01_vigil" must die HERE, not in
    import_track's error output."""
    tid = (raw or "").strip()
    return tid if tid and all(c.isalnum() or c == "_" for c in tid) else None


class Handler(sh.JsonHandler):
    # ── routes ──
    def do_GET(self):
        self._guarded(self._get)

    def do_POST(self):
        self._guarded(self._post)

    def do_DELETE(self):
        self._guarded(self._delete)

    def do_PUT(self):
        self._guarded(self._put)

    def _get(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            if not HTML.exists():
                return self.send_json({"error": "previewer not built"}, 404)
            return self.send_bytes(HTML.read_bytes(), "text/html; charset=utf-8")
        if path.startswith("/api/job/"):
            job = _runner.get(Path(path).name)
            if job is None:
                return self.send_json({"error": "no such job"}, 404)
            d = job.as_dict()
            if d["done"]:
                TRACKS.mkdir(exist_ok=True)
                d["tracks"] = [track_info(p) for p in track_files()]
            return self.send_json(d)
        if path == "/api/status":
            # The desk probes this to decide simulator-vs-device mode. When
            # the castle answers, relay ITS status — the desk then mirrors
            # scenes to the hardware while audio stays on this machine
            # (castle_link.py). Only with no castle in reach does the studio
            # answer for itself, marked so device.ts knows it is NOT one.
            live = cl.status()
            return self.send_json(live or {"studio": True})
        if path == "/api/tracks":
            TRACKS.mkdir(exist_ok=True)
            return self.send_json({
                "tracks": [track_info(p) for p in track_files()],
                "scenes": [s for s in scene_ids()],
            })
        if path.startswith("/api/waveform/"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            sens = parse_sensitivity(q)
            p = track_path(Path(path).name)    # name-stripped: no traversal
            if p is None:
                return self.send_json({"error": "no such track"}, 404)
            return self.send_json(sm.waveform(p, sensitivity=sens))
        if path.startswith("/api/stems/"):
            # Cached nine-way analysis (layer x channel), written by the
            # split job — never derived inside a GET, which would stall the
            # panel for the length of nine STFTs.
            out = st.analysis(Path(path).name)
            return self.send_json(out, 200 if out.get("ok") else 404)
        if path.startswith("/api/stem/"):
            # /api/stem/<tid>/<layer> — the stem mp3s; `combined` has no file
            # here because the original track already streams via /api/track.
            parts = path.split("/")
            p = st.stem_file(parts[-2], parts[-1]) if len(parts) >= 5 else None
            if p is None:
                return self.send_json({"error": "no such stem"}, 404)
            return self.send_range(p, "audio/mpeg")
        if path.startswith("/api/compare/"):
            # /api/compare/<token>/<codec>
            parts = path.split("/")
            p = sm.compare_file(parts[-2], parts[-1]) if len(parts) >= 5 else None
            if p is None:
                return self.send_json({"error": "no such comparison"}, 404)
            return self.send_range(p, MIME.get(p.suffix.lstrip("."),
                                               "application/octet-stream"))
        if path.startswith("/api/track/"):
            # Path(...).name strips any directory part, so a traversal
            # like ../../etc/passwd cannot escape TRACKS. That call IS
            # the guard — a `p.parent == TRACKS` check here would be
            # tautological and read as protection it is not providing.
            name = Path(path).name
            stem, _, ext = name.rpartition(".")
            p = track_path(stem if stem and ext in AUDIO_EXT else name)
            if p is None:
                return self.send_json({"error": "not found"}, 404)
            return self.send_range(p, MIME[p.suffix.lstrip(".")])
        if path.startswith("/api/card/"):
            # Pull leg: the castle serves card bytes at /sd/<name>.
            return self.relay("GET", to="/sd/" + path[len("/api/card/"):])
        if path.startswith("/api/"):
            return self.relay("GET")
        self.send_json({"error": "not found"}, 404)

    def _delete(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/tracks/"):
            tid = Path(path).name
            p = track_path(tid)            # name-stripped above
            if p is not None:
                p.unlink()
                mf.forget(tid)
                return self.send_json({"ok": True, "removed": tid})
            return self.send_json({"error": "not found"}, 404)
        if path.startswith("/api/"):
            return self.relay("DELETE")
        self.send_json({"error": "not found"}, 404)

    def _post(self):
        path = urllib.parse.urlparse(self.path).path
        raw = self.body()
        if path == "/api/import":
            return self.do_import(raw)
        if path == "/api/import/async":
            req = self.json_body(raw)
            src = (req.get("url") or "").strip()
            if not src.startswith(("http://", "https://")):
                return self.send_json({"error": "url must be http(s)"}, 400)
            if req.get("id") and safe_id(req["id"]) is None:
                return self.send_json(
                    {"error": "id: letters, digits and _ only"}, 400)
            args = [PY, str(ROOT / "tools" / "import_track.py"), src]
            for k in ("id", "start", "take", "sensitivity", "bitrate",
                      "sample_rate", "channels", "format", "gain_db",
                      "fade_in", "fade_out", "notes"):
                v = req.get(k)
                if v not in (None, ""):
                    args += [f"--{k.replace('_', '-')}", str(v)]
            if req.get("normalize"):
                args.append("--normalize")
            return self.send_json(_runner.start(args).as_dict())
        if path == "/api/stems":
            # Demucs split as a background job — ~25 s on the GPU is far too
            # long to hold an HTTP request open, and the JobRunner already
            # knows how to babysit a child process.
            req = self.json_body(raw)
            tid = safe_id(req.get("id") or "")
            if tid is None or track_path(tid) is None:
                return self.send_json({"error": "no such track"}, 400)
            args = [PY, str(ROOT / "tools" / "stems.py"), tid]
            if req.get("force"):
                args.append("--force")
            return self.send_json(_runner.start(args).as_dict())
        if path == "/api/refresh":
            # Rebuild a track from its remembered source, with any option
            # overridden. This is why the manifest exists.
            req = self.json_body(raw)
            tid = safe_id(req.get("id") or "")
            if tid is None:
                return self.send_json({"error": "no id"}, 400)
            args = [PY, str(ROOT / "tools" / "import_track.py"), "--refresh", tid]
            for k in ("start", "take", "sensitivity", "bitrate",
                      "sample_rate", "channels", "format", "gain_db",
                      "fade_in", "fade_out"):
                v = req.get(k)
                if v not in (None, ""):
                    args += [f"--{k.replace('_', '-')}", str(v)]
            if req.get("normalize"):
                args.append("--normalize")
            with _lock:
                ok, out = run(args)
            return self.send_json({"ok": ok, "log": out,
                                   "tracks": [track_info(p)
                                              for p in track_files()]},
                                  200 if ok else 500)
        if path == "/api/compare":
            req = self.json_body(raw)
            # .name-strip like every other track route — without it this was
            # an arbitrary-read: "../../x" resolved, encoded, and streamed.
            p = track_path(Path((req.get("id") or "").strip()).name)
            if p is None:
                return self.send_json({"ok": False, "error": "no such track"}, 404)
            num = lambda k, d: float(req.get(k) or d)      # noqa: E731
            opts = {
                "start": num("start", 0), "take": (float(req["take"])
                                                   if req.get("take") else None),
                "fade_in": None, "fade_out": None, "normalize": False,
                "gain_db": None, "bitrate": int(num("bitrate", 96)),
                "channels": int(num("channels", 1)),
                "sample_rate": int(num("sample_rate", 44100)),
            }
            # ffmpeg four times over; serialise with every other encode job.
            with _lock:
                out = sm.compare(p, opts, token=f"{p.stem}-{int(time.time())}")
            return self.send_json(out, 200 if out.get("ok") else 500)
        if path == "/api/probe":
            req = self.json_body(raw)
            out = sm.probe((req.get("url") or "").strip())
            # A bad or unreadable link is the caller's problem: 400, not 200.
            return self.send_json(out, 200 if out.get("ok") else 400)
        if path == "/api/server/stop":
            # Answer first, then shut down — otherwise the page sees the
            # socket die and reports a network error instead of "stopped".
            self.send_json({"ok": True, "stopping": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if path == "/api/server/restart":
            self.send_json({"ok": True, "restarting": True})
            threading.Thread(target=_restart, daemon=True).start()
            return
        if path == "/api/scene":
            # ?s=<id> is the castle's fire-a-scene; a JSON body is the
            # studio's own scenes.yaml editor. Same path, different verb.
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("s"):
                return self.relay("POST", raw)
            return self.do_scene(self.json_body(raw))
        if path == "/api/rebuild":
            ok, log = ss.rebuild(_lock, run, PY, ROOT)
            return self.send_json({"ok": ok, "log": log}, 200 if ok else 500)
        if path.startswith("/api/"):
            return self.relay("POST", raw)
        self.send_json({"error": "not found"}, 404)

    def _put(self) -> None:
        # The desk's "→ Castle" button: PUT /api/files/<name> with the track
        # bytes. The studio owns no PUT routes of its own, so everything
        # castle-shaped relays; castle_link enforces the reachability story.
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/api/"):
            return self.send_json({"error": "not found"}, 404)
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        return self.relay("PUT", body)

    def relay(self, method: str, body: bytes = b"",
              to: str | None = None) -> None:
        """Hand an unclaimed /api/* request to the castle, answer as it did."""
        code, out, ctype = cl.forward(method, to or self.path, body)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_import(self, raw: bytes):
        ctype = self.headers.get("Content-Type", "")
        TRACKS.mkdir(exist_ok=True)
        args = [PY, str(ROOT / "tools" / "import_track.py")]

        if ctype.startswith("application/json"):
            req = self.json_body(raw)
            src = (req.get("url") or "").strip()
            if not src:
                return self.send_json({"error": "no url"}, 400)
            if not src.startswith(("http://", "https://")):
                return self.send_json({"error": "url must be http(s)"}, 400)
            args.append(src)
        else:
            # multipart upload: pull out the single file part
            fname, data = sh.parse_multipart(raw, ctype)
            if not data:
                return self.send_json({"error": "no file in upload"}, 400)
            tmp = TRACKS / "_upload"
            tmp.mkdir(exist_ok=True)
            src = tmp / (fname or "upload.bin")
            src.write_bytes(data)
            args.append(str(src))
            req = json.loads(self.headers.get("X-Import-Opts") or "{}")

        if req.get("id") and safe_id(str(req["id"])) is None:
            return self.send_json({"error": "id: letters, digits and _ only"}, 400)
        for k in ("id", "start", "take", "sensitivity", "bitrate",
                  "sample_rate", "channels", "format", "gain_db",
                      "fade_in", "fade_out", "notes"):
            v = req.get(k)
            if v not in (None, ""):
                args += [f"--{k.replace('_', '-')}", str(v)]
        if req.get("normalize"):
            args.append("--normalize")

        with _lock:
            ok, out = run(args)
        shutil.rmtree(TRACKS / "_upload", ignore_errors=True)
        return self.send_json({"ok": ok, "log": out,
                               "tracks": [track_info(p)
                                          for p in track_files()]},
                              200 if ok else 500)

    def do_scene(self, req: dict):
        """Insert or replace a scene in scenes.yaml — studio_scenes.splice."""
        body, code = ss.splice(SCENES, req, _lock, run, PY, ROOT)
        return self.send_json(body, code)


def scene_ids() -> list[str]:
    return ss.scene_ids(SCENES)


def lan_ip() -> str:
    """Best guess at this machine's address on the LAN, for the banner."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))   # no packets sent; just picks a route
        return str(s.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    # This Mac only, unless asked: the server has no auth and it drives
    # ffmpeg/yt-dlp and edits files in the repo. --lan opts into the
    # phone/iPad use case deliberately, on a network you control.
    # (--localhost is accepted as a no-op for old launchers and Playwright.)
    host = "0.0.0.0" if "--lan" in sys.argv else "127.0.0.1"
    TRACKS.mkdir(exist_ok=True)
    # Warm the castle bridge before the first page load: the flash build's
    # native-API leg needs a second to connect, and the desk only probes
    # /api/status once.
    cl.status()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"cue desk studio  ->  http://127.0.0.1:{port}")
    if host == "0.0.0.0":
        print(f"  from your phone  ->  http://{lan_ip()}:{port}")
        print("  (OPEN TO YOUR LAN — anyone on the WiFi can edit the show)")
    else:
        print("  (this Mac only — pass --lan to reach it from your phone)")
    print("  serving the previewer with track management enabled")
    print("  ctrl-c to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
