"""The two-studio parity fixture — shared by every test that holds the Rust
studio (castle-core's `studio` bin) answer-for-answer with the Python one.

Split along tests/studio_case.py's own seam: this is infrastructure — two
servers over twin copies of one fixture library — and the test files keep
the assertions. Twin copies rather than one shared sandbox on purpose: the
live-analysis path and the manifest write-back must run in BOTH languages,
and the leftover tracks.json files are then compared byte for byte.
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
from typing import ClassVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import manifest as mf
from helpers import make_click_track

CARGO = shutil.which("cargo")
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
    assert CARGO is not None
    built = subprocess.run(
        [
            CARGO,
            "build",
            "--release",
            "--quiet",
            "--manifest-path",
            str(ROOT / "core" / "Cargo.toml"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
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
    """One fixture library — built identically for each server."""
    tracks.mkdir(parents=True, exist_ok=True)
    make_click_track(tracks / "t_alpha.wav", seconds=2.0)
    make_click_track(tracks / "t_beta.wav", seconds=3.0, bpm=90.0)
    make_click_track(tracks / "t_meta.wav", seconds=2.0)
    make_click_track(tracks / "t_del.wav", seconds=2.0, hats=False)
    _empty_wav(tracks / "t_empty.wav")
    src = tracks / "_src"
    src.mkdir()
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
    d.mkdir(parents=True)
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
    d.mkdir(parents=True)
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


class StudioPair(unittest.TestCase):
    """Base fixture: the two servers over twin copies of one sandbox."""

    HOST_ENV = ""  # explicitly castle-less unless a subclass says otherwise
    #: Per-side overrides, for suites that give each server its own castle
    #: (a shared emulator would let one server's push mark the other's
    #: files "unchanged, skipped" and split the logs).
    HOST_ENV_PY: str | None = None
    HOST_ENV_RS: str | None = None

    tmp: ClassVar[Path]
    py_tracks: ClassVar[Path]
    rs_tracks: ClassVar[Path]
    py_port: ClassVar[int]
    rs_port: ClassVar[int]
    procs: ClassVar[list[subprocess.Popen[bytes]]]

    py_scenes: ClassVar[Path]
    rs_scenes: ClassVar[Path]
    py_build: ClassVar[Path]
    rs_build: ClassVar[Path]

    @classmethod
    def setUpClass(cls) -> None:
        build_bin()
        cls.tmp = Path(tempfile.mkdtemp(prefix="studio-rust-"))
        scenes_text = scenes_fixture()
        template_build = cls.tmp / "template_build"
        (template_build / "previewer").mkdir(parents=True)
        (template_build / "previewer" / "castle-cue-desk.html").write_text(PAGE)
        (template_build / "audio").mkdir()
        (template_build / "audio" / "01_vigil.mp3").write_bytes(bytes(range(256)) * 12)
        template = cls.tmp / "template"
        seed_library(template)
        cls.py_tracks = cls.tmp / "py_tracks"
        cls.rs_tracks = cls.tmp / "rs_tracks"
        shutil.copytree(template, cls.py_tracks)
        shutil.copytree(template, cls.rs_tracks)
        cls.py_build = cls.tmp / "py_build"
        cls.rs_build = cls.tmp / "rs_build"
        shutil.copytree(template_build, cls.py_build)
        shutil.copytree(template_build, cls.rs_build)
        cls.py_scenes = cls.tmp / "py_scenes.yaml"
        cls.rs_scenes = cls.tmp / "rs_scenes.yaml"
        cls.py_scenes.write_text(scenes_text)
        cls.rs_scenes.write_text(scenes_text)
        # free_port() closes the socket before the server binds it, so a
        # busy machine (another suite, the user's own studio) can take the
        # port in between. One retry on fresh ports is the cheap answer:
        # the window is milliseconds, so losing it twice is not a race any
        # more — it is a machine with no free ports (grade report D6).
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
        """Both servers on a fresh pair of ports, up and answering."""
        cls.py_port, cls.rs_port = free_port(), free_port()
        env = {**os.environ}
        env.pop("CASTLE_HOST", None)
        cls.procs = [
            subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "tools" / "studio.py"),
                    str(cls.py_port),
                    "--localhost",
                ],
                env={
                    **env,
                    "CASTLE_HOST": cls.HOST_ENV_PY
                    if cls.HOST_ENV_PY is not None
                    else cls.HOST_ENV,
                    "CASTLE_TRACKS": str(cls.py_tracks),
                    "CASTLE_SCENES": str(cls.py_scenes),
                    "CASTLE_BUILD": str(cls.py_build),
                },
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ),
            subprocess.Popen(
                [str(BIN), str(cls.rs_port), "--localhost"],
                env={
                    **env,
                    "CASTLE_HOST": cls.HOST_ENV_RS
                    if cls.HOST_ENV_RS is not None
                    else cls.HOST_ENV,
                    "CASTLE_TRACKS": str(cls.rs_tracks),
                    "CASTLE_SCENES": str(cls.rs_scenes),
                    "CASTLE_BUILD": str(cls.rs_build),
                },
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ),
        ]
        wait_up(cls.py_port)
        wait_up(cls.rs_port)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._kill()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def both(
        self,
        path: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> tuple[tuple[int, dict[str, str], bytes], tuple[int, dict[str, str], bytes]]:
        a = fetch(self.py_port, path, method, headers, body)
        b = fetch(self.rs_port, path, method, headers, body)
        self.assertEqual(
            a[0], b[0], f"{method} {path}: {a[0]} vs {b[0]} — {a[2]!r} vs {b[2]!r}"
        )
        return a, b

    def parsed(self, raw: tuple[int, dict[str, str], bytes]) -> object:
        return json.loads(raw[2])

    def masked(self, text: str, side: str) -> str:
        """A log with this server's sandbox paths replaced by tokens, so
        the two sides' logs can be compared byte for byte."""
        build = self.py_build if side == "py" else self.rs_build
        scenes = self.py_scenes if side == "py" else self.rs_scenes
        tracks = self.py_tracks if side == "py" else self.rs_tracks
        return (
            text.replace(str(build), "<BUILD>")
            .replace(str(scenes), "<SCENES>")
            .replace(str(tracks), "<TRACKS>")
        )
