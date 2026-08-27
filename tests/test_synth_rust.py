"""castle-core's RNG against numpy's default_rng, draw for draw — B3's gate.

render_audio.py seeds np.random.default_rng(zlib.crc32(scene_id)) and every
synth draws from it, so a Rust render can only match the Python one if the
random stream is identical. core/src/rng.rs carries numpy's SeedSequence +
PCG64 XSL-RR; this test holds it to numpy itself — raw u64s exactly, and
uniforms digit-for-digit with no tolerances.

uniform has a twist: numpy's C is `low + range * next_double`, and whether
the wheel's compiler fused that into one rounding depends on the platform
(arm64 clang fuses, baseline x86-64 does not). numpy_uniform_mode() probes
the numpy actually installed and tells the Rust dump which form to use, so
the comparison stays exact on every host instead of tolerant on some.

Skipped, not failed, without cargo — except in CI.
"""

from __future__ import annotations

import math
import os
import random
import shutil
import subprocess
import sys
import unittest
import zlib
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

CARGO = shutil.which("cargo")
IN_CI = bool(os.environ.get("CI"))
DUMP = ROOT / "core" / "target" / "release" / "synth_dump"

RANGES = [
    (-1.0, 1.0),
    (-0.03, 0.03),
    (70.0, 160.0),
    (0.25, 0.9),
    (6.0, 11.0),
    (0.15, 1.4),
    (1200.0, 2600.0),
    (0.0, 1.0),
]


def fmt(v: float) -> str:
    return repr(float(v))


def numpy_uniform_mode() -> str:
    """ "fma" or "plain" — whichever this numpy wheel compiled uniform into."""
    g = np.random.Generator(np.random.PCG64(999))
    want = [float(x) for x in g.uniform(0.15, 1.4, 64)]
    raws = [int(x) for x in np.random.PCG64(999).random_raw(64)]
    ds = [(r >> 11) * (1.0 / 9007199254740992.0) for r in raws]
    if want == [math.fma(1.4 - 0.15, d, 0.15) for d in ds]:
        return "fma"
    if want == [0.15 + (1.4 - 0.15) * d for d in ds]:
        return "plain"
    raise AssertionError(
        "this numpy's uniform matches neither the fused nor the plain form"
    )


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class TestSynthRngParity(unittest.TestCase):
    def test_seeded_streams_match_numpy_digit_for_digit(self) -> None:
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
        mode = numpy_uniform_mode()

        r = random.Random(int(os.environ.get("CASTLE_SYNTH_SEED", "7")))
        seeds: list[int] = [
            0,
            1,
            12345,
            2**63,
            2**96 + 17,
            *(zlib.crc32(s.encode()) for s in ("vigil", "storm", "heartbeat_hall")),
            *(r.randrange(2**128) for _ in range(6)),
        ]
        lines: list[str] = []
        want: list[str] = []
        for seed in seeds:
            lines.append(f"raw {seed} 200")
            want.append(
                " ".join(str(int(x)) for x in np.random.PCG64(seed).random_raw(200))
            )
            for lo, hi in RANGES:
                lines.append(f"uni {seed} {fmt(lo)} {fmt(hi)} 50 {mode}")
                g = np.random.Generator(np.random.PCG64(seed))
                want.append(" ".join(fmt(x) for x in g.uniform(lo, hi, 50)))

        run = subprocess.run(
            [str(DUMP)],
            input="\n".join(lines) + "\n",
            capture_output=True,
            text=True,
            check=True,
            timeout=120,
        )
        got = run.stdout.splitlines()
        self.assertEqual(len(got), len(want))
        checked = 0
        for i, (a, b) in enumerate(zip(want, got)):
            va = [float(x) for x in a.split()]
            vb = [float(x) for x in b.split()]
            self.assertEqual(va, vb, f"line {i} ({lines[i].split()[0]}) diverged")
            checked += len(va)
        # 13 seeds x (200 raws + 8 ranges x 50 uniforms)
        self.assertEqual(checked, len(seeds) * (200 + len(RANGES) * 50))


if __name__ == "__main__":
    unittest.main()
