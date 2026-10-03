"""Every gzip this repo's Python writes says `mtime=`.

Without it gzip stamps the current second into bytes 4-7 of the header, so
the same input compresses to different bytes a second later. That was the
2026-10-03 Windows CI flake: tests/firmware_web_harness.py seeds one card
per castle, the two calls straddled a second, and the C castle and the
emulator served `/` with one header byte apart. The card's site page had
the same churn on every publish. This walks the trees
tests/test_text_encoding.py walks, so a new writer cannot bring it back.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TREES = ("tools", "tests", "demo/castle-radio")
#: The writers that take `mtime=`. gzip.open has no such parameter, and
#: nothing here writes through it.
WRITERS = {"compress", "GzipFile"}


def stamped(call: ast.Call) -> bool:
    """True for a gzip writer whose header would carry the clock."""
    func = call.func
    if isinstance(func, ast.Attribute):
        # zlib.compress, lzma.compress: no gzip header, so no clock in it.
        ours = isinstance(func.value, ast.Name) and func.value.id == "gzip"
        if not ours or func.attr not in WRITERS:
            return False
    elif not (isinstance(func, ast.Name) and func.id == "GzipFile"):
        return False  # a bare compress() could be anyone's
    return not any(kw.arg in ("mtime", None) for kw in call.keywords)


def offenders(source: str, name: str) -> list[str]:
    return [
        f"{name}:{node.lineno}: {ast.unparse(node)[:70]}"
        for node in ast.walk(ast.parse(source, name))
        if isinstance(node, ast.Call) and stamped(node)
    ]


class TestGzipMtime(unittest.TestCase):
    def test_every_gzip_writer_pins_its_mtime(self) -> None:
        found = [
            hit
            for tree in TREES
            for path in sorted((ROOT / tree).rglob("*.py"))
            for hit in offenders(
                path.read_text(encoding="utf-8"), str(path.relative_to(ROOT))
            )
        ]
        self.assertEqual(found, [], "pass mtime=0:\n" + "\n".join(found))

    def test_the_walk_sees_each_spelling(self) -> None:
        bad = "gzip.compress(b)\ngzip.compress(b, 9)\ngzip.GzipFile(f, 'wb')\n"
        bad += "GzipFile(fileobj=f, mode='wb')\n"
        self.assertEqual(len(offenders(bad, "x.py")), 4)
        good = (
            "gzip.compress(b, mtime=0)\n"
            "gzip.GzipFile(f, 'wb', mtime=0)\n"
            "zlib.compress(b)\n"
            "compress(b)\n"
            "gzip.compress(b, **opts)\n"
        )
        self.assertEqual(offenders(good, "x.py"), [])


if __name__ == "__main__":
    unittest.main()
