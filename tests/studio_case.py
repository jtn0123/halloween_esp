"""The studio server fixture, shared by every test that drives it over HTTP.

Split out of test_studio_api.py at the 500-line cap, along the obvious seam:
this is infrastructure — a server on a port and a couple of disposable tracks —
and everything left in the test files is assertions about behaviour.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import castle_link as cl
import import_track as it
import manifest as mf
import studio
import studio_tracks
from helpers import make_click_track

CONVERT_OPTS = {
    "start": 0,
    "take": None,
    "fade_in": None,
    "fade_out": None,
    "bitrate": 96,
    "channels": 1,
    "sample_rate": 44100,
    "normalize": False,
    "gain_db": None,
}


def make_mp3(dest: Path, seconds: float = 3.0) -> None:
    """A small real MP3, since the endpoints decode what they are given."""
    tmp = Path(tempfile.mkdtemp())
    try:
        src = tmp / "src.wav"
        make_click_track(src, seconds=seconds)
        it.convert(src, dest, dict(CONVERT_OPTS))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class HostEnv:
    """`self.host_env("10.0.0.7")` — CASTLE_HOST for one test, put back after.

    Every suite that touches the resolver was writing os.environ by hand and
    restoring it in tearDown (or forgetting to). patch.dict restores even when
    an assertion raises mid-test, and addCleanup runs in reverse order, so the
    outer patch a setUp installs still wins.
    """

    def host_env(self, value: str | None) -> None:
        env = mock.patch.dict(
            os.environ, {} if value is None else {"CASTLE_HOST": value}, clear=False
        )
        env.start()
        if value is None:
            os.environ.pop("CASTLE_HOST", None)
        self.addCleanup(env.stop)  # type: ignore[attr-defined]


class Quiet(studio.Handler):
    """The real handler logs every request to stderr; tests do not need that."""

    def log_message(self, fmt, *a):
        pass


class ServerCase(unittest.TestCase):
    """Base fixture: a server on an ephemeral port, and disposable tracks.

    Port 0 rather than 8765 on purpose — the user may well have a real studio
    running, and a test that fights it for the port is a test that fails for
    the wrong reason.
    """

    # A REAL sandbox, not fixtures-in-the-real-library: CASTLE_TRACKS in the
    # env covers the import_track.py child processes the server spawns, and
    # the four already-imported parent bindings are patched to match. The
    # old scheme (pid-tagged fixtures in the live tracks/, restored on
    # teardown) left debris in the user's library whenever a run crashed —
    # the exact thing the env knob exists to prevent (grade report D2).
    sandbox: Path
    _sandbox_patches: list
    wave: Path
    wav: Path
    srv: ThreadingHTTPServer
    port: int
    thread: threading.Thread

    PREFIX = f"_t_studio_{os.getpid()}_"
    WAVE_ID = PREFIX + "wave"
    WAV_ID = PREFIX + "lossless"
    DEL_ID = PREFIX + "delete"

    @classmethod
    def setUpClass(cls) -> None:
        cls.sandbox = Path(tempfile.mkdtemp(prefix="castle-tests-"))
        cls._sandbox_patches = [
            # CASTLE_HOST too: the studio now RELAYS unclaimed /api/* calls
            # to the castle (castle_link.py), and a suite that resolved the
            # real porch address from devices.toml would be driving live
            # hardware from the tests. Port 1 refuses instantly.
            mock.patch.dict(
                os.environ,
                {"CASTLE_TRACKS": str(cls.sandbox), "CASTLE_HOST": "127.0.0.1:1"},
            ),
            mock.patch.object(studio, "TRACKS", cls.sandbox),
            mock.patch.object(studio_tracks, "TRACKS", cls.sandbox),
            mock.patch.object(it, "TRACKS", cls.sandbox),
            mock.patch.object(mf, "PATH", cls.sandbox / "tracks.json"),
        ]
        for patch in cls._sandbox_patches:
            patch.start()
        cls.wave = studio.TRACKS / f"{cls.WAVE_ID}.mp3"
        make_mp3(cls.wave)
        # A second track in a container that is not MP3. The format option can
        # produce all four, and every endpoint has to cope with the result —
        # globbing "*.mp3" made a WAV import vanish from the panel entirely.
        cls.wav = studio.TRACKS / f"{cls.WAV_ID}.wav"
        make_click_track(cls.wav, seconds=2.0)
        cl._cache.clear()  # no live status leaking between test classes
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.thread.join(timeout=5)
        sandbox = cls.sandbox
        for patch in cls._sandbox_patches:
            patch.stop()
        shutil.rmtree(sandbox, ignore_errors=True)
        # The invariant the old fixture guarded by hand — "a test run must
        # never cost you a track" — now holds by construction: nothing in
        # the run ever pointed at the real library.

    # ── HTTP ──
    def req(
        self,
        method: str,
        path: str,
        data: bytes | None = None,
        headers: dict | None = None,
    ) -> tuple[int, bytes]:
        r = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            method=method,
            headers=headers or {},
        )
        try:
            with urllib.request.urlopen(r, timeout=20) as f:
                return f.status, f.read()
        except urllib.error.HTTPError as e:
            with e:  # closed, or unittest warns on GC
                return e.code, e.read()

    def get_json(self, path: str) -> tuple[int, dict]:
        code, body = self.req("GET", path)
        return code, json.loads(body)

    def post_json(self, path: str, obj: dict) -> tuple[int, dict]:
        code, body = self.req(
            "POST", path, json.dumps(obj).encode(), {"Content-Type": "application/json"}
        )
        return code, json.loads(body)
