"""castle-core (Rust) against the firmware header, bit for bit.

B1 of the typesafe plan: core/ re-implements the effect arithmetic that
exists in firmware/castle_effects.h (C++, float32) and web/src/effects.ts
(double). This gate holds the Rust copy to the strongest standard of the
three: the SAME f32 bits as the host-compiled C++, for the same seeded
corpus tests/cxx/parity_dump.cpp generates.

The C++ here is built with -ffp-contract=off, unlike test_firmware_cxx's
default build: clang on arm64 otherwise fuses a*b+c into fma, which the
ESP32-S2 (softfloat, one rounding per operation, no fma) never does — so
the un-fused build is the more device-faithful proxy, and it is what lets
the comparison demand exact bits instead of tolerances.

Skipped, not failed, where cargo or a host C++ compiler is missing —
except in CI, where losing either would silently retire the gate.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import rig_layout
import scene_schema

CORE = ROOT / "core"
CXX_SRC = ROOT / "tests" / "cxx" / "parity_dump.cpp"

CARGO = shutil.which("cargo")
COMPILER = shutil.which("clang++") or shutil.which("g++")
IN_CI = bool(os.environ.get("CI"))
SEED = os.environ.get("CASTLE_CORE_SEED", "7")


def f32_bits(v: float) -> int:
    """The float32 the value rounds to, as its bit pattern."""
    return int(struct.unpack("<I", struct.pack("<f", v))[0])


def rig_spec() -> str:
    """The rig as the Rust dump wants it, from the same source rig.h has.

    walk/fall go through the SAME %.6f quantisation gen_esphome bakes into
    generated/rig.h — the C++ side computes on the rounded values, so the
    Rust side must too."""
    doc = scene_schema.parse_show((ROOT / "scenes" / "scenes.yaml").read_text())
    per = int(doc["hardware"]["pixels_per_zone"])
    layouts = rig_layout.zone_layouts(doc["zones"], per)
    parts = []
    for z in doc["zones"]:
        lo = layouts[z["id"]]
        parts.append(
            ":".join(
                [
                    str(lo.n),
                    str(-1 if lo.center is None else lo.center),
                    str(lo.fall_steps),
                    ";".join(f"{v:.6f}" for v in lo.walk),
                    ";".join(f"{v:.6f}" for v in lo.fall),
                    "".join("1" if c else "0" for c in lo.core),
                ]
            )
        )
    return ",".join(parts)


def cargo(*args: str) -> subprocess.CompletedProcess[str]:
    assert CARGO is not None
    # --manifest-path goes right after the subcommand: anything after a
    # `--` separator belongs to the delegated tool, not to cargo.
    return subprocess.run(
        [CARGO, args[0], "--manifest-path", str(CORE / "Cargo.toml"), *args[1:]],
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )


@unittest.skipIf(
    (CARGO is None or COMPILER is None) and not IN_CI,
    "no cargo or no host C++ compiler",
)
class TestCastleCoreParity(unittest.TestCase):
    tmp: ClassVar[str]

    @classmethod
    def setUpClass(cls) -> None:
        assert CARGO is not None, (
            "cargo missing — required in CI (see module docstring)"
        )
        assert COMPILER is not None, "host C++ compiler missing — required in CI"
        cls.tmp = tempfile.mkdtemp(prefix="castle-core-")
        built = cargo("build", "--release", "--quiet")
        assert built.returncode == 0, f"cargo build failed:\n{built.stderr}"
        cxx = subprocess.run(
            [
                COMPILER,
                "-std=c++17",
                "-O1",
                "-ffp-contract=off",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-I",
                str(ROOT / "firmware"),
                str(CXX_SRC),
                "-o",
                f"{cls.tmp}/cxx_dump",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert cxx.returncode == 0, f"parity_dump.cpp failed to build:\n{cxx.stderr}"

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_rust_unit_tests_pass(self) -> None:
        r = cargo("test", "--quiet")
        self.assertEqual(r.returncode, 0, f"cargo test failed:\n{r.stdout}\n{r.stderr}")

    def test_rustfmt_is_clean(self) -> None:
        r = cargo("fmt", "--check")
        self.assertEqual(r.returncode, 0, f"rustfmt drift — run: cargo fmt\n{r.stdout}")

    def test_clippy_is_clean(self) -> None:
        r = cargo("clippy", "--quiet", "--all-targets", "--", "-D", "warnings")
        self.assertEqual(r.returncode, 0, f"clippy findings:\n{r.stderr}")

    def test_noise_lines_are_bit_exact(self) -> None:
        """Every noise-primitive line: same f32 bits from Rust and C++."""
        cxx = subprocess.run(
            [f"{self.tmp}/cxx_dump", SEED],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        rust = subprocess.run(
            [str(CORE / "target" / "release" / "parity_dump"), SEED],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        cxx_rows = [json.loads(ln) for ln in cxx.stdout.splitlines() if '"noise"' in ln]
        rust_rows = [
            json.loads(ln) for ln in rust.stdout.splitlines() if '"noise"' in ln
        ]
        self.assertEqual(len(cxx_rows), 400)
        self.assertEqual(len(rust_rows), 400)
        for i, (a, b) in enumerate(zip(cxx_rows, rust_rows)):
            for k in ("k", "a", "b", "c"):
                self.assertEqual(
                    a[k], b[k], f"seed {SEED} row {i}: corpus drift at {k!r}"
                )
            for k in ("hashi", "hash3", "x", "vnoise", "fbm"):
                self.assertEqual(
                    f32_bits(a[k]),
                    f32_bits(b[k]),
                    f"seed {SEED} row {i}: {k} differs — C++ {a[k]!r} vs Rust {b[k]!r}",
                )

    def test_effect_base_colours_are_bit_exact(self) -> None:
        """Every px line's base colour: the same f32 bits from both renders.

        The zone pixel counts are read from the C++ dump's own zone lines
        and handed to the Rust dump, so both walk the identical corpus."""
        cxx = subprocess.run(
            [f"{self.tmp}/cxx_dump", SEED],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        rows = [json.loads(ln) for ln in cxx.stdout.splitlines()]
        zones = sorted(
            (r for r in rows if r["kind"] == "zone"), key=lambda r: int(r["zi"])
        )
        spec = rig_spec()
        # The C++ geometry is compiled from generated/rig.h; ours is computed
        # from the same source (rig_layout over scenes.yaml). The zone lines
        # prove we are comparing like with like.
        for z, built in zip(zones, spec.split(",")):
            n, center, steps = built.split(":")[:3]
            self.assertEqual(
                (z["n"], z["center"], z["fall_steps"]),
                (int(n), int(center), int(steps)),
                "rig drift between generated/rig.h and rig_layout",
            )
        rust = subprocess.run(
            [str(CORE / "target" / "release" / "parity_dump"), SEED, "3000", spec],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        cxx_px = [r for r in rows if r["kind"] == "px"]
        rust_px = [json.loads(ln) for ln in rust.stdout.splitlines() if '"px"' in ln]
        self.assertEqual(len(cxx_px), len(rust_px))
        self.assertGreater(len(cxx_px), 2000, "corpus suspiciously small")
        for i, (a, b) in enumerate(zip(cxx_px, rust_px)):
            for k in ("eff", "pal", "soft", "zi", "p", "ov", "mode", "epoch"):
                self.assertEqual(
                    a[k], b[k], f"seed {SEED} px {i}: corpus drift at {k!r}"
                )
            for k in ("hue", "t", "seed"):
                self.assertEqual(
                    f32_bits(a[k]),
                    f32_bits(b[k]),
                    f"seed {SEED} px {i}: input {k} differs",
                )
            for key in ("base", "ovl"):
                for ch in range(4):
                    self.assertEqual(
                        f32_bits(a[key][ch]),
                        f32_bits(b[key][ch]),
                        f"seed {SEED} px {i} (eff {a['eff']}, ov {a['ov']}, "
                        f"t {a['t']}): {key}[{ch}] — "
                        f"C++ {a[key][ch]!r} vs Rust {b[key][ch]!r}",
                    )
            self.assertEqual(
                f32_bits(a["gate"]),
                f32_bits(b["gate"]),
                f"seed {SEED} px {i} (mode {a['mode']}): gate differs",
            )


if __name__ == "__main__":
    unittest.main()
