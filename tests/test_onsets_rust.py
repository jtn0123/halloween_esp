"""castle-core's onset detection against analyze.py, hit for hit — B3.

The whole detection chain — defined-order STFT, log-compressed flux,
FIR smoothing, median-adaptive thresholds, peak picking — compared as
complete band dictionaries: every time and every velocity equal, across
signal shapes (noise bursts, the waltz, a heartbeat) and sensitivities.
numpy's reduction orders (pairwise sums, sequential axis-0 adds, the
scaled complex abs) are all reproduced on the Rust side, so there is
nothing to be tolerant about.

Skipped, not failed, without cargo — except in CI.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from synth_probes import kernel_modes, numpy_uniform_mode

CARGO = shutil.which("cargo")
IN_CI = bool(os.environ.get("CI"))
DUMP = ROOT / "core" / "target" / "release" / "synth_dump"


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class TestOnsetParity(unittest.TestCase):
    def test_band_dictionaries_match_hit_for_hit(self) -> None:
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
        self.assertEqual(built.returncode, 0, built.stderr)
        import analyze
        import synth

        modes = kernel_modes()
        umode = numpy_uniform_mode()

        def burst(seed: int, n: int) -> np.ndarray:
            base = np.random.default_rng(seed).uniform(-1.0, 1.0, n)
            factors = np.where((np.arange(n) // 2000) % 9 == 0, 1.0, 0.05)
            return np.asarray(base * factors)

        cases: list[tuple[str, float, float, np.ndarray]] = [
            ("burst 201 250000", 1.1, 0.0, burst(201, 250000)),
            ("burst 202 180000", 0.6, 0.0, burst(202, 180000)),
            ("waltz - -", 1.1, 0.0, np.asarray(synth.waltz()[0])),
            ("waltz - -", 3.0, 0.0, np.asarray(synth.waltz()[0])),
            (
                "heartbeat 10.0 17",
                1.1,
                0.0,
                np.asarray(synth.heartbeat(10.0, np.random.default_rng(17))[0]),
            ),
        ]
        lines = [
            f"onsets {spec.split()[0]} "
            f"{spec.split()[1] if spec.split()[1] != '-' else 0} "
            f"{spec.split()[2] if spec.split()[2] != '-' else 0} "
            f"{sens} {umode} {modes}"
            for spec, sens, _, _ in cases
        ]
        run = subprocess.run(
            [str(DUMP)],
            input="\n".join(lines) + "\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )
        got = run.stdout.splitlines()
        self.assertEqual(len(got), len(cases))
        for (spec, sens, _, x), reply in zip(cases, got):
            want = analyze.analyze(x, sensitivity=sens)
            got_bands: dict[str, list[tuple[float, float]]] = {}
            for chunk in reply.split(";"):
                if not chunk:
                    continue
                name, _, pts = chunk.partition(">")
                got_bands[name] = [
                    (float(a), float(b))
                    for a, b in (p.split(":") for p in pts.split(",") if p)
                ]
            self.assertEqual(got_bands, want, f"{spec} sens={sens}")
            self.assertTrue(want, f"{spec}: the reference found nothing to compare")

    def test_analyze_full_matches_levels_and_pans_included(self) -> None:
        """What the importer actually calls: onsets grown a pan third
        element when stereo is known, and level envelopes for any band
        with no beat — rows equal, element for element."""
        assert CARGO is not None
        subprocess.run(
            [
                CARGO,
                "build",
                "--release",
                "--quiet",
                "--manifest-path",
                str(ROOT / "core" / "Cargo.toml"),
            ],
            capture_output=True,
            check=True,
            timeout=300,
        )
        import analyze
        import synth

        modes = kernel_modes()
        umode = numpy_uniform_mode()

        def burst(seed: int, n: int) -> np.ndarray:
            base = np.random.default_rng(seed).uniform(-1.0, 1.0, n)
            factors = np.where((np.arange(n) // 2000) % 9 == 0, 1.0, 0.05)
            return np.asarray(base * factors)

        cases: list[tuple[str, float, bool, np.ndarray]] = [
            ("burst 203 220000", 1.1, True, burst(203, 220000)),
            # sens 3.0: onsets AND levels mixed; sens 5.0: all-envelope
            ("drone 12.0 0", 3.0, False, np.asarray(synth.drone(12.0))),
            ("drone 12.0 0", 5.0, True, np.asarray(synth.drone(12.0))),
            (
                "heartbeat 12.0 19",
                1.1,
                True,
                np.asarray(synth.heartbeat(12.0, np.random.default_rng(19))[0]),
            ),
        ]
        lines = [
            f"full {spec} {sens} {1 if st else 0} {umode} {modes}"
            for spec, sens, st, _ in cases
        ]
        run = subprocess.run(
            [str(DUMP)],
            input="\n".join(lines) + "\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )
        got = run.stdout.splitlines()
        self.assertEqual(len(got), len(cases))
        for (spec, sens, st, x), reply in zip(cases, got):
            stereo = (x, x[::-1]) if st else None
            want = {
                k: [list(r) for r in v]
                for k, v in analyze.analyze_full(
                    x, sensitivity=sens, stereo=stereo
                ).items()
            }
            got_bands: dict[str, list[list[float]]] = {}
            for chunk in reply.split(";"):
                if not chunk:
                    continue
                name, _, pts = chunk.partition(">")
                got_bands[name] = [
                    [float(v) for v in p.split(":")] for p in pts.split(",") if p
                ]
            self.assertEqual(got_bands, want, f"{spec} stereo={st}")
            if "drone" in spec:
                self.assertTrue(
                    any(k.startswith("level_") for k in want),
                    f"the drone at sens={sens} should have envelope bands",
                )

    def test_analyze_file_matches_through_the_same_ffmpeg(self) -> None:
        """The whole importer path on a real file: both sides run the
        identical ffmpeg decode, then the identical detection — so a WAV
        rendered from the show comes back with equal band dictionaries."""
        assert CARGO is not None
        subprocess.run(
            [
                CARGO,
                "build",
                "--release",
                "--quiet",
                "--manifest-path",
                str(ROOT / "core" / "Cargo.toml"),
            ],
            capture_output=True,
            check=True,
            timeout=300,
        )
        if shutil.which("ffmpeg") is None and not IN_CI:
            self.skipTest("no ffmpeg")
        import tempfile

        import analyze
        import render_audio
        import synth

        tmp = Path(tempfile.mkdtemp(prefix="onsets-file-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        wavs: list[tuple[Path, float, bool]] = []
        buf = np.asarray(synth.waltz()[0])
        buf = buf * (0.89 / float(np.max(np.abs(buf))))
        render_audio.write_wav(tmp / "waltz.wav", buf, 44100)
        wavs.append((tmp / "waltz.wav", 1.1, True))
        d = np.asarray(synth.drone(12.0))
        d = d * (0.89 / float(np.max(np.abs(d))))
        render_audio.write_wav(tmp / "drone.wav", d, 44100)
        wavs.append((tmp / "drone.wav", 3.0, False))
        lines = [f"file {p} {sens} {1 if st else 0}" for p, sens, st in wavs]
        run = subprocess.run(
            [str(DUMP)],
            input="\n".join(lines) + "\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=300,
        )
        got = run.stdout.splitlines()
        self.assertEqual(len(got), len(wavs))
        for (p, sens, st), reply in zip(wavs, got):
            x = analyze.load_audio(p)
            stereo = analyze.load_stereo(p) if st else None
            want = {
                k: [list(r) for r in v]
                for k, v in analyze.analyze_full(
                    x, sensitivity=sens, stereo=stereo
                ).items()
            }
            got_bands: dict[str, list[list[float]]] = {}
            for chunk in reply.split(";"):
                if not chunk:
                    continue
                name, _, pts = chunk.partition(">")
                got_bands[name] = [
                    [float(v) for v in pt.split(":")] for pt in pts.split(",") if pt
                ]
            self.assertEqual(got_bands, want, p.name)
            self.assertTrue(want, f"{p.name}: nothing detected to compare")


if __name__ == "__main__":
    unittest.main()
