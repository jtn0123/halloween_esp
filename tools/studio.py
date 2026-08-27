#!/usr/bin/env python3
"""Local companion server for the cue desk (`make studio` -> 127.0.0.1:8765).

The previewer is one static HTML file; this small server is what lets its
Tracks panel run yt-dlp/ffmpeg, write tracks/, and edit scenes.yaml. Routes:
docs/API.md. What the studio OWNS lives under /studio/...; /api/... is the
castle's and relays to it untouched (castle_link.py) — except /api/status,
answered here when no castle is in reach. Old /api/ spellings of studio
routes are aliases for one release (STUDIO_ROUTES below).

Binds to 127.0.0.1 by default; `--lan` opens it to the local network for
the phone/iPad remote — only on a network you control: a LAN visitor can
import and delete tracks, rewrite scenes.yaml, push to the castle and stop
the server, with no login.
"""

from __future__ import annotations

import os
import shutil  # noqa: F401 — used as app.shutil by studio_routes
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import build_paths as bp
import castle_link as cl
import gen_previewer as gp  # noqa: F401
import manifest as mf  # noqa: F401
import netguard as ng  # noqa: F401
import stems as st  # noqa: F401
import studio_http as sh
import studio_jobs as sj
import studio_media as sm  # noqa: F401
import studio_publish as sp  # noqa: F401
import studio_scenes as ss
from studio_jobs import (  # noqa: F401 — the importer's CLI flags, one place
    OPT_KEYS,
    opt_args,
)

# Re-exported: TRACKS and these helpers are the track vocabulary, and
# callers (including the tests) reach for them through this module.
from studio_tracks import (  # noqa: F401 — the routes and tests reach through here
    AUDIO_EXT,
    MIME,
    TRACKS,
    parse_sensitivity,
    source_copies,
    track_files,
    track_info,
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

_lock = threading.Lock()  # ffmpeg/yt-dlp jobs are serialised
_runner = sj.JobRunner(gate=_lock)  # background jobs queue behind the same lock


def _restart() -> None:
    """Replace this process with a fresh copy of itself. os.execv keeps the
    PID, so whatever launched us (a launcher, launchd) notices nothing."""
    time.sleep(0.4)  # let the HTTP response actually go out
    os.execv(sys.executable, [sys.executable, *sys.argv])


def run(cmd: list[str], timeout: int = 900) -> tuple[bool, str]:
    # The ceiling matters more than its value: these run holding _lock, so
    # one hung ffmpeg/yt-dlp used to wedge every later import and rebuild.
    try:
        p = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return False, f"gave up after {timeout}s — the job stalled"
    return p.returncode == 0, (p.stdout + p.stderr)[-4000:]


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


def served() -> tuple[Path, Path]:
    """The page the studio serves and the audio/ it was built from — a
    sandbox's own build once it has one, the repo's until then; always both,
    so the lean page's /studio/scene-audio/ links resolve to its own files."""
    if bp.sandboxed() and bp.PREVIEW_HTML.exists():
        return bp.PREVIEW_HTML, bp.AUDIO
    return HTML, ROOT / "audio"


# The /api->/studio alias table and rewriter live in studio_http.py now
# (HTTP plumbing, same seam as the senders); the names stay importable here.
studio_path = sh.studio_path
STUDIO_ROUTES = sh.STUDIO_ROUTES
API = sh.API
_deprecated_seen = sh._deprecated_seen

# Imported HERE, after run/safe_id/served and the lock exist: the routes
# module resolves everything through this module at call time, and this
# late import is the half of the cycle that makes that safe.
import studio_routes as sr  # noqa: E402


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
        sr.handle_get(self)

    def _delete(self):
        sr.handle_delete(self)

    def _post(self):
        sr.handle_post(self)

    def _put(self):
        sr.handle_put(self)

    def relay(self, method: str, body: bytes = b"", to: str | None = None) -> None:
        """Hand a castle-shaped /api/* request to the castle, answer as it did."""
        code, out, ctype = cl.forward(method, to or self.path, body)
        self._send_plain(out, ctype, code, [])


def scene_ids() -> list[str]:
    return ss.scene_ids(SCENES)


def lan_ip() -> str:
    """Best guess at this machine's address on the LAN, for the banner."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packets sent; just picks a route
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
