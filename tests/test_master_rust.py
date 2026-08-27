"""castle-core's master chain against render_audio's, bit for bit — B3.

limit, the loop crossfade, the end fade, normalisation and the int16
quantise: the last deterministic steps between a mixed scene and the WAV
lame encodes. synth.limit was moved off np.convolve in the same change
this gate arrived (BLAS dot order is a vendor choice; cumsum differences
are defined-order), so the Python here is itself newly portable and the
Rust is held to it exactly — no filters involved, so no kernel modes,
just the uniform-form probe.

Skipped, not failed, without cargo — except in CI.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from synth_probes import numpy_uniform_mode

CARGO = shutil.which("cargo")
IN_CI = bool(os.environ.get("CI"))
DUMP = ROOT / "core" / "target" / "release" / "synth_dump"
SR = 44100


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class TestMasterChainParity(unittest.TestCase):
    def test_every_master_step_matches_bit_for_bit(self) -> None:
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
        import synth

        umode = numpy_uniform_mode()
        cases = [
            ("limit", 41, 120_000),
            ("limit", 42, 9_000),
            ("loop", 43, 200_000),
            ("loop", 44, 50_000),
            ("fade", 45, 200_000),
            ("norm", 46, 30_000),
            ("wav", 47, 30_000),
        ]
        lines = [f"master {kind} {seed} {n} {umode}" for kind, seed, n in cases]
        run = subprocess.run(
            [str(DUMP)],
            input="\n".join(lines) + "\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        got = run.stdout.splitlines()
        self.assertEqual(len(got), len(cases))
        for (kind, seed, n), reply in zip(cases, got):
            amp = 1.6 if kind == "limit" else 0.8
            g = np.random.default_rng(seed)
            buf = g.uniform(-amp, amp, n)
            if kind == "limit":
                buf = synth.limit(buf)
            elif kind == "loop":
                xf = min(int(0.6 * SR), len(buf) // 4)
                head = buf[:xf].copy()
                ramp = np.linspace(0.0, 1.0, xf)
                buf[-xf:] = buf[-xf:] * (1.0 - ramp) + head * ramp
            elif kind == "fade":
                fade = min(int(0.25 * SR), len(buf))
                buf[-fade:] *= np.linspace(1.0, 0.0, fade)
            elif kind == "norm":
                peak = float(np.max(np.abs(buf)))
                buf *= 0.89 / peak
            if kind == "wav":
                pcm = (np.clip(buf, -1.0, 1.0) * 32767.0).astype("<i2")
                crc, cnt = reply.split()
                self.assertEqual(int(cnt), len(pcm))
                self.assertEqual(int(crc, 16), zlib.crc32(pcm.tobytes()), (kind, seed))
                continue
            out = np.asarray(buf, dtype="<f8")
            crc, cnt, *probes = reply.split()
            self.assertEqual(int(cnt), len(out), (kind, seed))
            stride = max(1, len(out) // 16)
            self.assertEqual(
                [float(v) for v in probes],
                [float(out[i]) for i in range(0, len(out), stride)],
                f"probe diverged: {kind} {seed}",
            )
            self.assertEqual(int(crc, 16), zlib.crc32(out.tobytes()), (kind, seed))


if __name__ == "__main__":
    unittest.main()
