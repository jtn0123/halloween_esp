"""The safety floor: the traversal guards, the crash-safe writes, and the
scene validation that keeps a bad request from corrupting scenes.yaml.

Each of these pins a fix to a specific way the studio could previously lose
or expose data: a path escaping its directory, a write interrupted halfway,
a subprocess with no timeout, a splice that corrupted the show. (The audit
that found them predates the reports kept in `.claude/`, so there is no item
to cite — the list above is the citation.) If one starts failing, the hole
is open again.

Two thirds of it moved when the Python studio retired
(docs/RETIREMENT.md): the id sanitiser is
`core/src/studio_import.rs`'s and is tested there, and the server-side
guards — a traversal id on refresh, import and compare, a scene write that
does not parse or lies about its own id — are
`tests/test_studio_import_rs.py`, `tests/test_studio_media_rs.py` and
`tests/golden/scene_errors.json`. What is left is the manifest's own
crash-safety, which was never the server's.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import manifest as mf


class TestManifestSafety(unittest.TestCase):
    """B5: tracks.json can no longer be silently lost."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._patch = mock.patch.object(mf, "PATH", self.tmp / "tracks.json")
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_corrupt_manifest_is_moved_aside_not_erased(self) -> None:
        mf.PATH.write_text('{"chant": {truncated')
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(mf.load(), {})
        self.assertIn("moved to tracks.json.corrupt-", out.getvalue())
        survivors = list(self.tmp.glob("tracks.json.corrupt-*"))
        self.assertEqual(len(survivors), 1, "the damaged file must survive")
        self.assertIn("truncated", survivors[0].read_text())

    def test_save_is_atomic_write_then_rename(self) -> None:
        mf.record("chant", source="file:/x")
        self.assertFalse(
            mf.PATH.with_suffix(".tmp").exists(),
            "the temp file must not be left behind",
        )
        self.assertIn("chant", json.loads(mf.PATH.read_text()))

    def test_record_after_corruption_keeps_the_evidence(self) -> None:
        """The old failure: corrupt -> load()=={} -> next save persists {}.
        Now the corrupt original is still on disk to recover from."""
        mf.PATH.write_text("not json at all")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            mf.record("fresh", source="file:/y")
        self.assertIn("WARNING", out.getvalue())
        self.assertEqual(list(json.loads(mf.PATH.read_text())), ["fresh"])
        self.assertTrue(list(self.tmp.glob("tracks.json.corrupt-*")))


if __name__ == "__main__":
    unittest.main()
