"""The shipped show: the gate that keeps a song off a castle that is sold.

scenes/shipped.yaml is what every hand-off of a show reads — the desktop
app's first run, the uv installer, and the buyer's SD card image
(tools/shipped_show.py says which code reads it). No song ships
(docs/LICENSING.md), so this is where that rule is enforced, not trusted:

  * the committed file is exactly what the derivation makes of today's
    scenes/scenes.yaml, so a desk edit cannot leave it behind;
  * no scene in it plays, tunes or pulses on an imported track, and no
    `tracks/` path is anywhere in it;
  * every scene the firmware starts BY NAME — the buttons, the PIR's
    default, the phone remote, the bench — and the evening it plays are in
    it, so a sold castle never reaches for a scene its card lacks;
  * the seeding points at it, on both the Python and the Tauri side;
  * and the firmware's compiled fallback is byte-for-byte the same whether
    it is generated from the yard's show or the shipped one, so no image
    names a song.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import desktop_env
import gen_scene_cards
import scene_manifest
import shipped_show as ss

FW = ROOT / "firmware"


def shipped() -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load(ss.SHIPPED.read_text(encoding="utf-8"))
    return doc


def named_by_firmware() -> set[str]:
    """Every scene id the firmware's own source starts by name."""
    names: set[str] = set()
    for f in ("castle_inputs.yaml", "bench_audio.yaml"):
        text = (FW / f).read_text(encoding="utf-8")
        names |= set(re.findall(r"run_scene, scene: (\w+)\}", text))
    remote = (FW / "sd_web_remote.h").read_text(encoding="utf-8")
    names |= set(re.findall(r"/api/scene\?s=(\w+)'", remote))
    gen = (FW / "generated" / "scenes.yaml").read_text(encoding="utf-8")
    pir = re.search(r"id: pir_scene\n(?:    .*\n)*?    initial_value: (\w+)", gen)
    assert pir is not None, "no pir_scene default in generated/scenes.yaml"
    names.add(pir.group(1))
    return names


class TestTheCommittedShippedShow(unittest.TestCase):
    def test_it_is_what_the_yards_show_derives_to_today(self) -> None:
        want = ss.derive(ss.SOURCE.read_text(encoding="utf-8"))
        self.assertEqual(
            ss.SHIPPED.read_text(encoding="utf-8"),
            want,
            "scenes/shipped.yaml is stale — run tools/shipped_show.py "
            "(make generate does it)",
        )

    def test_no_scene_in_it_needs_a_song(self) -> None:
        for scene in shipped()["scenes"]:
            with self.subTest(scene=scene["id"]):
                self.assertEqual(ss.needs_track(scene), [])
        text = ss.SHIPPED.read_text(encoding="utf-8")
        self.assertNotIn("tracks/", text)
        self.assertNotRegex(text, r"(?m)^\s+audio_file:")

    def test_every_scene_the_firmware_names_is_in_it(self) -> None:
        ids = {s["id"] for s in shipped()["scenes"]}
        named = named_by_firmware()
        self.assertGreaterEqual(len(named), 8, named)  # a guard on the regexes
        self.assertLessEqual(named, ids)

    def test_it_has_an_evening_and_a_boot_scene(self) -> None:
        doc = shipped()
        evening = scene_manifest.evening_order(doc)
        self.assertGreaterEqual(len(evening), 1)
        # The castle opens with the first scene on its card; the compiled
        # fallback look is that same scene's.
        fallback = (FW / "generated" / "fallback_scenes.h").read_text(encoding="utf-8")
        self.assertIn(f"// {doc['scenes'][0]['id']}'s base look", fallback)

    def test_the_firmware_fallback_is_the_same_from_either_show(self) -> None:
        yard = yaml.safe_load(ss.SOURCE.read_text(encoding="utf-8"))
        self.assertEqual(
            gen_scene_cards.fallback_header(yard),
            gen_scene_cards.fallback_header(shipped()),
        )
        committed = (FW / "generated" / "fallback_scenes.h").read_text(encoding="utf-8")
        for s in yard["scenes"]:
            if ss.needs_track(s):
                self.assertNotIn(s["id"], committed)

    def test_every_first_run_seeds_it(self) -> None:
        self.assertEqual(desktop_env.SHIPPED_SCENES, Path("scenes") / "shipped.yaml")
        rs = (ROOT / "desktop" / "src-tauri" / "src" / "runtime.rs").read_text(
            encoding="utf-8"
        )
        body = rs[rs.index("pub fn seed_scenes") :]
        body = body[: body.index("\n}\n")]
        self.assertIn('join("shipped.yaml")', body)
        self.assertNotIn('join("scenes.yaml")', body.replace("data.scenes()", ""))


SOURCE = """\
# header comment
hardware: {board: x}
show:
  gap_ms: 100
  order: [song, b, a]
zones:
  - {id: z}
scenes:
  - id: a
    kind: ambient
    duration_ms: 1000
    score:
      - {t: 0, synth: wind, dur: 1.0}

  - id: song
    kind: custom
    duration_ms: 2000
    audio_file: tracks/song.mp3
    pulse:
      - {synth: onset_low, zones: [z]}

  - id: b
    # a comment inside b stays with b
    kind: motion
    duration_ms: 500

  - id: beat
    kind: custom
    duration_ms: 900
    pulse:
      - {synth: onset_mid, zones: [z]}
"""


class TestTheDerivation(unittest.TestCase):
    def test_track_scenes_go_and_everything_else_stays_verbatim(self) -> None:
        out = ss.derive(SOURCE)
        doc = yaml.safe_load(out)
        self.assertEqual([s["id"] for s in doc["scenes"]], ["a", "b"])
        self.assertIn("# a comment inside b stays with b", out)
        self.assertIn("# header comment", out)
        self.assertNotIn("tracks/", out)
        self.assertNotIn("beat", out, "an onset pulse needs a track too")
        # show.order loses the dropped id and keeps its own order.
        self.assertEqual(doc["show"], {"gap_ms": 100, "order": ["b", "a"]})
        self.assertTrue(out.startswith(ss.BANNER))

    def test_why_a_scene_needs_a_track(self) -> None:
        self.assertEqual(
            ss.needs_track({"audio_file": "tracks/x.mp3", "track_gain": 1}),
            ["audio_file: tracks/x.mp3", "track_gain:"],
        )
        self.assertEqual(
            ss.needs_track({"pulse": [{"synth": "onset_high"}, {"synth": "toll"}]}),
            ["pulse synth onset_high"],
        )
        self.assertEqual(ss.needs_track({"pulse": [{"synth": "heartbeat"}]}), [])

    def test_a_show_that_is_all_songs_ships_nothing_and_says_so(self) -> None:
        only = "scenes:\n  - id: s\n    duration_ms: 1\n    audio_file: tracks/s.mp3\n"
        with self.assertRaisesRegex(SystemExit, "nothing ships"):
            ss.derive(only)

    def test_an_order_with_nothing_to_drop_is_left_as_written(self) -> None:
        src = SOURCE.replace("order: [song, b, a]", "order: [b, a]  # kept")
        self.assertIn("order: [b, a]  # kept", ss.derive(src))

    def test_the_command_writes_then_checks(self) -> None:
        quiet = contextlib.ExitStack()
        quiet.enter_context(contextlib.redirect_stdout(io.StringIO()))
        quiet.enter_context(contextlib.redirect_stderr(io.StringIO()))
        with quiet, tempfile.TemporaryDirectory() as td:
            src, out = Path(td) / "scenes.yaml", Path(td) / "shipped.yaml"
            src.write_text(SOURCE, encoding="utf-8")
            quiet.enter_context(mock.patch.object(ss, "SOURCE", src))
            quiet.enter_context(mock.patch.object(ss, "SHIPPED", out))
            self.assertEqual(ss.main(["--check"]), 1, "missing is stale")
            self.assertEqual(ss.main([]), 0)
            self.assertEqual(ss.main(["--check"]), 0)
            self.assertFalse(ss.write(), "a second write changes nothing")
            src.write_text(
                SOURCE.replace("gap_ms: 100", "gap_ms: 200"), encoding="utf-8"
            )
            self.assertEqual(ss.main(["--check"]), 1)
            with self.assertRaises(SystemExit):
                ss.main(["--out", str(out)])  # no path from the command line


if __name__ == "__main__":
    unittest.main()
