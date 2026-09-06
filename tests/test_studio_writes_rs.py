"""The studio's write surface — scene surgery, deletion, the rebuild.

This is what `tests/test_studio_scene_edit.py`, the delete-with-scene half
of `tests/test_studio_tracks_api.py` and the rebuild cases of
`tests/test_studio_api.py` became when `tools/studio.py` retired
(docs/RETIREMENT.md phase 3). Those drove the Python server in-process and
mocked its subprocess calls; this drives the real binary and reads the
result off disk, which is the same claim made from further back: the file
was spliced as TEXT (comments intact), the generators ran in order, and a
refusal touched nothing.

The refusal STRINGS are frozen in `tests/golden/scene_errors.json` and
replayed by `tests/test_studio_golden.py`; what is here is the file on
disk afterwards.

Skipped, not failed, without cargo — except in CI.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

from check_loc import SCENE_LIMIT
from studio_rs_case import CARGO, IN_CI, StudioCase, scenes_fixture, seed_library


def block(sid: str, **over: object) -> str:
    """A scene block that passes scene_schema, for overriding per test.
    The base is empty on purpose — an empty map of zones is valid, and it
    keeps the block short enough to read in a failure message."""
    s: dict[str, Any] = {
        "name": sid,
        "kind": "triggered",
        "duration_ms": 1000,
        "base": {},
    }
    s.update(over)
    return f"  - id: {sid}\n" + "".join(
        f"    {k}: {json.dumps(v)}\n" for k, v in s.items()
    )


def show_of(*ids: str) -> str:
    """The repo's real preamble plus one tiny scene per id."""
    head = scenes_fixture().split("\nscenes:\n", 1)[0]
    return head + "\nscenes:\n" + "".join(block(i) + "\n" for i in ids)


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class SceneEditing(StudioCase):
    """scenes.yaml is spliced as text, against the sandbox's own copy."""

    ORIGINAL = (
        scenes_fixture().split("\nscenes:\n", 1)[0]
        + "\nscenes:\n"
        + "  # a comment that must survive\n"
        + block("vigil")
        + "\n"
        + block("storm", duration_ms=2000)
    )

    def setUp(self) -> None:
        self.scenes.write_text(self.ORIGINAL)
        self.scenes.with_suffix(".yaml.bak").unlink(missing_ok=True)

    def post(self, sid: str, yaml: str) -> tuple[int, dict[str, Any]]:
        return self.json("/studio/scene", "POST", {"id": sid, "yaml": yaml})

    def test_needs_both_an_id_and_a_block(self) -> None:
        for req in ({}, {"id": "x"}, {"yaml": "  - id: x\n"}):
            code, d = self.json("/studio/scene", "POST", req)
            self.assertEqual(code, 400, req)
            self.assertIn("need id and yaml", d["error"])
        self.assertEqual(
            self.scenes.read_text(),
            self.ORIGINAL,
            "a rejected request still edited the file",
        )

    def test_replaces_an_existing_scene_in_place(self) -> None:
        code, d = self.post("storm", block("storm", duration_ms=9999))
        self.assertEqual(code, 200, d)
        self.assertEqual(d["id"], "storm")
        self.assertTrue(d["replaced"])
        text = self.scenes.read_text()
        self.assertIn("duration_ms: 9999", text)
        self.assertNotIn("duration_ms: 2000", text)
        self.assertEqual(text.count("- id: storm"), 1, "the scene was duplicated")

    def test_leaves_the_surrounding_file_alone(self) -> None:
        """Comments carry the reasoning behind the show; a YAML round-trip
        would erase them, which is why this splices text."""
        self.post("storm", block("storm", duration_ms=3))
        text = self.scenes.read_text()
        self.assertIn("# a comment that must survive", text)
        self.assertIn("- id: vigil", text)
        self.assertIn("THE CEILING", text, "the preamble's own notes survived")

    def test_appends_a_scene_it_has_not_seen(self) -> None:
        code, d = self.post("brand_new", block("brand_new"))
        self.assertEqual(code, 200, d)
        self.assertFalse(d["replaced"])
        text = self.scenes.read_text()
        self.assertIn("- id: brand_new", text)
        self.assertLess(text.index("- id: storm"), text.index("- id: brand_new"))
        # The row's "in the show" badge reads this, so it must be the list
        # as of AFTER the write.
        self.assertEqual(d["scenes"], ["vigil", "storm", "brand_new"])

    def test_the_answer_says_which_of_the_two_happened(self) -> None:
        """ "Make scene" is a button whose whole effect is in a file you
        cannot see from the page. Without this the panel can only say
        "written", which reads exactly like nothing having happened."""
        _c, added = self.post("brand_new", block("brand_new"))
        self.assertFalse(added["replaced"])
        _c, again = self.post("brand_new", block("brand_new", duration_ms=2))
        self.assertTrue(again["replaced"])

    def test_rewriting_the_same_scene_twice_changes_nothing(self) -> None:
        """ "Update scene" on an unchanged track should be a no-op in git,
        not a whitespace diff that has to be explained."""
        self.post("storm", block("storm", duration_ms=7))
        once = self.scenes.read_text()
        self.post("storm", block("storm", duration_ms=7))
        self.assertEqual(self.scenes.read_text(), once)

    def test_a_write_keeps_the_previous_show_beside_it(self) -> None:
        """A crash mid-write must never be able to truncate the show."""
        self.post("storm", block("storm", duration_ms=11))
        self.assertEqual(
            self.scenes.with_suffix(".yaml.bak").read_text(), self.ORIGINAL
        )
        self.assertFalse(self.scenes.with_suffix(".yaml.tmp").exists())

    def test_a_block_that_is_not_a_scene_is_refused_with_every_reason(self) -> None:
        """Parses, names itself right, and is still wrong: an unknown
        effect and a cue past the end. These used to splice cleanly and
        fail inside the re-render (grade report 2026-08-21 B4); now the
        list comes back as a 400 and the file is untouched."""
        before = self.build_stamp()
        code, d = self.post(
            "storm",
            block(
                "storm",
                duration_ms=500,
                base={"door": "glow"},
                cues=[{"t": 900, "op": "set", "zone": "door", "effect": "ember"}],
            ),
        )
        self.assertEqual(code, 400)
        self.assertIn("storm", d["error"])
        joined = "\n".join(d["errors"])
        self.assertIn("unknown effect 'glow'", joined)
        self.assertIn("past the scene's duration_ms", joined)
        self.assertEqual(self.scenes.read_text(), self.ORIGINAL)
        self.assertFalse(self.scenes.with_suffix(".yaml.bak").exists())
        self.assertEqual(self.build_stamp(), before, "a refused scene rebuilt")

    def build_stamp(self) -> list[tuple[str, int]]:
        """What the build directory holds, by name and mtime — a rebuild
        that must not have happened is a directory that did not move."""
        return sorted(
            (str(p.relative_to(self.build)), p.stat().st_mtime_ns)
            for p in self.build.rglob("*")
            if p.is_file()
        )

    #: A show already at the board's ceiling — SCENE_LIMIT scenes, built
    #: from the constant so raising it here cannot silently pass this by.
    FULL = show_of(*(f"s{i}" for i in range(SCENE_LIMIT)))

    def test_a_thirteenth_scene_is_refused_before_anything_is_written(self) -> None:
        """`make check` already fails a thirteenth scene, but the desk is
        where scenes get written — and discovering the ceiling as a red
        pre-commit hook means discovering it with scenes.yaml already
        edited and the show already re-rendered (grade report 2026-08-31 A8)."""
        self.scenes.write_text(self.FULL)
        before = self.build_stamp()
        code, d = self.post("one_too_many", block("one_too_many"))
        self.assertEqual(code, 400)
        self.assertIn("the show is full", d["error"])
        self.assertIn(str(SCENE_LIMIT), d["error"])
        self.assertIn("one_too_many", d["error"])
        self.assertEqual(
            d["errors"], [f"scene ceiling: {SCENE_LIMIT}/{SCENE_LIMIT} scenes"]
        )
        self.assertEqual(self.scenes.read_text(), self.FULL, "the file was edited")
        self.assertFalse(
            self.scenes.with_suffix(".yaml.bak").exists(), "a .bak was written"
        )
        self.assertEqual(self.build_stamp(), before, "a refused scene rebuilt")

    def test_a_full_show_still_takes_a_replacement(self) -> None:
        """The ceiling counts scenes, not writes: re-saving a scene that is
        already in the show never grows it, and a full show is exactly when
        the operator is most likely to be editing rather than adding."""
        self.scenes.write_text(self.FULL)
        _code, d = self.post("s0", block("s0", duration_ms=4242))
        self.assertTrue(d["replaced"], d)
        self.assertIn("duration_ms: 4242", self.scenes.read_text())
        self.assertEqual(len(d["scenes"]), SCENE_LIMIT)


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class Rebuilding(StudioCase):
    """The chain a write triggers: render, cues, preview — and what the
    operator is told when one of them stops."""

    def setUp(self) -> None:
        self.scenes.write_text(scenes_fixture())

    def test_the_rebuild_runs_the_three_generators_in_order(self) -> None:
        code, d = self.json("/studio/rebuild", "POST", {})
        self.assertEqual(code, 200, d)
        self.assertTrue(d["ok"])
        log = self.masked(d["log"])
        self.assertIn("markers.json", log)
        self.assertTrue((self.build / "audio" / "01_vigil.mp3").exists())
        self.assertTrue((self.build / "firmware" / "generated").is_dir())
        self.assertTrue(
            (self.build / "previewer" / "castle-cue-desk.html").stat().st_size > 1000
        )
        # And it says out loud that it wrote beside the sandbox rather than
        # into the repo — "the audio re-rendered" with the repo untouched
        # reads as a lie otherwise.
        self.assertIn("sandbox: rendered under <BUILD>", log)

    def test_the_rebuild_stops_at_the_first_failing_step(self) -> None:
        """A failed render used to be followed by gen_esphome and
        gen_previewer anyway, and the operator's one-line reason came from
        the previewer's SUCCESS line — "Scene write failed — wrote 11
        scenes…" (judge B, JB2-3)."""
        code, d = self.json(
            "/studio/scene",
            "POST",
            {
                "id": "jb_drop",
                "yaml": block("jb_drop", audio_file="tracks/jb_drop.mp3"),
            },
        )
        self.assertEqual(code, 500, d)
        self.assertFalse(d["ok"])
        self.assertIn("no such audio_file tracks/jb_drop.mp3", d["log"])
        self.assertIn("render_audio.py failed", d["log"])
        # The previewer never ran, so its SUCCESS line cannot be what the
        # operator is shown as the cause.
        self.assertNotIn("castle-cue-desk.html", d["log"])
        self.assertNotIn("\n", d["reason"], "the reason is one line, not a traceback")
        self.assertIn("render_audio.py", d["reason"])


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class DeleteWithScene(StudioCase):
    """Deleting a track that is IN THE SHOW: the scene goes with it, or a
    later render fails for a reason the operator did not cause (JB1-6)."""

    def setUp(self) -> None:
        """Each case gets the library and the show back: the sandbox is
        class-scoped and these tests delete out of it."""
        self.scenes.write_text(show_of("vigil", "t_del"))
        seed_library(self.tracks)

    def test_a_plain_delete_leaves_the_scene_alone(self) -> None:
        before = self.scenes.read_text()
        code, d = self.json("/studio/tracks/t_del", "DELETE")
        self.assertEqual(code, 200, d)
        self.assertNotIn("scene_removed", d)
        self.assertEqual(self.scenes.read_text(), before)
        self.assertFalse((self.tracks / "t_del.wav").exists())

    def test_delete_with_scene_removes_the_block_and_rebuilds(self) -> None:
        code, d = self.json("/studio/tracks/t_del?scene=1", "DELETE")
        self.assertEqual(code, 200, d)
        self.assertTrue(d["removed"])
        self.assertTrue(d["scene_removed"])
        self.assertEqual(d["scenes"], ["vigil"])
        self.assertNotIn("- id: t_del", self.scenes.read_text())
        self.assertIn("- id: t_del", self.scenes.with_suffix(".yaml.bak").read_text())
        self.assertFalse((self.tracks / "t_del.wav").exists())
        self.assertFalse((self.tracks / "_src" / "t_del.orig.wav").exists())
        self.assertNotIn("t_del", json.loads((self.tracks / "tracks.json").read_text()))

    def test_delete_with_scene_removes_an_orphan_whose_file_is_gone(self) -> None:
        (self.tracks / "t_del.wav").unlink()
        code, d = self.json("/studio/tracks/t_del", "DELETE")
        self.assertEqual(code, 404, "a plain delete of nothing is not a success")
        code, d = self.json("/studio/tracks/t_del?scene=1", "DELETE")
        self.assertEqual(code, 200, d)
        self.assertTrue(d["file_missing"])
        self.assertTrue(d["scene_removed"])
        self.assertNotIn("t_del", d["scenes"])

    def test_delete_with_scene_of_a_track_not_in_the_show_is_harmless(self) -> None:
        code, d = self.json("/studio/tracks/t_beta?scene=1", "DELETE")
        self.assertEqual(code, 200, d)
        self.assertTrue(d["removed"])
        self.assertFalse(d["scene_removed"])
        self.assertEqual(d["scenes"], ["vigil", "t_del"])


if __name__ == "__main__":
    unittest.main()
