"""Compile the firmware's render path with a host compiler.

The C++ in firmware/ has never been built by anything except the ESP-IDF
toolchain, which means a syntax error or a changed signature is only found by
a full device build — minutes away, and only if someone happens to run one.

The render path in particular is now worth guarding: castle_pixels.h took the
per-pixel loop out of a YAML lambda, the overlays grew a `Fixture` argument,
and generated/rig.h is written fresh by `make generate`. None of that is
exercised by the Python or the browser suites.

This is a COMPILE check plus one call of each entry point, not a numeric
parity check against the browser — the effects are still kept in step with
web/src/effects.ts by hand. It catches the class of break that costs a
device build to discover, which is the cheap and worthwhile half.

Skipped, not failed, where no host C++ compiler exists.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "tests" / "cxx" / "render_check.cpp"
FIRMWARE = ROOT / "firmware"

COMPILER = shutil.which("clang++") or shutil.which("g++")


@unittest.skipIf(COMPILER is None, "no host C++ compiler")
class TestFirmwareRenderPath(unittest.TestCase):
    def test_compiles_clean_and_runs(self) -> None:
        """Warnings are errors here: the device build hides them in a wall of
        ESP-IDF output, so a warning that survives is a warning nobody reads."""
        self.assertTrue((FIRMWARE / "generated" / "rig.h").exists(),
                        "generated/rig.h missing — run `make generate` first")
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "render_check"
            build = subprocess.run(
                [str(COMPILER), "-std=c++17", "-Wall", "-Wextra", "-Werror",
                 "-I", str(FIRMWARE), str(SRC), "-o", str(binary)],
                capture_output=True, text=True, check=False)
            self.assertEqual(build.returncode, 0,
                             f"firmware render path did not compile:\n{build.stderr}")

            run = subprocess.run([str(binary)], capture_output=True, text=True,
                                 check=False)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("rendered ok", run.stdout)

    def test_every_zone_renders_its_own_pixel_count(self) -> None:
        """A zone whose Fixture disagrees with its strip length would write
        past the buffer on the device, where there is nothing to catch it."""
        rig = (FIRMWARE / "generated" / "rig.h").read_text()
        self.assertIn("inline constexpr Fixture RIG[", rig)
        self.assertIn("RIG_MAX_PIXELS", rig)
        # The buffer every lambda declares must cover the largest fixture.
        biggest = max(
            int(line.split("{", 1)[1].split(",", 1)[0])
            for line in rig.splitlines()
            if line.strip().startswith("{") and "rig_tables::" in line)
        declared = int(rig.split("RIG_MAX_PIXELS = ", 1)[1].split(";", 1)[0])
        self.assertGreaterEqual(declared, biggest)
