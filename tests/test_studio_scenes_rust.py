"""The Rust studio's scenes group against the Python's — B5 pass 3.

Validation errors must be BYTE-identical (both servers ask
tools/scene_check.py, so the strings have one home). Splices and removals
are compared as parsed bodies minus the log — the logs carry each
sandbox's own paths — and then the logs themselves are compared with those
paths masked. The artifacts are the real proof: after every rebuild the
two build directories' audio must match byte for byte, and the two
scenes.yaml files must be identical text.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from studio_rust_case import CARGO, IN_CI, StudioPair, scenes_fixture

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

from check_loc import SCENE_LIMIT

TINY = (
    "  - id: {sid}\n"
    "    name: Trial\n"
    "    kind: ambient\n"
    "    volume: 0.4\n"
    "    duration_ms: {ms}\n"
    "    base: {{towerL: candle, towerR: candle, door: ember}}\n"
    "    score:\n"
    "      - {{t: 0, synth: toll, gain: 0.4}}\n"
    "    cues: []"
)

JSON_HDRS = {"Content-Type": "application/json"}


def tiny(sid: str, ms: int = 1200) -> str:
    return TINY.format(sid=sid, ms=ms)


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class SceneRoutes(StudioPair):
    def post_scene(
        self, obj: dict[str, object]
    ) -> tuple[tuple[int, dict[str, str], bytes], tuple[int, dict[str, str], bytes]]:
        return self.both("/studio/scene", "POST", JSON_HDRS, json.dumps(obj).encode())

    def assert_bodies_match(
        self,
        a: tuple[int, dict[str, str], bytes],
        b: tuple[int, dict[str, str], bytes],
    ) -> None:
        """Parsed-equal minus the log; the logs equal once paths are masked."""
        da, db = self.parsed(a), self.parsed(b)
        assert isinstance(da, dict) and isinstance(db, dict)
        la, lb = str(da.pop("log", "")), str(db.pop("log", ""))
        self.assertEqual(da, db)
        self.assertEqual(self.masked(la, "py"), self.masked(lb, "rs"))

    def assert_artifacts_match(self) -> None:
        self.assertEqual(self.py_scenes.read_text(), self.rs_scenes.read_text())
        py_audio = sorted((self.py_build / "audio").glob("*"))
        rs_audio = sorted((self.rs_build / "audio").glob("*"))
        self.assertEqual([p.name for p in py_audio], [p.name for p in rs_audio])
        for pa, pb in zip(py_audio, rs_audio, strict=True):
            if pa.is_file():
                self.assertEqual(pa.read_bytes(), pb.read_bytes(), f"{pa.name} differs")

    def test_01_validation_speaks_with_one_voice(self) -> None:
        cases: list[dict[str, object]] = [
            {"id": "x", "yaml": ""},
            {"id": "x", "yaml": "nonsense: ["},
            {"id": "x", "yaml": tiny("y")},
            {"id": "bad", "yaml": "  - id: bad\n    kind: ambient"},
        ]
        for obj in cases:
            a, b = self.post_scene(obj)
            self.assertEqual(a[0], 400, str(obj))
            self.assertEqual(a[2], b[2], str(obj))
        a, b = self.both("/studio/scene", "POST", JSON_HDRS, b"{nope")
        self.assertEqual(a[0], 400)
        pa, pb = self.parsed(a), self.parsed(b)
        assert isinstance(pa, dict) and isinstance(pb, dict)
        for d in (pa, pb):
            self.assertTrue(
                str(d["error"]).startswith("request body is not valid JSON")
            )

    def test_02_splice_new_scene_rebuilds_everything(self) -> None:
        a, b = self.post_scene({"id": "trial", "yaml": tiny("trial")})
        self.assertEqual(a[0], 200, a[2][:400])
        self.assert_bodies_match(a, b)
        d = self.parsed(a)
        assert isinstance(d, dict)
        self.assertFalse(d["replaced"])
        self.assertIn("trial", d["scenes"])
        self.assert_artifacts_match()

    def test_03_replace_then_remove_with_the_scene(self) -> None:
        a, b = self.post_scene({"id": "storm", "yaml": tiny("storm", ms=900)})
        self.assertEqual(a[0], 200, a[2][:400])
        self.assert_bodies_match(a, b)
        d = self.parsed(a)
        assert isinstance(d, dict)
        self.assertTrue(d["replaced"])
        self.assert_artifacts_match()
        # The orphan leg: no such track file, but the scene goes out.
        a, b = self.both("/studio/tracks/trial?scene=1", method="DELETE")
        self.assertEqual(a[0], 200, a[2][:400])
        self.assert_bodies_match(a, b)
        d = self.parsed(a)
        assert isinstance(d, dict)
        self.assertTrue(d["file_missing"])
        self.assertTrue(d["scene_removed"])
        self.assertNotIn("trial", d["scenes"])
        self.assert_artifacts_match()

    def test_04_the_rebuild_button(self) -> None:
        a, b = self.both("/studio/rebuild", "POST", JSON_HDRS, b"{}")
        self.assertEqual(a[0], 200, a[2][:400])
        self.assert_bodies_match(a, b)
        self.assert_artifacts_match()

    def test_05_the_thirteenth_scene_is_refused_the_same_way(self) -> None:
        """A show already at the ceiling: both servers must turn the next
        scene away with the same sentence, and neither may touch the file
        (grade report 2026-08-31 A8). Last of the five because it rewrites both
        sandboxes' scenes.yaml to a full show and does not put them back —
        nothing after it would be measuring the fixture any more.

        The Rust studio has no count of its own: it asks
        tools/scene_check.py, which is where the ceiling lives. This test is
        what would notice if that delegation were ever replaced by a port.
        """
        full = (
            scenes_fixture().split("\nscenes:\n", 1)[0]
            + "\nscenes:\n"
            + "\n".join(tiny(f"s{i}") + "\n" for i in range(SCENE_LIMIT))
        )
        for path in (self.py_scenes, self.rs_scenes):
            path.write_text(full)
        a, b = self.post_scene({"id": "one_too_many", "yaml": tiny("one_too_many")})
        self.assertEqual(a[0], 400, a[2][:400])
        self.assertEqual(b[0], 400, b[2][:400])
        self.assertEqual(a[2], b[2], "the two servers refuse differently")
        d = self.parsed(a)
        assert isinstance(d, dict)
        self.assertIn("the show is full", str(d["error"]))
        self.assertEqual(self.py_scenes.read_text(), full, "python edited the file")
        self.assertEqual(self.rs_scenes.read_text(), full, "rust edited the file")


if __name__ == "__main__":
    unittest.main()
