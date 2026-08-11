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
    GET    /api/track/<id>.mp3      stream a track for the browser to audition

Binds to 127.0.0.1 only. This drives ffmpeg and yt-dlp on your machine and
edits files in the repo; it is not something to expose to a network.
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import analyze as ana  # noqa: E402
import manifest as mf  # noqa: E402
import studio_media as sm  # noqa: E402
import studio_jobs as sj  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TRACKS = ROOT / "tracks"
SCENES = ROOT / "scenes" / "scenes.yaml"
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


def run(cmd: list[str]) -> tuple[bool, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode == 0, (p.stdout + p.stderr)[-4000:]


def track_info(p: Path) -> dict:
    """Everything the Tracks panel needs, including where the file came from.

    The manifest is the cheap part — read it even if decoding fails, so a
    broken file still shows its source and can be re-imported.
    """
    meta = mf.get(p.stem) or {}
    info = {
        "id": p.stem,
        "kb": p.stat().st_size // 1024,
        "source": meta.get("source", ""),
        "title": meta.get("title", ""),
        "imported": meta.get("imported", ""),
        "opts": meta.get("opts", {}),
        "notes": meta.get("notes", ""),
    }
    try:
        x = ana.load_audio(p)
        marks = ana.analyze(x)
    except Exception as e:                       # noqa: BLE001
        info["error"] = str(e)
        return info
    info["dur"] = round(len(x) / ana.SR, 2)
    info["onsets"] = {k: len(v) for k, v in marks.items()}
    return info


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):              # quieter console
        sys.stderr.write("  %s\n" % (fmt % a))

    # ── helpers ──
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

    def body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))

    # ── routes ──
    def do_GET(self):
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
                d["tracks"] = [track_info(p) for p in sorted(TRACKS.glob("*.mp3"))]
            return self.send_json(d)
        if path == "/api/tracks":
            TRACKS.mkdir(exist_ok=True)
            return self.send_json({
                "tracks": [track_info(p) for p in sorted(TRACKS.glob("*.mp3"))],
                "scenes": [s for s in scene_ids()],
            })
        if path.startswith("/api/waveform/"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            sens = float((q.get("sensitivity") or ["1.1"])[0])
            p = TRACKS / f"{Path(path).name}.mp3"
            if not (p.exists() and p.parent == TRACKS):
                return self.send_json({"error": "no such track"}, 404)
            return self.send_json(sm.waveform(p, sensitivity=sens))
        if path.startswith("/api/track/"):
            p = TRACKS / Path(path).name
            if p.suffix == ".mp3" and p.exists() and p.parent == TRACKS:
                return self.send_bytes(p.read_bytes(), "audio/mpeg")
            return self.send_json({"error": "not found"}, 404)
        self.send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/tracks/"):
            tid = Path(path).name
            p = TRACKS / f"{tid}.mp3"
            if p.exists() and p.parent == TRACKS:
                p.unlink()
                mf.forget(tid)
                return self.send_json({"ok": True, "removed": tid})
            return self.send_json({"error": "not found"}, 404)
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        raw = self.body()
        if path == "/api/import":
            return self.do_import(raw)
        if path == "/api/import/async":
            req = json.loads(raw or b"{}")
            src = (req.get("url") or "").strip()
            if not src.startswith(("http://", "https://")):
                return self.send_json({"error": "url must be http(s)"}, 400)
            args = [PY, str(ROOT / "tools" / "import_track.py"), src]
            for k in ("id", "start", "take", "sensitivity", "bitrate",
                      "sample_rate", "channels", "gain_db", "notes"):
                v = req.get(k)
                if v not in (None, ""):
                    args += [f"--{k.replace('_', '-')}", str(v)]
            if req.get("normalize"):
                args.append("--normalize")
            return self.send_json(_runner.start(args).as_dict())
        if path == "/api/refresh":
            # Rebuild a track from its remembered source, with any option
            # overridden. This is why the manifest exists.
            req = json.loads(raw or b"{}")
            tid = (req.get("id") or "").strip()
            if not tid:
                return self.send_json({"error": "no id"}, 400)
            args = [PY, str(ROOT / "tools" / "import_track.py"), "--refresh", tid]
            for k in ("start", "take", "sensitivity", "bitrate",
                      "sample_rate", "channels", "gain_db"):
                v = req.get(k)
                if v not in (None, ""):
                    args += [f"--{k.replace('_', '-')}", str(v)]
            if req.get("normalize"):
                args.append("--normalize")
            with _lock:
                ok, out = run(args)
            return self.send_json({"ok": ok, "log": out,
                                   "tracks": [track_info(p)
                                              for p in sorted(TRACKS.glob("*.mp3"))]})
        if path == "/api/probe":
            req = json.loads(raw or b"{}")
            return self.send_json(sm.probe((req.get("url") or "").strip()))
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
            return self.do_scene(json.loads(raw or b"{}"))
        if path == "/api/rebuild":
            with _lock:
                ok1, o1 = run([PY, str(ROOT / "tools" / "render_audio.py")])
                ok2, o2 = run([PY, str(ROOT / "tools" / "gen_esphome.py")])
                ok3, o3 = run([PY, str(ROOT / "tools" / "gen_previewer.py")])
            return self.send_json({"ok": ok1 and ok2 and ok3,
                                   "log": (o1 + o2 + o3)[-4000:]})
        self.send_json({"error": "not found"}, 404)

    def do_import(self, raw: bytes):
        ctype = self.headers.get("Content-Type", "")
        TRACKS.mkdir(exist_ok=True)
        args = [PY, str(ROOT / "tools" / "import_track.py")]

        if ctype.startswith("application/json"):
            req = json.loads(raw or b"{}")
            src = (req.get("url") or "").strip()
            if not src:
                return self.send_json({"error": "no url"}, 400)
            if not src.startswith(("http://", "https://")):
                return self.send_json({"error": "url must be http(s)"}, 400)
            args.append(src)
        else:
            # multipart upload: pull out the single file part
            fname, data = parse_multipart(raw, ctype)
            if not data:
                return self.send_json({"error": "no file in upload"}, 400)
            tmp = TRACKS / "_upload"
            tmp.mkdir(exist_ok=True)
            src = tmp / (fname or "upload.bin")
            src.write_bytes(data)
            args.append(str(src))
            req = json.loads(self.headers.get("X-Import-Opts") or "{}")

        for k in ("id", "start", "take", "sensitivity", "bitrate",
                  "sample_rate", "channels", "gain_db", "notes"):
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
                                          for p in sorted(TRACKS.glob("*.mp3"))]}, )

    def do_scene(self, req: dict):
        """Insert or replace a scene block in scenes.yaml.

        Text splicing, not a YAML round-trip: scenes.yaml is a hand-authored
        file full of comments that carry the reasoning behind the show, and
        dumping it back through a YAML serialiser would erase all of them.
        """
        block = (req.get("yaml") or "").rstrip()
        sid = (req.get("id") or "").strip()
        if not block or not sid:
            return self.send_json({"error": "need id and yaml"}, 400)
        raw = SCENES.read_text()
        import re
        pat = re.compile(rf"^  - id: {re.escape(sid)}\n(?:.*\n)*?(?=^  - id: |\Z)", re.M)
        if pat.search(raw):
            raw = pat.sub(block + "\n\n", raw)
        else:
            raw = raw.rstrip() + "\n\n" + block + "\n"
        SCENES.write_text(raw)
        with _lock:
            ok1, o1 = run([PY, str(ROOT / "tools" / "render_audio.py")])
            ok2, o2 = run([PY, str(ROOT / "tools" / "gen_esphome.py")])
            ok3, o3 = run([PY, str(ROOT / "tools" / "gen_previewer.py")])
        return self.send_json({"ok": ok1 and ok2 and ok3, "id": sid,
                               "log": (o1 + o2 + o3)[-4000:]})


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
        return Path(name).name, data.rstrip(b"\r\n-")
    return "", b""


def scene_ids() -> list[str]:
    import yaml
    try:
        doc = yaml.safe_load(SCENES.read_text())
        return [s["id"] for s in doc.get("scenes", [])]
    except Exception:                            # noqa: BLE001
        return []


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
    # Bound to every interface so the cue desk works from a phone or iPad on
    # the same WiFi. That is a deliberate choice for a home network: anyone
    # who can reach this port can drive ffmpeg/yt-dlp and edit files in the
    # repo. Do not run it on a network you do not control.
    host = "0.0.0.0" if "--localhost" not in sys.argv else "127.0.0.1"
    TRACKS.mkdir(exist_ok=True)
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"cue desk studio  ->  http://127.0.0.1:{port}")
    if host == "0.0.0.0":
        print(f"  from your phone  ->  http://{lan_ip()}:{port}")
        print("  (open to your LAN — pass --localhost to restrict to this Mac)")
    print("  serving the previewer with track management enabled")
    print("  ctrl-c to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
