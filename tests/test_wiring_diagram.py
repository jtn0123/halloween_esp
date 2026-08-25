"""Smoke tests for the wiring-schematic generator.

The generator splices an SVG body into docs/castle-wiring.html in place. Two
properties matter and neither needs a browser: running it twice must change
nothing (the splice is find-and-replace, so a drifted marker would duplicate
or destroy the body), and no drawn text may start outside the viewBox (the
"5 V SPUR" label once ended 5 px past the right edge and lost its R).
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "gen_wiring_diagram.py"
PAGE = ROOT / "docs" / "castle-wiring.html"


def _run(page: Path) -> None:
    subprocess.run([sys.executable, str(SCRIPT), str(page)],
                   check=True, capture_output=True)


class TestWiringDiagram(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.page = self.tmp / "castle-wiring.html"
        shutil.copy(PAGE, self.page)

    def test_splice_is_idempotent(self) -> None:
        _run(self.page)
        once = self.page.read_bytes()
        _run(self.page)
        self.assertEqual(once, self.page.read_bytes(),
                         "a second run must be byte-identical")

    def test_exactly_one_svg_body_survives(self) -> None:
        _run(self.page)
        html = self.page.read_text()
        self.assertEqual(html.count("</svg>"), 1)
        self.assertEqual(html.count('id="schem"'), 1)

    def test_no_text_starts_outside_the_viewbox(self) -> None:
        _run(self.page)
        html = self.page.read_text()
        m = re.search(r'viewBox="0 0 (\d+) (\d+)"', html)
        assert m is not None, "schematic viewBox missing"
        w, h = int(m.group(1)), int(m.group(2))
        svg = html[html.index('id="schem"'):html.index("</svg>")]
        for tm in re.finditer(r'<text[^>]*\bx="([\d.]+)"[^>]*\by="([\d.]+)"', svg):
            x, y = float(tm.group(1)), float(tm.group(2))
            self.assertTrue(0 <= x <= w, f"text x={x} outside 0..{w}")
            self.assertTrue(0 <= y <= h, f"text y={y} outside 0..{h}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
