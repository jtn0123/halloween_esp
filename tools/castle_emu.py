"""A castle on the desk: emulates the SD build's HTTP surface for testing.

The real device (firmware/sd_web.h) can only be exercised by plugging it in.
This serves the same routes with the same validation, the same error strings
and the same queued-action semantics, so the whole chain — desk → studio
relay → castle — runs end-to-end on the Mac with zero hardware:

    .venv/bin/python tools/castle_emu.py 8093 &
    CASTLE_HOST=127.0.0.1:8093 .venv/bin/python tools/studio.py

Fidelity notes, each mirrored from sd_web.h on purpose:
  - POSTs answer {"queued":true} immediately; the state changes ~200 ms
    later (the device's pending-action mailbox + main-loop interval). A UI
    that reads state right after a click sees the OLD state, exactly as it
    would on the porch.
  - /api/volume takes digits only, 0..100 — atoi("abc")-is-0 was dogfood
    ISSUE-007.
  - /api/scene 404s an unknown id — {"queued":true} for a typo was 008.
  - Filenames are one path component, nothing hidden, same safe_name rule.
  - --wedge replays the pre-v5.22 firmware defect: while a track plays,
    every request stalls. Lets the desk's "castle not answering" path be
    rehearsed without flashing the old build.

The card is a directory (--dir, default a temp dir seeded with two tones);
uploads and deletes are real files, so a send can be verified with ls.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

#: The device applies queued actions on its main-loop interval.
APPLY_DELAY_S = 0.2
#: Rough playback clock: 96 kbps MP3 is ~12 kB of file per second.
BYTES_PER_S = 12000

#: Used only when no scenes.yaml can be found — the firmware seeds its list
#: from the generated show, so the emulator reads the same source of truth
#: (CASTLE_SCENES, else the repo's scenes/scenes.yaml) and 404s exactly the
#: ids the real castle would.
DEFAULT_SCENES = ["vigil", "storm", "arrival", "stop"]
ROOT = Path(__file__).resolve().parent.parent


def show_scene_ids(path: Path | None = None) -> list[str] | None:
    """Scene ids from a scenes.yaml — CASTLE_SCENES, else the repo's — or
    None when there is no readable show to seed from."""
    import yaml
    src = path or Path(os.environ.get("CASTLE_SCENES")
                       or ROOT / "scenes" / "scenes.yaml")
    try:
        doc = yaml.safe_load(src.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return None
    ids = [str(sc["id"]) for sc in doc.get("scenes", []) if "id" in sc]
    return [*ids, "stop"] if ids else None


def safe_name(n: str) -> bool:
    """One path component, nothing hidden — sd_web.h's rule verbatim."""
    return bool(n) and len(n) < 100 and n[0] != "." and "/" not in n and ".." not in n


class _State:
    """What the firmware's globals hold, behind one lock."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.volume = 70
        self.scene = ""
        self.track = ""
        self.track_ends = 0.0
        self.show_on = False
        self.pir = {"armed": True, "cooldown_s": 60, "scene": "storm"}
        self.light = "show"
        self.boot = time.monotonic()


class CastleEmu(ThreadingHTTPServer):
    """The emulated castle. Construct with port 0 to get an ephemeral port."""

    daemon_threads = True

    def __init__(self, port: int = 0, sd_dir: Path | None = None,
                 scenes: list[str] | None = None, version: str = "5.23",
                 wedge: bool = False, sd_mounted: bool = True,
                 serial: bool = False) -> None:
        super().__init__(("127.0.0.1", port), _Handler)
        self.state = _State()
        self.sd_dir = sd_dir or Path(tempfile.mkdtemp(prefix="castle-emu-sd-"))
        self.sd_dir.mkdir(parents=True, exist_ok=True)
        self.scenes = (scenes if scenes is not None
                       else show_scene_ids() or list(DEFAULT_SCENES))
        self.version = version
        self.wedge = wedge
        self.sd_mounted = sd_mounted
        # The real httpd is ONE task: a long PUT holds every other request
        # (the status poll included) until it finishes. --serial rehearses
        # that; the default threads so the bench stays snappy.
        self.serial = threading.Lock() if serial else None
        self._pending: list[tuple[str, str]] = []
        threading.Thread(target=self._ticker, daemon=True,
                         name="castle-emu-tick").start()

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def start(self) -> None:
        threading.Thread(target=self.serve_forever, daemon=True,
                         name="castle-emu").start()

    # -- the pending-action mailbox ---------------------------------------

    def queue(self, action: str, arg: str) -> None:
        with self.state.lock:
            self._pending.append((action, arg))

    def _ticker(self) -> None:
        while True:
            time.sleep(APPLY_DELAY_S)
            with self.state.lock:
                batch, self._pending = self._pending, []
                st = self.state
                if st.track and time.monotonic() > st.track_ends:
                    st.track = ""          # the song ended on its own
            for action, arg in batch:
                self._apply(action, arg)

    def _apply(self, action: str, arg: str) -> None:
        st = self.state
        with st.lock:
            if action == "VOLUME":
                st.volume = int(arg)
            elif action == "PLAY":
                f = self.sd_dir / arg
                size = f.stat().st_size if f.is_file() else 0
                st.track = arg
                st.track_ends = time.monotonic() + max(1, size // BYTES_PER_S)
            elif action == "SCENE":
                st.scene = arg
                audio = self.sd_dir / "scenes" / f"{arg}.mp3"
                if audio.is_file():
                    st.track = audio.name
                    st.track_ends = (time.monotonic()
                                     + max(1, audio.stat().st_size // BYTES_PER_S))
            elif action in ("STOP", "BLACKOUT"):
                st.scene, st.track, st.show_on = "", "", False
            elif action == "SHOW":
                st.show_on = arg == "1"
            elif action == "LIGHT":
                st.light = arg
            elif action == "PIRCFG":
                armed, cool, scene = [*arg.split("|"), "", "", ""][:3]
                if armed:
                    st.pir["armed"] = armed == "1"
                if cool:
                    st.pir["cooldown_s"] = int(cool)
                if scene:
                    st.pir["scene"] = scene

    def status_json(self) -> dict[str, object]:
        st = self.state
        # Real numbers from the disk under the card dir — the point is that
        # the field EXISTS and is honest, same as v5.23's esp_vfs_fat_info.
        du = shutil.disk_usage(self.sd_dir)
        with st.lock:
            return {
                "version": self.version, "compiled": "emulated",
                "uptime_s": int(time.monotonic() - st.boot),
                "sd_mounted": self.sd_mounted,
                "psram_free_kb": 1800, "heap_free_kb": 96,
                "sd_total_kb": du.total // 1024 if self.sd_mounted else 0,
                "sd_free_kb": du.free // 1024 if self.sd_mounted else 0,
                "missing": "",
                "volume": st.volume, "scene": st.scene, "track": st.track,
                "show_on": st.show_on,
                "pir": {"armed": st.pir["armed"],
                        "cooldown_s": st.pir["cooldown_s"],
                        "scene": st.pir["scene"]},
            }


class _Handler(BaseHTTPRequestHandler):
    server: CastleEmu  # type: ignore[assignment]  # narrowed for handlers

    # -- plumbing ----------------------------------------------------------

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # tests and background use; the port banner is enough

    def handle(self) -> None:
        if self.server.serial is None:
            return super().handle()
        with self.server.serial:
            super().handle()

    def _json(self, body: dict[str, object] | list[object]) -> None:
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _err(self, code: int, msg: str) -> None:
        raw = msg.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _q(self, key: str) -> str:
        qs = parse_qs(urlsplit(self.path).query)
        return (qs.get(key) or [""])[0]

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

    # -- GET ---------------------------------------------------------------

    def do_GET(self) -> None:
        self._wedge()
        path = urlsplit(self.path).path
        if path == "/api/status":
            return self._json(self.server.status_json())
        if path == "/api/health":
            return self._json({"boots": 3, "crashes": 0,
                               "last_reset": "power-on", "was_crash": False})
        if path == "/api/files":
            return self._list()
        if path == "/api/bootlog":
            return self._err(200, "boot log: 2 lines, 0 dropped\n[I][emu] up\n")
        if path == "/api/blackout":          # registered for GET too: bookmarkable
            self.server.queue("BLACKOUT", "")
            return self._json({"queued": True})
        if path.startswith("/sd/"):
            return self._sd_get(path)
        self._err(404, "not found")

    def _list(self) -> None:
        if not self.server.sd_mounted:
            return self._err(503, "no SD card")
        out: list[object] = []
        for p in sorted(self.server.sd_dir.iterdir()):
            if p.name.startswith("."):
                continue
            out.append({"name": p.name,
                        "size": p.stat().st_size if p.is_file() else 0,
                        "dir": p.is_dir()})
        self._json(out)

    def _sd_get(self, path: str) -> None:
        name = unquote(path[len("/sd/"):])
        # Serving is nested-path capable on the device; keep the same guard.
        if ".." in name or name.startswith("/") or not name:
            return self._err(400, "bad path")
        f = self.server.sd_dir / name
        if not f.is_file():
            return self._err(404, "no such file")
        raw = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    # -- POST: show control, all queued ------------------------------------

    def do_POST(self) -> None:
        self._wedge()
        path = urlsplit(self.path).path
        if path == "/api/play":
            f = self._q("f")
            if not safe_name(f):
                return self._err(400, "need ?f=<file>")
            self.server.queue("PLAY", f)
        elif path == "/api/scene":
            s = self._q("s")
            if not s:
                return self._err(400, "need ?s=<scene>")
            if self.server.scenes and s not in self.server.scenes:
                return self._err(404, "unknown scene")
            self.server.queue("SCENE", s)
        elif path == "/api/stop":
            self.server.queue("STOP", "")
        elif path == "/api/blackout":
            self.server.queue("BLACKOUT", "")
        elif path == "/api/volume":
            v = self._q("v")
            if not re.fullmatch(r"[0-9]{1,3}", v) or int(v) > 100:
                return self._err(400, "need ?v=0..100")
            self.server.queue("VOLUME", v)
        elif path == "/api/light":
            c = self._q("c")
            if not re.fullmatch(r"[0-9a-fA-F]{6}", c) and c not in ("show", "off"):
                return self._err(400, "need ?c=RRGGBB, show, or off")
            self.server.queue("LIGHT", c)
        elif path == "/api/pir":
            armed, cool, scene = self._q("armed"), self._q("cooldown"), self._q("scene")
            if not (armed or cool or scene):
                return self._err(400, "need armed=, cooldown= or scene=")
            self.server.queue("PIRCFG", f"{armed}|{cool}|{scene}")
        elif path in ("/api/show/start", "/api/show/stop"):
            self.server.queue("SHOW", "1" if path.endswith("start") else "0")
        else:
            return self._err(404, "not found")
        self._json({"queued": True})

    # -- PUT/DELETE: the card ----------------------------------------------

    def do_PUT(self) -> None:
        self._wedge()
        path = urlsplit(self.path).path
        sub = ""
        for prefix, d in (("/api/site/", "site"), ("/api/scenes/", "scenes"),
                          ("/api/files/", "")):
            if path.startswith(prefix):
                name, sub = unquote(path[len(prefix):]), d
                break
        else:
            return self._err(404, "not found")
        if not self.server.sd_mounted:
            return self._err(503, "no SD card")
        if not safe_name(name):
            return self._err(400, "bad filename")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        dest = self.server.sd_dir / sub if sub else self.server.sd_dir
        dest.mkdir(parents=True, exist_ok=True)
        (dest / name).write_bytes(body)
        card = f"/sd/{sub}/{name}" if sub else f"/sd/{name}"
        self._json({"path": card, "bytes": len(body)})

    def do_DELETE(self) -> None:
        self._wedge()
        path = urlsplit(self.path).path
        if not path.startswith("/api/files/"):
            return self._err(404, "not found")
        if not self.server.sd_mounted:
            return self._err(503, "no SD card")
        name = unquote(path[len("/api/files/"):])
        if not safe_name(name):
            return self._err(400, "bad filename")
        f = self.server.sd_dir / name
        if not f.is_file():
            return self._err(404, "no such file")
        f.unlink()
        self._json({"deleted": True})


def _seed(card: Path) -> None:
    """Two placeholder 'songs' so the desk has something to list and play."""
    for name, kb in (("wicked_winds.mp3", 280), ("ghostbusters.mp3", 960)):
        f = card / name
        if not f.exists():
            f.write_bytes(b"\xff\xfb" + b"\x00" * (kb * 1024 - 2))
    (card / "logs").mkdir(exist_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("port", nargs="?", type=int, default=8093)
    ap.add_argument("--dir", type=Path, default=None,
                    help="directory that plays the SD card (default: temp, seeded)")
    ap.add_argument("--wedge", action="store_true",
                    help="replay the pre-v5.22 wedge: stall requests while playing")
    ap.add_argument("--no-sd", action="store_true",
                    help="pretend the card is missing")
    ap.add_argument("--serial", action="store_true",
                    help="one request at a time, like the device's single httpd task")
    ap.add_argument("--scenes", default=None,
                    help="scene ids: a comma list, or a scenes.yaml "
                         "(default: $CASTLE_SCENES, else scenes/scenes.yaml)")
    args = ap.parse_args()
    scenes: list[str] | None = None
    if args.scenes:
        scenes = (show_scene_ids(Path(args.scenes)) if args.scenes.endswith(".yaml")
                  else [x.strip() for x in args.scenes.split(",") if x.strip()])
    emu = CastleEmu(port=args.port, sd_dir=args.dir, wedge=args.wedge,
                    sd_mounted=not args.no_sd, serial=args.serial,
                    scenes=scenes)
    if args.dir is None:
        _seed(emu.sd_dir)
    print(f"castle emulator on http://127.0.0.1:{emu.port}  card={emu.sd_dir}"
          + ("  [WEDGE MODE]" if args.wedge else "")
          + ("  [SERIAL]" if args.serial else ""))
    print(f"  scenes: {', '.join(emu.scenes)}")
    print(f"  point the studio at it:  CASTLE_HOST=127.0.0.1:{emu.port}")
    emu.serve_forever()


if __name__ == "__main__":
    main()
