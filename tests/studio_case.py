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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import import_track as it  # noqa: E402
import manifest as mf  # noqa: E402
import studio  # noqa: E402
from helpers import make_click_track  # noqa: E402

CONVERT_OPTS = {"start": 0, "take": None, "fade_in": None, "fade_out": None,
                "bitrate": 96, "channels": 1, "sample_rate": 44100,
                "normalize": False, "gain_db": None}


def make_mp3(dest: Path, seconds: float = 3.0) -> None:
    """A small real MP3, since the endpoints decode what they are given."""
    tmp = Path(tempfile.mkdtemp())
    try:
        src = tmp / "src.wav"
        make_click_track(src, seconds=seconds)
        it.convert(src, dest, dict(CONVERT_OPTS))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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

    # Endpoints read the real tracks/ directory, so the fixtures live there —
    # tagged with the pid so two runs at once cannot fight over a filename,
    # and so the cleanup can never touch a track that is not ours.
    manifest_before: bytes | None
    tracks_before: set[str]
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
        cls.manifest_before = (mf.PATH.read_bytes() if mf.PATH.exists() else None)
        cls.tracks_before = {p.name for p in studio.track_files()
                             if not p.name.startswith(cls.PREFIX)}
        cls.wave = studio.TRACKS / f"{cls.WAVE_ID}.mp3"
        make_mp3(cls.wave)
        # A second track in a container that is not MP3. The format option can
        # produce all four, and every endpoint has to cope with the result —
        # globbing "*.mp3" made a WAV import vanish from the panel entirely.
        cls.wav = studio.TRACKS / f"{cls.WAV_ID}.wav"
        make_click_track(cls.wav, seconds=2.0)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.thread.join(timeout=5)
        for ext in studio.AUDIO_EXT:
            for p in studio.TRACKS.glob(f"{cls.PREFIX}*.{ext}"):
                p.unlink(missing_ok=True)
        shutil.rmtree(studio.TRACKS / "_upload", ignore_errors=True)
        if cls.manifest_before is not None:
            mf.PATH.write_bytes(cls.manifest_before)
        # The invariant worth guarding: a test run must never cost you a track
        # you imported. Checked as "nothing went missing" rather than "the
        # directory is identical", so an unrelated file appearing alongside is
        # not mistaken for damage.
        gone = cls.tracks_before - {p.name for p in studio.track_files()}
        assert not gone, f"tests removed pre-existing tracks: {gone}"

    # ── HTTP ──
    def req(self, method: str, path: str, data: bytes | None = None,
            headers: dict | None = None) -> tuple[int, bytes]:
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                   data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(r, timeout=20) as f:
                return f.status, f.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def get_json(self, path: str) -> tuple[int, dict]:
        code, body = self.req("GET", path)
        return code, json.loads(body)

    def post_json(self, path: str, obj: dict) -> tuple[int, dict]:
        code, body = self.req("POST", path, json.dumps(obj).encode(),
                              {"Content-Type": "application/json"})
        return code, json.loads(body)

