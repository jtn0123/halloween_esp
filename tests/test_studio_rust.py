"""The Rust studio against the Python studio — B5 pass 1, the read side.

castle-core's `studio` bin serves the cue desk's HTTP from the crate that
already owns the show's arithmetic. Both servers run side by side, each on
its own copy of ONE fixture library — so the live-analysis path and the
manifest write-back are exercised in both languages — and every answer is
compared: status codes, parsed JSON bodies, streamed bytes, validators,
and the tracks.json each side leaves behind (byte for byte). The write
groups (import, jobs, scenes, publish, relay caches) arrive in later
passes; this file grows with them.

Skipped, not failed, without cargo — except in CI.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import urllib.error
import urllib.request
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

SCENES_YAML = textwrap.dedent(
    """\
    # A sandbox show for the parity harness.
    version: 3
    zones:
      - id: door
      - id: tower_l
    scenes:
      - id: vigil  # the quiet one
        length_ms: 2000
      - id: storm
        length_ms: 3000
    """
)

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
        with urllib.request.urlopen(req, timeout=30) as r:
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


def seed_library(tracks: Path) -> None:
    """One fixture library — built identically for each server."""
    tracks.mkdir(parents=True, exist_ok=True)
    make_click_track(tracks / "t_alpha.wav", seconds=2.0)
    make_click_track(tracks / "t_beta.wav", seconds=3.0, bpm=90.0)
    make_click_track(tracks / "t_meta.wav", seconds=2.0)
    make_click_track(tracks / "t_del.wav", seconds=2.0, hats=False)
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

    tmp: ClassVar[Path]
    py_tracks: ClassVar[Path]
    rs_tracks: ClassVar[Path]
    py_port: ClassVar[int]
    rs_port: ClassVar[int]
    procs: ClassVar[list[subprocess.Popen[bytes]]]

    @classmethod
    def setUpClass(cls) -> None:
        build_bin()
        cls.tmp = Path(tempfile.mkdtemp(prefix="studio-rust-"))
        (cls.tmp / "scenes.yaml").write_text(SCENES_YAML)
        build = cls.tmp / "build"
        (build / "previewer").mkdir(parents=True)
        (build / "previewer" / "castle-cue-desk.html").write_text(PAGE)
        (build / "audio").mkdir()
        (build / "audio" / "01_vigil.mp3").write_bytes(bytes(range(256)) * 12)
        template = cls.tmp / "template"
        seed_library(template)
        cls.py_tracks = cls.tmp / "py_tracks"
        cls.rs_tracks = cls.tmp / "rs_tracks"
        shutil.copytree(template, cls.py_tracks)
        shutil.copytree(template, cls.rs_tracks)
        cls.py_port, cls.rs_port = free_port(), free_port()
        env = {
            **os.environ,
            "CASTLE_SCENES": str(cls.tmp / "scenes.yaml"),
            "CASTLE_BUILD": str(build),
            "CASTLE_HOST": cls.HOST_ENV,
        }
        cls.procs = [
            subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "tools" / "studio.py"),
                    str(cls.py_port),
                    "--localhost",
                ],
                env={**env, "CASTLE_TRACKS": str(cls.py_tracks)},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ),
            subprocess.Popen(
                [str(BIN), str(cls.rs_port), "--localhost"],
                env={**env, "CASTLE_TRACKS": str(cls.rs_tracks)},
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ),
        ]
        wait_up(cls.py_port)
        wait_up(cls.rs_port)

    @classmethod
    def tearDownClass(cls) -> None:
        for p in cls.procs:
            p.terminate()
        for p in cls.procs:
            p.wait(timeout=10)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def both(
        self,
        path: str,
        method: str = "GET",
        headers: dict[str, str] | None = None,
    ) -> tuple[tuple[int, dict[str, str], bytes], tuple[int, dict[str, str], bytes]]:
        a = fetch(self.py_port, path, method, headers)
        b = fetch(self.rs_port, path, method, headers)
        self.assertEqual(
            a[0], b[0], f"{method} {path}: {a[0]} vs {b[0]} — {a[2]!r} vs {b[2]!r}"
        )
        return a, b


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class CastleLess(StudioPair):
    """CASTLE_HOST='' — the simulator-on-purpose configuration."""

    def parsed(self, raw: tuple[int, dict[str, str], bytes]) -> object:
        import json

        return json.loads(raw[2])

    def test_01_tracks_listing_matches_live_then_cached(self) -> None:
        a, b = self.both("/studio/tracks")
        self.assertEqual(self.parsed(a), self.parsed(b))
        self.assertEqual(a[1]["content-type"], b[1]["content-type"])
        # The listing decoded two fixtures and wrote the answers back —
        # the manifests must now be byte-identical.
        self.assertEqual(
            (self.py_tracks / "tracks.json").read_text(),
            (self.rs_tracks / "tracks.json").read_text(),
        )
        # And the second listing (the decode-free path) answers the same.
        a2, b2 = self.both("/studio/tracks")
        self.assertEqual(self.parsed(a2), self.parsed(b2))
        self.assertEqual(self.parsed(a), self.parsed(a2))

    def test_02_page_serves_lean_and_validates(self) -> None:
        a, b = self.both("/")
        self.assertEqual(a[0], 200)
        self.assertEqual(a[2], b[2])
        self.assertEqual(a[1]["content-type"], b[1]["content-type"])
        self.assertEqual(a[1]["etag"], b[1]["etag"])
        self.assertEqual(
            a[1].get("content-security-policy"), b[1].get("content-security-policy")
        )
        self.assertIn(b'"vigil": "/studio/scene-audio/vigil"', a[2])
        self.assertIn(b'"storm": "/studio/scene-audio/storm"', a[2])
        self.assertIn(b"QUJD", a[2])  # the non-scene data URI survived
        a3, b3 = self.both("/", headers={"If-None-Match": a[1]["etag"]})
        self.assertEqual(a3[0], 304)
        self.assertEqual(a3[2], b3[2])

    def test_03_scene_audio_streams_with_ranges(self) -> None:
        full, rfull = self.both("/studio/scene-audio/vigil")
        self.assertEqual(full[0], 200)
        self.assertEqual(full[2], rfull[2])
        for rng in (
            "bytes=100-199",
            "bytes=-50",
            "bytes=2900-",
            "bytes=zz",
            "bytes=5-2",
        ):
            a, b = self.both("/studio/scene-audio/vigil", headers={"Range": rng})
            self.assertEqual(a[2], b[2], rng)
            self.assertEqual(a[1].get("content-range"), b[1].get("content-range"), rng)
            self.assertEqual(a[1].get("accept-ranges"), b[1].get("accept-ranges"), rng)
        a, b = self.both("/studio/scene-audio/nope")
        self.assertEqual(a[0], 404)
        self.assertEqual(self.parsed(a), self.parsed(b))
        a, b = self.both("/studio/scene-audio/%2e%2e")
        self.assertEqual(a[0], 404)
        self.assertEqual(self.parsed(a), self.parsed(b))

    def test_04_track_streams_by_id_and_extension(self) -> None:
        for path in ("/studio/track/t_alpha", "/studio/track/t_alpha.wav"):
            a, b = self.both(path)
            self.assertEqual(a[0], 200, path)
            self.assertEqual(a[2], b[2])
            self.assertEqual(a[1]["content-type"], b[1]["content-type"])
        a, b = self.both("/studio/track/t_alpha", headers={"Range": "bytes=0-99"})
        self.assertEqual(a[0], 206)
        self.assertEqual(a[2], b[2])
        a, b = self.both("/studio/track/nope")
        self.assertEqual(a[0], 404)
        self.assertEqual(self.parsed(a), self.parsed(b))

    def test_05_status_is_the_studio_marker(self) -> None:
        a, b = self.both("/api/status")
        self.assertEqual(self.parsed(a), {"studio": True})
        self.assertEqual(self.parsed(a), self.parsed(b))

    def test_06_the_api_alias_still_answers(self) -> None:
        a, b = self.both("/api/tracks")
        self.assertEqual(a[0], 200)
        self.assertEqual(self.parsed(a), self.parsed(b))

    def test_07_unknown_routes_answer_the_same_404(self) -> None:
        for path in ("/studio/nope", "/nope", "/studio/track/"):
            a, b = self.both(path)
            self.assertEqual(a[0], 404, path)
            self.assertEqual(self.parsed(a), self.parsed(b))

    def test_08_delete_takes_the_file_sources_and_manifest(self) -> None:
        a, b = self.both("/studio/tracks/t_del", method="DELETE")
        self.assertEqual(a[0], 200)
        self.assertEqual(self.parsed(a), self.parsed(b))
        for tracks in (self.py_tracks, self.rs_tracks):
            self.assertFalse((tracks / "t_del.wav").exists())
            self.assertFalse((tracks / "_src" / "t_del.orig.wav").exists())
            self.assertNotIn("t_del", (tracks / "tracks.json").read_text())
        self.assertEqual(
            (self.py_tracks / "tracks.json").read_text(),
            (self.rs_tracks / "tracks.json").read_text(),
        )
        a, b = self.both("/studio/tracks/t_del", method="DELETE")
        self.assertEqual(a[0], 404)
        self.assertEqual(self.parsed(a), self.parsed(b))


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class DeadCastle(StudioPair):
    """CASTLE_HOST names a port that refuses instantly — the relay's
    unreachable walk, without a single live socket."""

    HOST_ENV = "127.0.0.1:1"

    def parsed(self, raw: tuple[int, dict[str, str], bytes]) -> object:
        import json

        return json.loads(raw[2])

    def test_status_names_who_it_tried(self) -> None:
        a, b = self.both("/api/status")
        self.assertEqual(self.parsed(a), {"studio": True, "castle": "127.0.0.1:1"})
        self.assertEqual(self.parsed(a), self.parsed(b))

    def test_castle_verbs_report_unreachable(self) -> None:
        a, b = self.both("/api/stop", method="POST")
        self.assertEqual(a[0], 502)
        self.assertEqual(self.parsed(a), self.parsed(b))
        a, b = self.both("/api/files")
        self.assertEqual(a[0], 502)
        self.assertEqual(self.parsed(a), self.parsed(b))

    def test_a_typo_is_not_an_outage(self) -> None:
        a, b = self.both("/api/nonsense")
        self.assertEqual(a[0], 404)
        self.assertEqual(self.parsed(a), self.parsed(b))


if __name__ == "__main__":
    unittest.main()
