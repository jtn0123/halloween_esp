"""requirements-desktop.lock agrees with requirements-desktop.txt.

The installer runs `uv pip sync --require-hashes` on this file and on
nothing else, so a constraint changed in the .txt and never re-locked would
be silently unenforced on every buyer's machine — the same gap
test_lock_deps.py closes for requirements.lock. No resolver runs here and
nothing is fetched: tools/lock_desktop.py's pure halves, and the file.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import lock_desktop as ld

PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^ ;\\]+)")


def entries() -> dict[str, tuple[str, str, list[str]]]:
    """{normalised name: (version, marker, hashes)} from the lock."""
    out: dict[str, tuple[str, str, list[str]]] = {}
    key = ""
    for line in ld.LOCK.read_text(encoding="utf-8").splitlines():
        m = PIN.match(line)
        if m:
            key = ld.norm(m[1])
            marker = line.split(" ; ", 1)[1].rstrip(" \\") if " ; " in line else ""
            out[key] = (m[2], marker, [])
        elif key and line.strip().startswith("--hash=sha256:"):
            out[key][2].append(line.strip().removesuffix("\\").strip())
    return out


class TestLockDesktop(unittest.TestCase):
    def setUp(self) -> None:
        self.lock = entries()

    def test_requirements_drop_the_standalone_tools(self) -> None:
        reqs = ld.requirements(
            "numpy~=2.5  # comment\n\nyt-dlp\nYT_DLP>=1\ndemucs==4.1.0\n"
        )
        self.assertEqual(reqs, ["numpy~=2.5", "demucs==4.1.0"])

    def test_project_names_only_the_supported_platforms(self) -> None:
        toml = ld.project_toml(['a ; sys_platform == "darwin"'])
        self.assertIn('"a ; sys_platform == \\"darwin\\""', toml)
        for env in ld.ENVIRONMENTS:
            self.assertIn(env, toml)

    def test_every_desktop_requirement_is_pinned_and_satisfied(self) -> None:
        text = (ROOT / "requirements-desktop.txt").read_text(encoding="utf-8")
        reqs = [Requirement(r) for r in ld.requirements(text)]
        self.assertGreaterEqual(len(reqs), 4)
        for req in reqs:
            with self.subTest(requirement=str(req)):
                entry = self.lock.get(ld.norm(req.name))
                self.assertIsNotNone(
                    entry, f"{req.name} missing — run `make lock-desktop`"
                )
                assert entry is not None
                self.assertTrue(
                    req.specifier.contains(Version(entry[0]), prereleases=True),
                    f"requirements-desktop.txt asks for {req}, the lock pins "
                    f"{entry[0]} — run `make lock-desktop`",
                )

    def test_every_pin_is_hashed_and_platform_scoped(self) -> None:
        self.assertNotIn("yt-dlp", self.lock, "yt-dlp ships as a standalone binary")
        for name, (version, marker, hashes) in self.lock.items():
            with self.subTest(package=name):
                self.assertTrue(version)
                self.assertTrue(hashes, "--require-hashes refuses an unhashed pin")
                self.assertTrue(marker, "every pin names the platforms it is for")
                self.assertNotIn("linux", marker)

    def test_torch_has_a_wheel_for_both_platforms(self) -> None:
        _version, marker, hashes = self.lock["torch"]
        self.assertIn("darwin", marker)
        self.assertIn("win32", marker)
        self.assertGreaterEqual(len(hashes), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
