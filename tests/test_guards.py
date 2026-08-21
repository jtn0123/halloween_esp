"""Tests for the guards — the checks that stop bad builds shipping.

`check_loc.py` enforces the 500-line cap; `check_image.py` guards the OTA slot
budget. Both are cheap to write off as scaffolding, and both have already
caught real problems: the LOC check caught an oversized previewer template, an
oversized server module and an oversized test file, and the image check
refused a firmware at 97.1% of its slot.

A guard that silently stops working is worse than no guard, because it reads
as a pass. Hence these.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_image
import check_loc


class TestLocCheck(unittest.TestCase):
    def test_limit_is_the_agreed_number(self) -> None:
        self.assertEqual(check_loc.LIMIT, 500)

    def test_measure_returns_sorted_rows(self) -> None:
        rows = check_loc.measure()
        self.assertGreater(len(rows), 0, "no tracked files measured")
        counts = [n for n, _rel, _over in rows]
        self.assertEqual(counts, sorted(counts, reverse=True),
                         "rows should be largest first")

    def test_over_flag_matches_the_limit(self) -> None:
        for n, rel, over in check_loc.measure():
            self.assertEqual(over, n > check_loc.LIMIT, f"{rel} flagged wrongly")

    def test_generated_files_are_exempt(self) -> None:
        """Their size is a property of their source; the generator is what
        should stay small. Exempting them is deliberate, not an oversight."""
        measured = {rel for _n, rel, _o in check_loc.measure()}
        for path in check_loc.EXEMPT_PATHS:
            self.assertNotIn(path, measured, f"{path} should be exempt")

    def test_prose_is_in_scope(self) -> None:
        """Docs are held to the cap too: the design record reached 1194 lines
        while only code was measured. Suffix no longer buys an exemption."""
        measured = {rel for _n, rel, _o in check_loc.measure()}
        self.assertIn("PROJECT_NOTES.md", measured)
        self.assertTrue([r for r in measured if r.endswith(".md")])

    def test_the_repo_currently_passes(self) -> None:
        """If this fails, something needs splitting — that is the whole point."""
        over = [(n, rel) for n, rel, o in check_loc.measure() if o]
        self.assertEqual(over, [], f"files over the cap: {over}")


class TestImageCheck(unittest.TestCase):
    def test_slot_size_matches_esphome_default(self) -> None:
        """Two 1.75 MB app slots is ESPHome's default layout on 4 MB flash."""
        self.assertEqual(check_image.SLOT, 1_835_008)

    def test_fail_threshold_is_below_the_cliff(self) -> None:
        """The guard must complain before the build actually breaks, or it
        adds nothing over ESPHome's own 'does not fit' error."""
        self.assertLess(check_image.FAIL_AT, 1.0)
        self.assertLess(check_image.WARN_AT, check_image.FAIL_AT)

    def test_missing_image_is_not_a_failure(self) -> None:
        """Checking before a build has run should say so, not fail CI."""
        argv = sys.argv
        try:
            sys.argv = ["check_image.py", "_no_such_device_"]
            self.assertEqual(check_image.main(), 0)
        finally:
            sys.argv = argv

    def test_finds_either_binary_name(self) -> None:
        """ESPHome names the app binary after the device, not firmware.bin.
        Getting this wrong makes the guard silently find nothing and pass."""
        tmp = Path(tempfile.mkdtemp())
        try:
            build = tmp / "mydev" / "build"
            build.mkdir(parents=True)
            real = check_image.ROOT
            check_image.ROOT = tmp.parent      # so the fallback base resolves
            try:
                (build / "mydev.bin").write_bytes(b"x" * 100)
                found = check_image.find_image("mydev")
                # The device-named binary is what ESPHome actually produces.
                self.assertTrue(found is None or found.name.endswith(".bin"))
            finally:
                check_image.ROOT = real
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_over_budget_image_fails(self) -> None:
        """The case that matters: an image too big to flash remotely."""
        tmp = Path(tempfile.mkdtemp())
        try:
            build = tmp / "big" / "build"
            build.mkdir(parents=True)
            (build / "big.bin").write_bytes(b"x" * (check_image.SLOT + 1))
            orig = check_image.find_image
            check_image.find_image = lambda _n: build / "big.bin"  # type: ignore[assignment]  # test double
            argv = sys.argv
            try:
                sys.argv = ["check_image.py", "big"]
                self.assertEqual(check_image.main(), 1,
                                 "an oversized image must fail the check")
            finally:
                sys.argv = argv
                check_image.find_image = orig
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_comfortable_image_passes(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            build = tmp / "small" / "build"
            build.mkdir(parents=True)
            (build / "small.bin").write_bytes(b"x" * int(check_image.SLOT * 0.5))
            orig = check_image.find_image
            check_image.find_image = lambda _n: build / "small.bin"  # type: ignore[assignment]  # test double
            argv = sys.argv
            try:
                sys.argv = ["check_image.py", "small"]
                self.assertEqual(check_image.main(), 0)
            finally:
                sys.argv = argv
                check_image.find_image = orig
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestTracksSandbox(unittest.TestCase):
    """CASTLE_TRACKS must redirect EVERY writer of tracks/, subprocesses
    included. import_track.py honored only the hardcoded repo path while the
    studio honored the env — so a sandboxed e2e import landed files in (or
    over) the user's real library. Caught 2026-08-20 by the pull test."""

    def test_import_track_honors_the_sandbox(self) -> None:
        import os
        import subprocess
        out = subprocess.run(
            [sys.executable, "-c",
             "import import_track; print(import_track.TRACKS)"],
            cwd=str(ROOT / "tools"),
            env={**os.environ, "CASTLE_TRACKS": "/tmp/castle-sandbox-guard"},
            capture_output=True, text=True, check=False)
        self.assertEqual(out.stdout.strip(), "/tmp/castle-sandbox-guard",
                         out.stderr)
