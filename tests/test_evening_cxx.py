"""The evening show is the card's (v5.77), held equal across two languages.

Until v5.77 "start show" ran a generated script of four actions per scene
id, so the image named every scene in the yard's show — the two imported
songs included — and a castle sold with a card that does not carry them
would have walked into two "missing" scenes every round of the evening. The
list rides in the manifest now: the header's MARKS_EVENING bit and a place
number in each row's last byte (tools/scene_manifest.py), read by
castle_scenes::evening_csv and handed out one id per playlist pass by
evening_next (firmware/castle_scenes.h).

What is pinned here:

  * the C reads the evening the Python wrote — default (every non-motion
    scene, in file order), a `show.order` that reorders, and an evening with
    nobody in it — and both languages answer the same list;
  * a card from before v5.77 still has an evening on a v5.77 castle: every
    scene but the PIR's, which is what the compiled list used to be;
  * a place the writer never makes (past twelve, or claimed twice) is
    ignored the same way on both sides;
  * the playlist's walk — in order, wrapping, back to the top on "start
    show", and still in bounds when a publish shortens the list under it;
  * and the bytes: a v5.77 manifest differs from a v5.76 one ONLY in the two
    bytes v5.76 wrote as zero and never read, so an older castle reads it.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

import scene_manifest as sm
from test_scene_manifest_cxx import SceneRunnerCase


def show(*kinds: str) -> list[dict[str, Any]]:
    """Scenes s0, s1, ... of these kinds."""
    return [
        {"id": f"s{i}", "kind": k, "duration_ms": 1000, "base": {}}
        for i, k in enumerate(kinds)
    ]


def legacy(blob: bytes) -> bytes:
    """`blob` as a v5.76 writer left it: header flags and row bytes zero."""
    out = bytearray(blob)
    out[6:8] = b"\0\0"
    for i in range(out[5]):
        out[sm.HEADER.size + (i + 1) * sm.ENTRY.size - 1] = 0
    return bytes(out)


class TestTheBytes(unittest.TestCase):
    def test_only_the_bytes_an_older_castle_never_read_changed(self) -> None:
        scenes = show("ambient", "motion", "ambient")
        new = sm.encode(scenes)
        old = legacy(new)
        diff = [i for i, (a, b) in enumerate(zip(new, old, strict=True)) if a != b]
        row_tails = [sm.HEADER.size + (i + 1) * sm.ENTRY.size - 1 for i in range(3)]
        self.assertEqual(diff, [6, row_tails[0], row_tails[2]])
        self.assertEqual(sm.decode(old)[0]["evening"], None)
        self.assertEqual([r["evening"] for r in sm.decode(new)], [1, 0, 2])

    def test_the_order_the_show_names_is_checked(self) -> None:
        doc = {"scenes": show("ambient", "ambient"), "show": {"order": ["s1", "s0"]}}
        self.assertEqual(sm.evening_order(doc), ["s1", "s0"])
        with self.assertRaisesRegex(SystemExit, "unknown scene 'nope'"):
            sm.evening_order(dict(doc, show={"order": ["nope"]}))
        with self.assertRaisesRegex(SystemExit, "twice"):
            sm.evening_order(dict(doc, show={"order": ["s0", "s0"]}))


class TestTheCastleReadsTheEvening(SceneRunnerCase):
    def evening(self, blob: bytes, skip: str = "") -> list[str]:
        """Write `blob` as the card's manifest; ask the C, check the Python
        agrees, answer the list."""
        self.write_card(show(*["ambient"] * blob[5]), manifest=blob)
        line = self.one(f"evening:{skip}")
        c = [x for x in line.removeprefix("evening ").split(",") if x]
        self.assertEqual(c, sm.evening_ids(blob, skip), "C and Python differ")
        return c

    def test_every_scene_but_the_motion_one_by_default(self) -> None:
        blob = sm.encode(show("ambient", "motion", "triggered", "custom"))
        self.assertEqual(self.evening(blob), ["s0", "s2", "s3"])

    def test_the_shows_own_order_is_the_order_it_plays(self) -> None:
        scenes = show("ambient", "ambient", "motion")
        blob = sm.encode(scenes, ["s2", "s0"])
        self.assertEqual(self.evening(blob), ["s2", "s0"])

    def test_an_evening_with_nobody_in_it_is_empty(self) -> None:
        self.assertEqual(self.evening(sm.encode(show("motion", "motion"))), [])

    def test_a_card_from_before_577_plays_all_but_the_pirs_scene(self) -> None:
        blob = legacy(sm.encode(show("ambient", "motion", "ambient")))
        self.assertEqual(self.evening(blob, skip="s1"), ["s0", "s2"])
        self.assertEqual(self.evening(blob, skip=""), ["s0", "s1", "s2"])

    def test_a_place_the_writer_never_makes_is_ignored(self) -> None:
        out = bytearray(sm.encode(show("ambient", "ambient", "ambient")))
        tail = [sm.HEADER.size + (i + 1) * sm.ENTRY.size - 1 for i in range(3)]
        out[tail[0]], out[tail[1]], out[tail[2]] = 2, 13, 2  # s2 claims s0's
        self.assertEqual(self.evening(bytes(out)), ["s0"])

    def test_no_manifest_is_no_evening_from_the_card(self) -> None:
        self.assertEqual(self.one("evening:"), "evening ")


class TestThePlaylistWalk(SceneRunnerCase):
    def test_in_order_and_round_again(self) -> None:
        got = self.run_ops("set:a,b,c", "next", "next", "next", "next")
        self.assertEqual(got, ["set n=3", "next a", "next b", "next c", "next a"])

    def test_start_show_starts_at_the_top(self) -> None:
        got = self.run_ops("set:a,b,c", "next", "next", "rewind", "next")
        self.assertEqual(got[-1], "next a")

    def test_a_publish_that_shortens_the_evening_stays_in_bounds(self) -> None:
        got = self.run_ops("set:a,b,c", "next", "next", "set:x,y", "next", "next")
        self.assertEqual(got[-3:], ["set n=2", "next x", "next y"])

    def test_an_empty_evening_hands_out_nothing(self) -> None:
        self.assertEqual(self.run_ops("set:", "next"), ["set n=0", "next "])


if __name__ == "__main__":
    unittest.main()
