"""The studio fixture: one server, one seeded sandbox, black-box HTTP.

This was the two-studio parity fixture until `tools/studio.py` retired
(docs/RETIREMENT.md phase 3). It launched both servers over twin copies of
one library so their answers could be diffed; there is one server now, so
it launches one — and the suites that used to assert "the two agree" state
what the answer IS instead.

The library it seeds is the rich one the parity suites needed and the
absolute assertions still want: four click tracks of known length, a
zero-frame WAV, a kept `_src` original, a manifest with a cached entry and
a dead `file:` source, and two stem directories (one fresh, one stale).

Nothing here reaches the network or the operator's own files: the four
CASTLE_* knobs are set explicitly and `CASTLE_HOST` is empty unless a
subclass names an emulator (CLAUDE.md's sandboxing section).
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import wave
from pathlib import Path
from typing import Any, ClassVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import cargo_gate
import manifest as mf
from helpers import make_click_track

CARGO = cargo_gate.CARGO
IN_CI = bool(os.environ.get("CI"))
BIN = ROOT / "core" / "target" / "release" / "studio"

#: Two renderable-in-a-blink scenes riding the REPO's own preamble
#: (hardware, zones, palette — the parts the generators need real).
SCENES_TAIL = """\
  - id: vigil
    name: Vigil
    kind: ambient
    volume: 0.45
    duration_ms: 2000
    loop: true
    base: {towerL: candle, towerR: candle, door: ember}
    score:
      - {t: 0, synth: toll, gain: 0.5}
    cues: []

  - id: storm
    name: Storm
    kind: triggered
    volume: 1.0
    duration_ms: 1500
    base: {towerL: candle, towerR: candle, door: ember}
    score:
      - {t: 0.0, synth: wind, dur: 1.5, gain: 0.6}
    cues:
      - {t: 80, op: strike, ms: 70, pixels: scatter, note: "lightning"}
"""


def scenes_fixture() -> str:
    real = (ROOT / "scenes" / "scenes.yaml").read_text()
    preamble = real.split("\nscenes:\n", 1)[0]
    return preamble + "\nscenes:\n" + SCENES_TAIL


#: Both spacing forms the lean rewriter must normalise (`": ?"`), plus one
#: data URI that is NOT a scene entry and must survive untouched.
PAGE = (
    "<!doctype html><title>desk</title><script>const AUDIO = {"
    '"vigil": "data:audio/mpeg;base64,SGVsbG8=", '
    '"storm":"data:audio/mpeg;base64,V29ybGQhIQ=="'
    '};</script><img src="data:audio/mpeg;base64,QUJD">'
)


def build_bin() -> None:
    built = cargo_gate.build()
    assert built.returncode == 0, built.stderr


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def fetch(
    port: int,
    path: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=body,
        method=method,
        headers=headers or {},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read()


def wait_up(port: int, deadline_s: float = 45.0) -> None:
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        try:
            fetch(port, "/api/status")
            return
        except (urllib.error.URLError, OSError):
            time.sleep(0.1)
    raise AssertionError(f"server on {port} never answered")


def _empty_wav(p: Path) -> None:
    """A valid WAV holding zero frames — the len(x)==0 waveform shape."""
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"")


def seed_library(tracks: Path) -> None:
    """The fixture library, from scratch. Re-seedable: a suite whose cases
    delete out of the sandbox calls it again in setUp, and a half-torn
    library is worse than none."""
    shutil.rmtree(tracks, ignore_errors=True)
    tracks.mkdir(parents=True, exist_ok=True)
    make_click_track(tracks / "t_alpha.wav", seconds=2.0)
    make_click_track(tracks / "t_beta.wav", seconds=3.0, bpm=90.0)
    make_click_track(tracks / "t_meta.wav", seconds=2.0)
    make_click_track(tracks / "t_del.wav", seconds=2.0, hats=False)
    _empty_wav(tracks / "t_empty.wav")
    src = tracks / "_src"
    src.mkdir(exist_ok=True)
    (src / "t_del.orig.wav").write_bytes(b"RIFFxxxx-original")
    meta_bytes = (tracks / "t_meta.wav").stat().st_size
    entries: dict[str, mf.Entry] = {
        "t_meta": {
            "source": "https://example.test/meta",
            "title": "Späti 🎃",
            "imported": "2026-08-20T10:00:00",
            "opts": {"start": "0:01", "bitrate": 96},
            "audio": {"duration": 2.34, "bytes": meta_bytes, "channels": 1},
            # level_* entries are not onsets and must be filtered out.
            "onsets": {"onset_low": 3, "onset_mid": 5, "level_full": 7},
            "notes": "cached entry — no decode should happen",
        },
        "t_del": {
            "source": "file:/tmp/nonexistent-original.wav",
            "title": "doomed",
            "imported": "2026-08-21T10:00:00",
            "opts": {},
            "audio": {},
            "onsets": {},
            "notes": "",
        },
    }
    with mock_manifest_path(tracks):
        mf.save(entries)
    # Stems: t_alpha split and fresh, t_beta split from a different file
    # (stale), everything else unsplit. copytree preserves mtimes, so the
    # freshness stamps written here stay true in both twins.
    st_alpha = (tracks / "t_alpha.wav").stat()
    d = tracks / "stems" / "t_alpha"
    d.mkdir(parents=True, exist_ok=True)
    (d / "analysis.json").write_text(
        json.dumps(
            {
                "src_bytes": st_alpha.st_size,
                "src_mtime": int(st_alpha.st_mtime),
                "layers": {
                    "vocals": {
                        "peaks": [0.1, 0.25, 1.0],
                        "onsets": {"onset_mid": [[0.5, 1.0]]},
                    },
                    "backing": {"peaks": []},
                },
                "note": "fixture 🎃",
            }
        )
    )
    (d / "vocals.mp3").write_bytes(b"\xff\xfbSTEMBYTES" * 40)
    st_beta = (tracks / "t_beta.wav").stat()
    d = tracks / "stems" / "t_beta"
    d.mkdir(parents=True, exist_ok=True)
    (d / "analysis.json").write_text(
        json.dumps(
            {
                "src_bytes": st_beta.st_size + 1,
                "src_mtime": int(st_beta.st_mtime),
                "layers": {},
            }
        )
    )


class mock_manifest_path:
    """Point manifest.PATH at a sandbox for one save."""

    def __init__(self, tracks: Path) -> None:
        self.tracks = tracks
        self.old = mf.PATH

    def __enter__(self) -> None:
        mf.PATH = self.tracks / "tracks.json"

    def __exit__(self, *exc: object) -> None:
        mf.PATH = self.old


class StudioCase(unittest.TestCase):
    """Base fixture: the studio bin over a seeded sandbox of its own."""

    #: "" is explicitly castle-less; a subclass names an emulator host.
    HOST_ENV = ""

    tmp: ClassVar[Path]
    tracks: ClassVar[Path]
    port: ClassVar[int]
    procs: ClassVar[list[subprocess.Popen[bytes]]]
    scenes: ClassVar[Path]
    build: ClassVar[Path]

    @classmethod
    def setUpClass(cls) -> None:
        build_bin()
        cls.tmp = Path(tempfile.mkdtemp(prefix="studio-rs-"))
        cls.build = cls.tmp / "build"
        (cls.build / "previewer").mkdir(parents=True)
        (cls.build / "previewer" / "castle-cue-desk.html").write_text(PAGE)
        (cls.build / "audio").mkdir()
        (cls.build / "audio" / "01_vigil.mp3").write_bytes(bytes(range(256)) * 12)
        cls.tracks = cls.tmp / "tracks"
        seed_library(cls.tracks)
        cls.scenes = cls.tmp / "scenes.yaml"
        cls.scenes.write_text(scenes_fixture())
        # free_port() closes the socket before the server binds it, so a
        # busy machine (another suite, the user's own studio) can take the
        # port in between. One retry on a fresh port is the cheap answer:
        # the window is milliseconds, so losing it twice is not a race any
        # more — it is a machine with no free ports (grade report 2026-08-31 D6).
        for attempt in (0, 1):
            try:
                cls._launch()
                return
            except (AssertionError, OSError):
                cls._kill()
                if attempt:
                    raise

    @classmethod
    def _kill(cls) -> None:
        for p in getattr(cls, "procs", []):
            p.terminate()
        for p in getattr(cls, "procs", []):
            p.wait(timeout=10)
        cls.procs = []

    @classmethod
    def _launch(cls) -> None:
        cls.port = free_port()
        env = {**os.environ}
        for k in ("CASTLE_HOST", "CASTLE_TRACKS", "CASTLE_SCENES", "CASTLE_BUILD"):
            env.pop(k, None)
        # The importer, the generators and the manifest write are Python
        # children of the server, and a BINARY has no sys.executable to
        # hand them: it asks CASTLE_PY, then <root>/.venv/bin/python, then
        # a bare python3 with no yaml (core/src/studio_proc.rs py()). Named
        # HERE, in the child's environment only — a suite that exported it
        # into this process would leak it into every other one, which is
        # the hermeticity tests/test_hermetic.py exists to catch. An
        # interpreter the operator named on purpose still wins.
        venv = ROOT / ".venv" / "bin" / "python"
        if "CASTLE_PY" not in env and venv.exists():
            env["CASTLE_PY"] = str(venv)
        cls.procs = [
            subprocess.Popen(
                [str(BIN), str(cls.port), "--localhost"],
                env={
                    **env,
                    "CASTLE_HOST": cls.HOST_ENV,
                    "CASTLE_TRACKS": str(cls.tracks),
                    "CASTLE_SCENES": str(cls.scenes),
                    "CASTLE_BUILD": str(cls.build),
                },
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        ]
        wait_up(cls.port)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._kill()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def req(
        self,
        path: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        return fetch(self.port, path, method, headers, body)

    def json(
        self,
        path: str,
        method: str = "GET",
        obj: object | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """One request, its status and its parsed object body."""
        headers = {"Content-Type": "application/json"} if obj is not None else None
        raw = self.req(
            path, method, headers, json.dumps(obj).encode() if obj is not None else None
        )
        parsed = json.loads(raw[2])
        assert isinstance(parsed, dict), parsed
        return raw[0], parsed

    def masked(self, text: str) -> str:
        """A log with this sandbox's paths replaced by tokens, so what a
        rebuild says can be asserted without naming a temp directory."""
        return (
            text.replace(str(self.build), "<BUILD>")
            .replace(str(self.scenes), "<SCENES>")
            .replace(str(self.tracks), "<TRACKS>")
        )
