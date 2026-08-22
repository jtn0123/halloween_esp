"""Compile and run the firmware's render path with a host compiler.

The C++ in firmware/ is otherwise only built by the ESP-IDF toolchain, which
means a syntax error or a changed signature is found by a full device build —
minutes away, and only if someone happens to run one. And the device has
nothing that would catch a write past a zone's buffer, a NaN turning a pixel
white, or a centre role surviving a blackout.

Two host programs, both in tests/cxx/:

  render_check.cpp   the invariant harness — every effect/overlay/gate entry
                     point, every fixture in generated/rig.h plus the rest of
                     the catalogue, canary bytes around each zone's buffer,
                     0..255 and finite at t=0, t=10^7 s, negative t and the
                     parameter extremes; the strike envelope's exact curve.
  parity_dump.cpp    the numeric dump web/test/firmware_parity.mjs compares
                     against the TypeScript port. Here it is only built and
                     smoke-run; the comparison lives on the node side.

Skipped, not failed, where no host C++ compiler exists.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parent.parent
CXX_DIR = ROOT / "tests" / "cxx"
FIRMWARE = ROOT / "firmware"

COMPILER = shutil.which("clang++") or shutil.which("g++")
FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", "-I", str(FIRMWARE)]


def build(src: Path, out: Path) -> subprocess.CompletedProcess[str]:
    assert COMPILER is not None
    return subprocess.run([COMPILER, *FLAGS, str(src), "-o", str(out)],
                          capture_output=True, text=True, check=False)


#: Locally a missing compiler is a skip. In CI it is a failure: the runner
#: image losing g++ would otherwise turn the only firmware-executing tests
#: into a green tick that tests nothing.
IN_CI = bool(os.environ.get("CI"))


@unittest.skipIf(COMPILER is None and not IN_CI, "no host C++ compiler")
class TestFirmwareRenderPath(unittest.TestCase):
    tmp: ClassVar[str]
    check: ClassVar[Path]
    dump: ClassVar[Path]
    built: ClassVar[subprocess.CompletedProcess[str]]
    built_dump: ClassVar[subprocess.CompletedProcess[str]]

    @classmethod
    def setUpClass(cls) -> None:
        if COMPILER is None:
            raise AssertionError("CI is set and no host C++ compiler (clang++/g++) "
                                 "is on PATH — the firmware harness must run in CI")
        cls.tmp = tempfile.mkdtemp()
        cls.check = Path(cls.tmp) / "render_check"
        cls.dump = Path(cls.tmp) / "parity_dump"
        cls.built = build(CXX_DIR / "render_check.cpp", cls.check)
        cls.built_dump = build(CXX_DIR / "parity_dump.cpp", cls.dump)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_generated_rig_header_exists(self) -> None:
        self.assertTrue((FIRMWARE / "generated" / "rig.h").exists(),
                        "generated/rig.h missing — run `make generate` first")

    def test_render_check_compiles_warning_free(self) -> None:
        """Warnings are errors here: the device build hides them in a wall of
        ESP-IDF output, so a warning that survives is a warning nobody reads."""
        self.assertEqual(self.built.returncode, 0,
                         f"render_check.cpp did not compile:\n{self.built.stderr}")

    def test_parity_dump_compiles_warning_free(self) -> None:
        self.assertEqual(self.built_dump.returncode, 0,
                         f"parity_dump.cpp did not compile:\n{self.built_dump.stderr}")

    def test_invariants_hold_for_two_seeds(self) -> None:
        """Buffer canaries, finite 0..255 output, identity of unknown ids, the
        exact strike-envelope curve, blackout really dark — see the harness."""
        self.assertEqual(self.built.returncode, 0, self.built.stderr)
        for seed in ("1234", "424242"):
            run = subprocess.run([str(self.check), seed], capture_output=True,
                                 text=True, check=False)
            self.assertEqual(run.returncode, 0,
                             f"seed {seed}:\n{run.stdout}\n{run.stderr}")
            self.assertIn("rendered ok", run.stdout)
            checks = int(run.stdout.split("rendered ok, ")[1].split(" checks")[0])
            self.assertGreater(checks, 500_000, "the harness lost most of its checks")

    def test_parity_dump_is_well_formed(self) -> None:
        """Every line parses; every zone in rig.h is described; every effect,
        overlay and strike mask appears; every value is finite and in 0..1."""
        self.assertEqual(self.built_dump.returncode, 0, self.built_dump.stderr)
        run = subprocess.run([str(self.dump), "11", "1500"], capture_output=True,
                             text=True, check=False)
        self.assertEqual(run.returncode, 0, run.stderr)
        rows = [json.loads(line) for line in run.stdout.splitlines()]
        zones = [r for r in rows if r["kind"] == "zone"]
        rig = (FIRMWARE / "generated" / "rig.h").read_text()
        declared = int(rig.split("inline constexpr Fixture RIG[", 1)[1].split("]", 1)[0])
        self.assertEqual(len(zones), declared)
        px = [r for r in rows if r["kind"] == "px"]
        self.assertEqual({r["eff"] for r in px}, set(range(13)))
        self.assertEqual({r["ov"] for r in px}, {0, 1, 2, 3})
        self.assertEqual({r["mode"] for r in px}, {0, 1, 2, 3})
        self.assertEqual({r["pal"] for r in px}, {0, 1, 2, 3})
        for r in px:
            for key in ("base", "ovl"):
                for v in r[key]:
                    self.assertTrue(0.0 <= v <= 1.0, f"{key} {v} in {r}")
            self.assertTrue(0.0 <= r["gate"] <= 1.0, r)
            self.assertLess(r["p"], zones[r["zi"]]["n"])

    def test_every_zone_renders_its_own_pixel_count(self) -> None:
        """A zone whose Fixture disagrees with its strip length would write
        past the buffer on the device, where there is nothing to catch it."""
        rig = (FIRMWARE / "generated" / "rig.h").read_text()
        self.assertIn("inline constexpr Fixture RIG[", rig)
        self.assertIn("RIG_MAX_PIXELS", rig)
        biggest = max(
            int(line.split("{", 1)[1].split(",", 1)[0])
            for line in rig.splitlines()
            if line.strip().startswith("{") and "rig_tables::" in line)
        declared = int(rig.split("RIG_MAX_PIXELS = ", 1)[1].split(";", 1)[0])
        self.assertGreaterEqual(declared, biggest)
