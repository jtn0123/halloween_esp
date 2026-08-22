"""The safety floor: the traversal guards, the crash-safe writes, and the
scene validation that keeps a bad request from corrupting scenes.yaml.

Each of these pins a fix to a specific way the studio could previously lose
or expose data (grade report items E1, E2, B3, B4, B5). If one starts
failing, the hole is open again.
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
import studio
from studio_case import ServerCase


class TestIdGuards(unittest.TestCase):
    """E1: the write-side id sanitiser."""

    def test_safe_ids_pass(self) -> None:
        for tid in ("chant", "organ_loop", "a1_b2_c3"):
            self.assertEqual(studio.safe_id(tid), tid)

    def test_traversal_and_junk_die(self) -> None:
        for tid in ("../../audio/01_vigil", "a/b", "a\\b", "a.b", "", "  ",
                    "x;rm -rf", "a b"):
            self.assertIsNone(studio.safe_id(tid), tid)


class TestServerGuards(ServerCase):
    """The HTTP boundary refuses what the sanitiser refuses."""

    def test_refresh_rejects_a_traversal_id(self) -> None:
        code, out = self.post_json("/api/refresh",
                                   {"id": "../../audio/01_vigil"})
        self.assertEqual(code, 400)
        self.assertIn("error", out)

    def test_import_rejects_a_traversal_id(self) -> None:
        code, out = self.post_json(
            "/api/import", {"url": "https://example.com/x", "id": "../evil"})
        self.assertEqual(code, 400)
        self.assertIn("error", out)

    def test_compare_strips_a_traversal_id_to_nothing(self) -> None:
        """E2: '../../<real file elsewhere>' must NOT resolve and stream."""
        code, out = self.post_json("/api/compare",
                                   {"id": "../../audio/01_vigil"})
        self.assertEqual(code, 404)
        self.assertEqual(out.get("error"), "no such track")

    def test_scene_write_refuses_invalid_yaml(self) -> None:
        """B4: a block that does not parse must never reach the file."""
        code, out = self.post_json(
            "/api/scene", {"id": "x", "yaml": "  - id: x\n   broken: ["})
        self.assertEqual(code, 400)
        self.assertIn("YAML", out["error"])

    def test_scene_write_refuses_a_lying_id(self) -> None:
        """B4: the block must be the one scene it claims to be."""
        code, out = self.post_json(
            "/api/scene", {"id": "x", "yaml": "  - id: y\n    name: n"})
        self.assertEqual(code, 400)
        self.assertIn("expected exactly one scene", out["error"])


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
        self.assertFalse(mf.PATH.with_suffix(".tmp").exists(),
                         "the temp file must not be left behind")
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
