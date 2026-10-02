"""The card's free-space cache, executed: tests/cxx/sd_space_check.cpp
compiles firmware/sd_space.h with the host compiler and drives it the way
the handlers do.

Until v5.58 the cache expired on a 60 s timer, so about once a minute a
/api/status poll paid for esp_vfs_fat_info — f_getfree walks the whole FAT
when FSINFO is stale, which can take seconds on a 128 GB card, ON THE HTTPD
TASK. Every other request queued behind it and the browser called the castle
dead. Nothing but this firmware's own PUT/DELETE moves the number, so those
refresh it and the poll is three field copies.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

import cxx_compiler

SRC = ROOT / "tests" / "cxx" / "sd_space_check.cpp"
COMPILER = cxx_compiler.COMPILER  # g++ first on Windows; see the module
FLAGS = [
    "-std=c++17",
    "-O1",
    "-Wall",
    "-Wextra",
    "-Werror",
    "-I",
    str(ROOT / "tests" / "cxx" / "shim"),
    "-I",
    str(ROOT / "firmware"),
    "-I",
    str(ROOT / "firmware" / "generated"),
]
IN_CI = bool(os.environ.get("CI"))


@unittest.skipIf(COMPILER is None and not IN_CI, "no host C++ compiler")
class TestCardSpaceCache(unittest.TestCase):
    def test_only_a_writer_makes_the_firmware_read_the_card(self) -> None:
        if COMPILER is None:
            raise AssertionError("CI is set and no host C++ compiler is on PATH")
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "sd_space_check"
            built = subprocess.run(
                [COMPILER, *FLAGS, str(SRC), "-o", str(exe)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            run = subprocess.run(
                [str(exe)], capture_output=True, text=True, check=False
            )
        self.assertEqual(run.returncode, 0, run.stdout)
        self.assertIn("sd space OK", run.stdout)


class TestStatusDoesNotTimeOutTheCache(unittest.TestCase):
    def test_no_timer_is_left_in_the_status_path(self) -> None:
        """A grep, deliberately: the defect was a 60 s expiry read by
        h_status, and a re-introduced one would pass every behaviour test
        that runs in under a minute."""
        space = (ROOT / "firmware" / "sd_space.h").read_text(encoding="utf-8")
        self.assertNotIn("esp_timer_get_time", space)
        web = (ROOT / "firmware" / "sd_web.h").read_text(encoding="utf-8")
        self.assertNotIn("esp_vfs_fat_info", web)
        status = web.split("inline esp_err_t h_status(")[1].split("\ninline ")[0]
        self.assertIn("sd_space_kb(sd_total, sd_free)", status)
        self.assertNotIn("true", status.split("sd_space_kb(")[1].split(")")[0])


if __name__ == "__main__":
    unittest.main()


class TestCompilerChoice(unittest.TestCase):
    """tests/cxx_compiler.py: MinGW's g++ first on Windows, clang++ first
    everywhere else, and None rather than a guess when there is neither."""

    def test_the_order_follows_the_platform(self) -> None:
        have = {"g++": "/bin/g++", "clang++": "/bin/clang++"}
        with mock.patch.object(cxx_compiler.shutil, "which", have.get):
            self.assertEqual(cxx_compiler.find(windows=True), "/bin/g++")
            self.assertEqual(cxx_compiler.find(windows=False), "/bin/clang++")
        with mock.patch.object(cxx_compiler.shutil, "which", {"clang++": "c"}.get):
            self.assertEqual(cxx_compiler.find(windows=True), "c")
        with mock.patch.object(cxx_compiler.shutil, "which", {}.get):
            self.assertIsNone(cxx_compiler.find(windows=False))
