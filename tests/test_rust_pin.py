"""Every literal copy of the Rust pin agrees with core/rust-toolchain.toml.

The pin is the toolchain the show was rendered and verified with, and the
parity gates compare its float output bit for bit — but CI names it again in
each job that installs Rust, and nothing held those copies to the file
(grade report 2026-09-24 F2). release.yml reads the file at run time and
needs no check; the literals do.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from cargo_gate import pinned_channel

WORKFLOWS = ROOT / ".github" / "workflows"
LITERAL = re.compile(r'^\s*toolchain:\s*"([^"]+)"', re.MULTILINE)


class TestOnePin(unittest.TestCase):
    def test_every_workflow_literal_is_the_pinned_channel(self) -> None:
        pin = pinned_channel()
        seen = 0
        for wf in sorted(WORKFLOWS.glob("*.yml")):
            for found in LITERAL.findall(wf.read_text(encoding="utf-8")):
                seen += 1
                with self.subTest(workflow=wf.name):
                    self.assertEqual(found, pin)
        self.assertGreater(seen, 0, "no workflow names a toolchain any more")

    def test_the_crates_rust_version_is_the_pins_minor(self) -> None:
        text = (ROOT / "core" / "Cargo.toml").read_text(encoding="utf-8")
        m = re.search(r'^rust-version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        assert m is not None, "core/Cargo.toml has no rust-version"
        self.assertEqual(m.group(1), ".".join(pinned_channel().split(".")[:2]))


if __name__ == "__main__":
    unittest.main()
