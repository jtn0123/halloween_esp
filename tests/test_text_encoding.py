"""Every text file this repo's Python opens names its encoding.

Without `encoding=`, open()/read_text()/write_text() use the locale's
encoding: UTF-8 on macOS, cp1252 on a Windows PC — where scenes.yaml's em
dashes and any song title past Latin-1 either turn to mojibake or raise.
Ruff's PLW1514 (pyproject.toml) catches the calls whose receiver it can see
is a Path; this walks the same three trees for the rest (`p.read_text()` on
a variable, `(a / b).open("a")`), so the rule holds for every call.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TREES = ("tools", "tests", "demo/castle-radio")
#: `<module>.open(...)` that is not a text-file open (os.open takes flags,
#: gzip/tarfile/zipfile/wave are binary by default, webbrowser opens a URL).
NOT_FILE_OPENS = {"os", "gzip", "tarfile", "zipfile", "webbrowser", "wave", "io"}


def _mode(call: ast.Call, index: int) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == "mode":
            return kw.value
    return call.args[index] if len(call.args) > index else None


def unencoded(call: ast.Call) -> bool:
    """True for a text-mode open/read_text/write_text with no encoding=."""
    if any(kw.arg in ("encoding", None) for kw in call.keywords):
        return False  # named, or **kwargs this cannot see into
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr in ("read_text", "write_text"):
        return True
    if isinstance(func, ast.Name) and func.id == "open":
        mode = _mode(call, 1)
    elif isinstance(func, ast.Attribute) and func.attr == "open":
        if isinstance(func.value, ast.Name) and func.value.id in NOT_FILE_OPENS:
            return False
        mode = _mode(call, 0)
    else:
        return False
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return "b" not in mode.value
    return mode is None


def offenders(source: str, name: str) -> list[str]:
    tree = ast.parse(source, name)
    return [
        f"{name}:{node.lineno}: {ast.unparse(node)[:70]}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and unencoded(node)
    ]


class TestTextEncoding(unittest.TestCase):
    def test_every_text_open_names_its_encoding(self) -> None:
        found = [
            hit
            for tree in TREES
            for path in sorted((ROOT / tree).rglob("*.py"))
            for hit in offenders(
                path.read_text(encoding="utf-8"), str(path.relative_to(ROOT))
            )
        ]
        self.assertEqual(found, [], "pass encoding='utf-8':\n" + "\n".join(found))

    def test_the_walk_sees_what_ruff_cannot(self) -> None:
        bad = (
            "p.read_text()\n"
            "p.write_text(s)\n"
            "(a / b).open('a')\n"
            "open(path)\n"
            "open(path, 'w')\n"
            "q.open(mode='r')\n"
        )
        self.assertEqual(len(offenders(bad, "bad.py")), 6)

    def test_binary_and_non_file_opens_pass(self) -> None:
        fine = (
            "p.read_text(encoding='utf-8')\n"
            "open(path, 'rb')\n"
            "p.open('wb')\n"
            "os.open(path, flags)\n"
            "webbrowser.open(url)\n"
            "open(path, **kw)\n"
            "open(path, mode)\n"
        )
        self.assertEqual(offenders(fine, "fine.py"), [])


if __name__ == "__main__":
    unittest.main()
