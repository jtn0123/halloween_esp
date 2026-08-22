"""Long scenes are emitted as short chained scripts — the crash of 2026-08-21.

ESPHome walks a script's action chain recursively to stop or poll it, one
stack frame per action, on an 8 KB loop task. `run_scene` stops every scene
script before starting the next, so the 457-action Citizens scene panicked
the castle on EVERY scene switch (v5.27). gen_esphome.chunked splits a scene
into a head plus `cont_<id>_N` continuations of at most CHUNK actions; these
tests pin the split, the chain, the unchanged timeline and the stop list.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from itertools import pairwise
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gen_esphome as ge
import yaml

ZONES = [{"id": "towerL"}, {"id": "towerR"}, {"id": "door"}]


def long_scene(n: int, loop: bool = False) -> dict:
    return {
        "id": "epic", "name": "Epic", "kind": "triggered", "loop": loop,
        "duration_ms": n * 100 + 500, "base": {"towerL": "candle"},
        "cues": [{"t": 100 * (i + 1), "op": "strike", "intensity": 0.5}
                 for i in range(n)],
    }


def scripts(lines: list[str]) -> list[dict]:
    out: list[dict] = yaml.safe_load("script:\n" + "\n".join(lines))["script"]
    return out


def replay(lines: list[str]) -> list[int]:
    """Walk every emitted script in order; continuations keep the clock."""
    times, t, started = [], 0, False
    for ln in lines:
        s = ln.strip()
        if s == "id: sfx":
            started = True
        elif started and s.startswith("- delay:"):
            t += int(s.split()[-1].removesuffix("ms"))
        elif started and s.startswith("- lambda:"):
            times.append(t)
    return times


class TestChunking(unittest.TestCase):
    def test_a_short_scene_is_one_script(self) -> None:
        sc = scripts(ge.emit_scene(long_scene(5), ZONES, 1, {}))
        self.assertEqual([s["id"] for s in sc], ["scene_epic"])

    def test_no_script_exceeds_chunk_and_the_chain_is_complete(self) -> None:
        sc = scripts(ge.emit_scene(long_scene(300), ZONES, 1, {}))
        ids = [s["id"] for s in sc]
        self.assertEqual(ids[0], "scene_epic")
        self.assertEqual(ids[1:], [f"cont_epic_{k}" for k in range(1, len(ids))])
        for s in sc:
            self.assertLessEqual(len(s["then"]), ge.CHUNK, s["id"])
            self.assertEqual(s["mode"], "restart")
        for here, nxt in pairwise(sc):
            self.assertEqual(here["then"][-1], {"script.execute": nxt["id"]})
        self.assertNotIn("script.execute", sc[-1]["then"][-1])   # not looping

    def test_the_timeline_survives_the_split(self) -> None:
        scene = long_scene(300)
        lines = ge.emit_scene(scene, ZONES, 1, {})
        self.assertEqual(replay(lines), [c["t"] for c in scene["cues"]])
        total = sum(int(ln.strip().split()[-1].removesuffix("ms"))
                    for ln in lines if ln.strip().startswith("- delay:"))
        self.assertEqual(total, scene["duration_ms"])

    def test_a_looping_scene_loops_back_to_its_head_only(self) -> None:
        sc = scripts(ge.emit_scene(long_scene(300, loop=True), ZONES, 1, {}))
        self.assertEqual(sc[-1]["then"][-1], {"script.execute": "scene_epic"})
        text = yaml.safe_dump(sc[:-1])
        self.assertNotIn("script.execute: scene_epic", text)

    def test_run_scene_stops_every_continuation(self) -> None:
        """The whole point: the one script mid-delay may be any chunk."""
        scene = long_scene(300)
        doc = {"zones": ZONES, "hardware": {"pixels_per_zone": 7},
               "scenes": [scene]}
        # Every output path redirected — main() writes rig.h, lights.yaml
        # and the audio dispatch too, and the real ones are tracked files.
        names = ("MEDIA_OUT", "AUDIO_FLASH", "AUDIO_SD", "RIG_OUT", "LIGHTS_OUT", "OUT")
        with tempfile.TemporaryDirectory() as td, \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.ExitStack() as stack:
            tmp = Path(td)
            (tmp / "generated").mkdir()
            # Every output path redirected, restored by the stack even if an
            # assertion raises — the real ones are tracked files.
            for n in names:
                stack.enter_context(mock.patch.object(
                    ge, n, tmp / "generated" / Path(getattr(ge, n)).name))
            for n, v in (("ROOT", tmp), ("SRC", tmp / "scenes.yaml"),
                         ("MARKERS", tmp / "m.json")):
                stack.enter_context(mock.patch.object(ge, n, v))
            ge.SRC.write_text(yaml.safe_dump(doc))
            self.assertEqual(ge.main(), 0)
            out = yaml.safe_load(ge.OUT.read_text())["script"]
        body = next(s for s in out if s["id"] == "run_scene")["then"][0]["lambda"]
        ids = [s["id"] for s in out if s["id"].startswith(("scene_epic", "cont_epic_"))]
        self.assertGreater(len(ids), 5)
        for sid in ids:
            self.assertIn(f"id({sid})->stop();", body)

if __name__ == "__main__":
    unittest.main()
