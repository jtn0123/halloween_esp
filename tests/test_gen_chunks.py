"""No script the device runs is long — the crash of 2026-08-21, kept shut.

ESPHome walks a script's action chain RECURSIVELY to stop or poll it, one
stack frame per action, on an 8 KB loop task. `run_scene` stops the running
scene before starting the next, so the 457-action Citizens scene panicked the
castle on EVERY scene switch (v5.27). The fix then was arithmetic: split a
scene into a head plus `cont_<id>_N` continuations of at most
`gen_esphome.CHUNK` actions, and these tests pinned the split.

v5.67 removed the cause instead of the symptom. A scene is not a script any
more — it is a cue file on the card, walked 16 ms at a time by an interval
(tools/gen_scene_cards.py, firmware/castle_cues.h), and the one generic
`scene_run` that plays it is two actions long whatever the scene is. So the
chunker, the continuations and `CHUNK` are gone, and what is left to defend is
the PROPERTY they were bought for: nothing the device can be asked to stop has
a chain deep enough to matter. That is now a ceiling over every script in the
firmware, generated or hand-written, because the next long chain will not
arrive in a scene — it will arrive in a playlist or a self-test that somebody
added a step to.

The ceiling is ACTION_CEILING below. It is not a measured stack limit; it is
"an order of magnitude under the chain that crashed", which is the only kind
of margin worth having when the failure mode is a panic on a Halloween night.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import gen_esphome as ge
import yaml
from test_gen_esphome import OUTPUT_PATHS, EsphomeLoader

ZONES = [{"id": "towerL"}, {"id": "towerR"}, {"id": "door"}]

#: The chain that crashed was 457 actions deep. Fifty is the largest a script
#: in this firmware has ever been (`show_playlist`, four actions per scene),
#: and 64 leaves room for the twelfth scene without leaving room for a
#: timeline. A script over this is not a number to raise — it is a chain that
#: wants to become data, the way a scene did.
ACTION_CEILING = 64


def long_scene(n: int, loop: bool = False) -> dict[str, Any]:
    """A scene with `n` cues — 300 of them is the Citizens scene's order of
    magnitude, and the whole point is that it no longer changes any script."""
    return {
        "id": "epic",
        "name": "Epic",
        "kind": "triggered",
        "loop": loop,
        "duration_ms": n * 100 + 500,
        "base": {"towerL": "candle"},
        "cues": [
            {"t": 100 * (i + 1), "op": "strike", "intensity": 0.5} for i in range(n)
        ],
    }


def generate(scenes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """Run the whole generator into a temporary tree; answer its scripts and
    the names of the card files it wrote. Every output path is redirected,
    because the real ones are tracked files and audio/card/ is a publish
    directory the user syncs to the castle."""
    doc = {"zones": ZONES, "hardware": {"pixels_per_zone": 7}, "scenes": scenes}
    with (
        tempfile.TemporaryDirectory() as td,
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.ExitStack() as stack,
    ):
        tmp = Path(td)
        (tmp / "generated").mkdir()
        for n in OUTPUT_PATHS:
            stack.enter_context(
                mock.patch.object(ge, n, tmp / "generated" / Path(getattr(ge, n)).name)
            )
        card = tmp / "card" / "scenes"
        stack.enter_context(mock.patch.object(ge, "CARD_SCENES", card))
        for n, v in (
            ("ROOT", tmp),
            ("SRC", tmp / "scenes.yaml"),
            ("MARKERS", tmp / "m.json"),
        ):
            stack.enter_context(mock.patch.object(ge, n, v))
        ge.SRC.write_text(yaml.safe_dump(doc))
        assert ge.main() == 0
        out: list[dict[str, Any]] = yaml.load(ge.OUT.read_text(), EsphomeLoader)[
            "script"
        ]
        return out, sorted(p.name for p in card.iterdir())


def firmware_scripts() -> list[tuple[str, str, list[Any]]]:
    """(file, id, actions) for every script in the hand-written firmware YAML.

    Read from the files rather than from a generator, because that is where
    the next long chain will be written — `scene_run` itself is hand-written
    now, and so is the boot self-test.
    """
    out: list[tuple[str, str, list[Any]]] = []
    for path in sorted((ROOT / "firmware").glob("*.yaml")):
        doc = yaml.load(path.read_text(), EsphomeLoader)
        if not isinstance(doc, dict):
            continue
        out.extend(
            (path.name, script.get("id", "?"), script.get("then") or [])
            for script in doc.get("script") or []
        )
    return out


class TestNoDeepChains(unittest.TestCase):
    def test_a_scene_of_three_hundred_cues_generates_no_script_at_all(self) -> None:
        """Was `test_no_script_exceeds_chunk_and_the_chain_is_complete` and
        `test_a_short_scene_is_one_script`. The scene's length is now entirely
        a question about a file: same three scripts either way."""
        short, _ = generate([long_scene(5)])
        epic, card = generate([long_scene(300)])
        self.assertEqual([s["id"] for s in short], [s["id"] for s in epic])
        self.assertEqual(
            [s["id"] for s in epic], ["scene_stop", "run_scene", "show_playlist"]
        )
        # And the 300 cues did go somewhere: the card, as one file.
        self.assertEqual(card, ["epic.cue", "show.man"])

    def test_no_generated_script_is_deep(self) -> None:
        out, _ = generate([long_scene(300), dict(long_scene(300), id="epic2")])
        for script in out:
            with self.subTest(script=script["id"]):
                self.assertLessEqual(len(script["then"]), ACTION_CEILING)

    def test_no_hand_written_firmware_script_is_deep(self) -> None:
        scripts = firmware_scripts()
        # A guard on the guard: an empty list would pass silently if the glob
        # or the loader ever stopped seeing the files. Eight today, across the
        # cue loader, the scene runner, the boot self-test, the card player and
        # the two bench rigs.
        self.assertGreaterEqual(len(scripts), 8)
        self.assertIn(("castle_scenes.yaml", "scene_run"), [s[:2] for s in scripts])
        for name, sid, actions in scripts:
            with self.subTest(file=name, script=sid):
                self.assertLessEqual(len(actions), ACTION_CEILING)

    def test_the_playlist_is_the_longest_chain_and_grows_per_scene(self) -> None:
        """Why the ceiling has headroom: the playlist is the one script whose
        length is still a function of the show, four actions per scene. Twelve
        scenes is 49 actions; the thirteenth is refused for other reasons
        (SCENE_LIMIT), so this is the worst case the firmware can be built in.
        """
        one, _ = generate([long_scene(3)])
        two, _ = generate([long_scene(3), dict(long_scene(3), id="epic2")])
        lengths = [
            len(next(s for s in out if s["id"] == "show_playlist")["then"])
            for out in (one, two)
        ]
        self.assertEqual(lengths[1] - lengths[0], 4)
        self.assertLess(lengths[0] + 11 * 4, ACTION_CEILING)

    def test_run_scene_stops_one_runner_not_a_list_of_scripts(self) -> None:
        """Was `test_run_scene_stops_every_continuation` — the crash itself.
        `run_scene` had to name every head and every `cont_<id>_N` it might
        have to interrupt, which is what made stopping expensive. It names one
        script now, and that script holds no timeline."""
        out, _ = generate([long_scene(300)])
        body = next(s for s in out if s["id"] == "run_scene")["then"][0]["lambda"]
        self.assertIn("id(scene_run)->stop();", body)
        self.assertNotIn("cont_", body)
        self.assertNotIn("scene_epic", body)

    # WITHOUT A SUCCESSOR, deliberately:
    #
    #   test_the_timeline_survives_the_split — replayed the emitted `delay:`
    #   chain and summed it to duration_ms. There is no split and there are no
    #   delays; the timeline is absolute milliseconds in a file, and that it
    #   matches what the script would have replayed is
    #   tests/test_scene_cue_equivalence.py's whole subject.
    #
    #   test_a_looping_scene_loops_back_to_its_head_only — the last
    #   continuation re-executed the head, and only the head. Looping is a bit
    #   in the manifest read by one self-executing script
    #   (tests/test_gen_esphome.py's test_looping_is_a_manifest_flag, and the
    #   runner's state machine in tests/test_scene_manifest_cxx.py).


if __name__ == "__main__":
    unittest.main()
