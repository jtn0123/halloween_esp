#!/usr/bin/env python3
"""Local companion server for the cue desk.

The previewer is a single static HTML file, which is exactly what makes it
portable — but a static file cannot run yt-dlp, cannot write to tracks/, and
cannot edit scenes.yaml. This puts a small server behind it so the Tracks
panel can actually do those things on your own machine.

    make studio          -> http://127.0.0.1:8765

Routes — the full table is docs/API.md. In one line: what the studio OWNS
lives under /studio/... ; /api/... is the castle's and relays to it untouched
(castle_link.py), except /api/status, which the studio answers for itself
when no castle is in reach. Old /api/ spellings of the studio routes are
aliases for one release (STUDIO_ROUTES below).

Binds to 127.0.0.1 by default. This drives ffmpeg and yt-dlp on your machine
and edits files in the repo; `--lan` opens it to the local network for the
phone/iPad remote — do that only on a network you control.
"""

from __future__ import annotations

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
import build_paths as bp
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
    source_copies,
    track_files,
    track_info,  # noqa: F401 — re-exported for tests
    track_infos,
    track_path,
)

ROOT = Path(__file__).resolve().parent.parent
# CASTLE_SCENES redirects scene writes the way CASTLE_TRACKS redirects the
# track library — a sandboxed studio (tests, UX sessions on a scratch copy)
# must not be able to edit the real show. Unset means the real file.
SCENES = bp.SCENES
HTML = ROOT / "previewer" / "castle-cue-desk.html"
# The interpreter running this server is the one its children run under —
# whichever venv that is. The hardcoded .venv/bin/python broke the moment
# the studio was launched from anywhere else.
PY = sys.executable

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


#: The option row as the import command wants it; normalize is tri-state
#: (True/False/absent), since an unchecked "match loudness" must reach
#: import_track as --no-normalize — it used to be silently dropped (JB1-6).
OPT_KEYS = ("id", "start", "take", "sensitivity", "bitrate", "sample_rate",
            "channels", "format", "gain_db", "fade_in", "fade_out", "notes")


def opt_args(req: dict, keys: tuple[str, ...] = OPT_KEYS) -> list[str]:
    args: list[str] = []
    for k in keys:
        v = req.get(k)
        if v not in (None, ""):
            args += [f"--{k.replace('_', '-')}", str(v)]
    if req.get("normalize") is True:
        args.append("--normalize")
    elif req.get("normalize") is False:
        args.append("--no-normalize")
    return args


def failed(log: str, **extra) -> dict:
    """A failure body: the log tail for the curious, one reason for the row."""
    return {"ok": False, "log": log, "reason": sj.reason(log), **extra}


def safe_id(raw: str) -> str | None:
    """A track id as the importer would mint it — or None.

    The browser's id lands in `--id`/`--refresh` and then in a filesystem
    path, so this is the write-side twin of the `Path(...).name` guard the
    serving routes use: "../../audio/01_vigil" must die HERE, not in
    import_track's error output."""
    tid = (raw or "").strip()
    return tid if tid and all(c.isalnum() or c == "_" for c in tid) else None


#: The studio's own route families. "/api/<x>" for any of these is the OLD
#: spelling (v5.23 and earlier), rewritten to "/studio/<x>" for one release
#: so a desk built before the move keeps working; every other /api/* path
#: is the castle's and relays untouched (/api/scene?s=<id> included).
STUDIO_ROUTES = frozenset((
    "tracks", "import", "job", "refresh", "track", "waveform", "stems",
    "stem", "compare", "probe", "server", "scene", "rebuild", "card"))
_deprecated_seen: set[str] = set()


def studio_path(raw: str) -> str:
    """The request's path (no query), an old /api/ spelling of a studio
    route rewritten to its /studio/ home — logged once per route."""
    url = urllib.parse.urlparse(raw)
    path = url.path
    head = path[5:].split("/", 1)[0] if path.startswith("/api/") else ""
    if head not in STUDIO_ROUTES or (
            head == "scene" and urllib.parse.parse_qs(url.query).get("s")):
        return path
    if head not in _deprecated_seen:
        _deprecated_seen.add(head)
        sys.stderr.write(f"  DEPRECATED: /api/{head} is now /studio/{head} "
                         "(docs/API.md) — the alias goes away next release\n")
    return "/studio/" + path[5:]


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
        path = studio_path(self.path)
        if path in ("/", "/index.html"):
            # A sandboxed studio serves the page ITS rebuilds wrote (so
            # "Reload the desk" shows the sandbox's scenes), the repo's
            # build until it has one.
            page = bp.PREVIEW_HTML if bp.sandboxed() and bp.PREVIEW_HTML.exists() else HTML
            if not page.exists():
                return self.send_json({"error": "previewer not built"}, 404)
            return self.send_file(page, "text/html; charset=utf-8")
        if path == "/remote":
            # The castle's own phone remote (firmware/sd_web_remote.h) — four
            # thumb buttons that live in flash. Relayed so the address on
            # the desk's link works from any phone on the LAN (JB1-8).
            return self.relay("GET")
        if path.startswith("/studio/job/"):
            job = _runner.get(Path(path).name)
            if job is None:
                return self.send_json({"error": "no such job"}, 404)
            d = job.as_dict()
            if d["done"]:
                TRACKS.mkdir(exist_ok=True)
                d["tracks"] = track_infos(track_files())
            return self.send_json(d)
        if path == "/api/status":
            # The desk probes this to decide simulator-vs-device mode. When
            # the castle answers, relay ITS status — the desk then mirrors
            # scenes to the hardware while audio stays on this machine
            # (castle_link.py). Only with no castle in reach does the studio
            # answer for itself, marked so device.ts knows it is NOT one.
            live = cl.status()
            return self.send_json(live or {"studio": True})
        if path == "/studio/tracks":
            TRACKS.mkdir(exist_ok=True)
            return self.send_json({
                "tracks": track_infos(track_files()),
                "scenes": [s for s in scene_ids()],
            })
        if path.startswith("/studio/waveform/"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            sens = parse_sensitivity(q)
            p = track_path(Path(path).name)    # name-stripped: no traversal
            if p is None:
                return self.send_json({"error": "no such track"}, 404)
            return self.send_json(sm.waveform(p, sensitivity=sens))
        if path.startswith("/studio/stems/"):
            # Cached nine-way analysis (layer x channel), written by the
            # split job — never derived inside a GET, which would stall the
            # panel for the length of nine STFTs.
            out = st.analysis(Path(path).name)
            return self.send_json(out, 200 if out.get("ok") else 404)
        if path.startswith("/studio/stem/"):
            # /studio/stem/<tid>/<layer> — the stem mp3s; `combined` has no file
            # here because the original track already streams via /studio/track.
            parts = path.split("/")
            p = st.stem_file(parts[-2], parts[-1]) if len(parts) >= 5 else None
            if p is None:
                return self.send_json({"error": "no such stem"}, 404)
            return self.send_range(p, "audio/mpeg")
        if path.startswith("/studio/compare/"):
            # /studio/compare/<token>/<codec>
            parts = path.split("/")
            p = sm.compare_file(parts[-2], parts[-1]) if len(parts) >= 5 else None
            if p is None:
                return self.send_json({"error": "no such comparison"}, 404)
            return self.send_range(p, MIME.get(p.suffix.lstrip("."),
                                               "application/octet-stream"))
        if path.startswith("/studio/track/"):
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
        if path.startswith("/studio/card/"):
            # Pull leg: the castle serves card bytes at /sd/<name>. Name-
            # stripped like every other file route — the raw suffix used to
            # go through, and "../api/status" reached any GET on the castle.
            name = Path(path[len("/studio/card/"):]).name
            if not name:
                return self.send_json({"error": "no file name"}, 400)
            return self.relay("GET", to="/sd/" + name)
        if path.startswith("/api/"):
            return self.relay("GET")
        self.send_json({"error": "not found"}, 404)

    def _delete(self):
        path = studio_path(self.path)
        if path.startswith("/studio/tracks/"):
            tid = Path(path).name
            p = track_path(tid)            # name-stripped above
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            # ?scene=1 with the file already gone: the scene is an orphan
            # and taking it out is the whole point (judge B, JB2-5a).
            if p is None and not q.get("scene"):
                return self.send_json({"error": "not found"}, 404)
            if p is not None:
                p.unlink()
            for kept in source_copies(tid):
                kept.unlink()
            mf.forget(tid)
            body: dict = {"ok": True, "removed": tid, "file_missing": p is None}
            if q.get("scene"):
                # The track was IN THE SHOW and the operator chose to take
                # its scene out with it, rather than leave scenes.yaml
                # pointing at a file that is gone (JB1-6).
                res, _code = ss.remove(SCENES, tid, _lock, run, PY, ROOT)
                body.update(scene_removed=res.get("removed", False),
                            scenes=res.get("scenes", []), log=res.get("log", ""))
                if not res.get("ok"):
                    body.update(failed(res.get("log", "")))
            return self.send_json(body, 200 if body["ok"] else 500)
        if path.startswith("/api/"):
            return self.relay("DELETE")
        self.send_json({"error": "not found"}, 404)

    def _post(self):
        path = studio_path(self.path)
        raw = self.body()
        if path == "/studio/import":
            return self.do_import(raw)
        if path == "/studio/import/async":
            req = self.json_body(raw)
            src = (req.get("url") or "").strip()
            if not src.startswith(("http://", "https://")):
                return self.send_json({"error": "url must be http(s)"}, 400)
            if req.get("id") and safe_id(req["id"]) is None:
                return self.send_json(
                    {"error": "id: letters, digits and _ only"}, 400)
            args = [PY, str(ROOT / "tools" / "import_track.py"), src]
            args += opt_args(req)
            return self.send_json(_runner.start(args).as_dict())
        if path == "/studio/stems":
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
        if path == "/studio/refresh":
            # Rebuild a track from its remembered source, with any option
            # overridden. This is why the manifest exists.
            req = self.json_body(raw)
            tid = safe_id(req.get("id") or "")
            if tid is None:
                return self.send_json({"error": "no id"}, 400)
            args = [PY, str(ROOT / "tools" / "import_track.py"), "--refresh", tid]
            args += opt_args(req, OPT_KEYS[1:-1])      # no id, no notes
            with _lock:
                ok, out = run(args)
            tracks = track_infos(track_files())
            return self.send_json({"ok": True, "log": out, "tracks": tracks}
                                  if ok else failed(out, tracks=tracks),
                                  200 if ok else 500)
        if path == "/studio/compare":
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
        if path == "/studio/probe":
            req = self.json_body(raw)
            out = sm.probe((req.get("url") or "").strip())
            # A bad or unreadable link is the caller's problem: 400, not 200.
            return self.send_json(out, 200 if out.get("ok") else 400)
        if path == "/studio/server/stop":
            # Answer first, then shut down — otherwise the page sees the
            # socket die and reports a network error instead of "stopped".
            self.send_json({"ok": True, "stopping": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if path == "/studio/server/restart":
            self.send_json({"ok": True, "restarting": True})
            threading.Thread(target=_restart, daemon=True).start()
            return
        if path == "/studio/scene":
            # The studio's scenes.yaml editor (JSON body). /api/scene?s=<id>
            # is the castle's fire-a-scene and never lands here —
            # studio_path keeps it on the relay.
            return self.do_scene(self.json_body(raw))
        if path == "/studio/rebuild":
            ok, log = ss.rebuild(_lock, run, PY, ROOT)
            return self.send_json({"ok": ok, "log": log}, 200 if ok else 500)
        if path.startswith("/api/"):
            return self.relay("POST", raw)
        self.send_json({"error": "not found"}, 404)

    def _put(self) -> None:
        # The desk's "→ Castle" button: PUT /api/files/<name> with the track
        # bytes. The studio owns no PUT routes of its own, so everything
        # castle-shaped relays; castle_link enforces the reachability story.
        path = studio_path(self.path)
        if not path.startswith("/api/"):
            return self.send_json({"error": "not found"}, 404)
        return self.relay("PUT", self.body())

    def relay(self, method: str, body: bytes = b"",
              to: str | None = None) -> None:
        """Hand a castle-shaped /api/* request to the castle, answer as it did."""
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
            # Through json_body: malformed options are the client's mistake
            # (400), not a traceback and a dead socket. Before the staging
            # write, so a rejected upload leaves nothing behind.
            req = self.json_body(
                (self.headers.get("X-Import-Opts") or "{}").encode())
            tmp = TRACKS / "_upload"
            tmp.mkdir(exist_ok=True)
            src = tmp / (fname or "upload.bin")
            src.write_bytes(data)
            # The staging copy is gone the moment this returns, so the
            # importer keeps the original beside the library (tracks/_src/)
            # and remembers THAT as the source — or Re-import could never
            # work for a dropped or card-pulled file (JB1-3).
            args += [str(src), "--keep-source"]

        if req.get("id") and safe_id(str(req["id"])) is None:
            return self.send_json({"error": "id: letters, digits and _ only"}, 400)
        args += opt_args(req)

        with _lock:
            ok, out = run(args)
        shutil.rmtree(TRACKS / "_upload", ignore_errors=True)
        tracks = track_infos(track_files())
        return self.send_json({"ok": True, "log": out, "tracks": tracks}
                              if ok else failed(out, tracks=tracks),
                              200 if ok else 500)

    def do_scene(self, req: dict):
        """Insert or replace a scene in scenes.yaml — studio_scenes.splice."""
        body, code = ss.splice(SCENES, req, _lock, run, PY, ROOT)
        if not body.get("ok") and body.get("log"):
            body["reason"] = sj.reason(body["log"])
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
