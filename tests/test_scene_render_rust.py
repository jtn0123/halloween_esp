"""The scene_render bin against render_audio.render_scene, byte for byte —
the B3 swap's gate.

The bin takes one JSON scene spec on stdin and writes the WAV the flash
build embeds plus the marker JSON the cue generators read. Here the SAME
scene renders through the Python reference and through the bin, with the
host's probed kernel modes passed in so the comparison is exact on every
platform — and once more through the bin's CANONICAL default profile,
pinned by crc32, which is what production renders use and why a scene is
now the same bytes on every machine.

Skipped, not failed, without cargo — except in CI.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import helpers  # noqa: F401  (hermetic env)
import render_audio as ra
from helpers import make_click_track
from synth_probes import kernel_modes, numpy_uniform_mode

CARGO = shutil.which("cargo")
IN_CI = bool(os.environ.get("CI"))
BIN = ROOT / "core" / "target" / "release" / "scene_render"

#: crc32 of the WAV the canonical profile renders for CANON_SCENE — the
#: cross-machine determinism pin. A change here means the render itself
#: changed, which must be a deliberate, listened-to decision.
CANON_CRC = "b812f2a6"

#: The wheel profile that render was pinned against (macOS arm64) — the
#: same six characters as Modes::CANONICAL. Off it, the Rust render is
#: still canonical; only the Python comparison below is skipped.
CANON_MODES = "101211"

CANON_SCENE: dict[str, Any] = {
    "id": "vigil",
    "duration_ms": 5000,
    "reverb": 0.15,
    "score": [
        {"synth": "toll", "t": 0.5, "gain": 0.8},
        {"synth": "heartbeat", "t": 0.0, "dur": 4.0, "take": 2.0},
    ],
}


def spec_of(scene: dict[str, Any], out: Path) -> dict[str, Any]:
    """The bin's stdin — render_audio's own builder, the single home."""
    return ra.scene_spec(scene, {"sample_rate": 44100}, out)


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class TestSceneRenderParity(unittest.TestCase):
    tmp: Path

    @classmethod
    def setUpClass(cls) -> None:
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
        cls.tmp = Path(tempfile.mkdtemp(prefix="scene-render-"))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def rust(
        self, scene: dict[str, Any], probed: bool = True
    ) -> tuple[bytes, dict[str, list[list[float]]]]:
        out = self.tmp / f"rs_{scene['id']}.wav"
        spec = spec_of(scene, out)
        if probed:
            spec["modes"] = kernel_modes()
            spec["umode"] = numpy_uniform_mode()
        run = subprocess.run(
            [str(BIN)],
            input=json.dumps(spec),
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        return out.read_bytes(), json.loads(run.stdout)

    def python(
        self, scene: dict[str, Any]
    ) -> tuple[bytes, dict[str, list[list[float]]]]:
        buf, markers = ra.render_scene_py(scene, {"sample_rate": 44100})
        out = self.tmp / f"py_{scene['id']}.wav"
        ra.write_wav(out, buf, 44100)
        return out.read_bytes(), markers

    def held_equal(self, scene: dict[str, Any]) -> None:
        wav_rs, marks_rs = self.rust(scene)
        wav_py, marks_py = self.python(scene)
        self.assertEqual(wav_rs, wav_py, f"{scene['id']}: WAV bytes diverged")
        # Values AND key order: markers.json is written in this order.
        self.assertEqual(marks_rs, marks_py, scene["id"])
        self.assertEqual(list(marks_rs), list(marks_py), scene["id"])

    def test_a_synth_score_with_reverb_and_a_take(self) -> None:
        self.held_equal(CANON_SCENE)

    def test_a_looped_scene_through_the_filtered_voices(self) -> None:
        self.held_equal(
            {
                "id": "storm",
                "duration_ms": 3000,
                "loop": True,
                "score": [
                    {"synth": "wind", "t": 0.0, "dur": 2.5, "gain": 0.6},
                    {"synth": "toll", "t": 1.0, "gain": 0.4},
                ],
            }
        )

    def test_an_imported_track_scene_markers_pans_and_all(self) -> None:
        lib = self.tmp / "lib"
        lib.mkdir(exist_ok=True)
        make_click_track(lib / "click.wav", seconds=2.0)
        scene: dict[str, Any] = {
            "id": "ballad",
            "duration_ms": 3000,
            "audio_file": "tracks/click.wav",
            "track_gain": 0.9,
            "track_at": 0.25,
            "sensitivity": {"low": 0.8, "mid": 1.1},
            "score": [{"synth": "toll", "t": 1.0, "gain": 0.3}],
        }
        with mock.patch.dict(os.environ, {"CASTLE_TRACKS": str(lib)}):
            self.held_equal(scene)

    def test_an_unknown_synth_fails_with_the_pythons_sentence(self) -> None:
        scene = {"id": "x", "duration_ms": 1000, "score": [{"synth": "zzz", "t": 0}]}
        run = subprocess.run(
            [str(BIN)],
            input=json.dumps(spec_of(scene, self.tmp / "x.wav")),
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
        self.assertEqual(run.returncode, 1)
        with self.assertRaises(SystemExit) as cm:
            ra.render_scene_py(scene, {"sample_rate": 44100})
        self.assertEqual(run.stderr.strip(), str(cm.exception))

    def test_the_canonical_profile_is_pinned(self) -> None:
        """No modes in the spec → the render profile every machine shares.
        On the reference platform it equals the Python bytes too."""
        wav, _ = self.rust(CANON_SCENE, probed=False)
        self.assertEqual(f"{zlib.crc32(wav):08x}", CANON_CRC)
        if kernel_modes() == CANON_MODES and numpy_uniform_mode() == "fma":
            wav_py, _ = self.python(CANON_SCENE)
            self.assertEqual(wav, wav_py)


if __name__ == "__main__":
    unittest.main()
