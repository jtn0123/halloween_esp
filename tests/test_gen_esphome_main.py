"""End to end: a scenes.yaml on disk becomes a loadable ESPHome file.

Split from test_gen_esphome.py at the 500-line cap along the seam that was
already there: that file unit-tests the emitters; this one runs ge.main()
whole, against a redirected output tree. The fixtures (scene(), ZONES,
OUTPUT_PATHS) stay with the emitter tests and are imported from there.
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import cue_file
import gen_esphome as ge
import scene_manifest
import yaml
from test_gen_esphome import OUTPUT_PATHS, ZONES, EsphomeLoader, scene


class TestGenEsphomeMain(unittest.TestCase):
    """End to end: a scenes.yaml on disk becomes a loadable ESPHome file."""

    DOC: ClassVar[dict[str, Any]] = {
        "hardware": {"pixels_per_zone": 7},
        "zones": ZONES,
        "scenes": [
            scene(id="a", loop=True, cues=[{"t": 100, "op": "strike"}]),
            scene(id="b", pulse=[{"synth": "h", "zone": "door"}]),
        ],
    }

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        # EVERY module-level output path, checked against the module rather
        # than listed by hand — the list went stale twice (the media manifest
        # when it was added, then RIG_OUT and LIGHTS_OUT), and both times
        # these tests wrote their two fixture scenes into the real
        # firmware/generated/, which broke the next firmware build with
        # "cannot find 01_a.mp3".
        # test_every_output_path_is_redirected below is what keeps it honest.
        self._saved = {name: getattr(ge, name) for name in OUTPUT_PATHS}
        # The generator narrates ("wrote …", "note: …"); keep -q output clean.
        self.out = self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self._saved["ROOT"] = ge.ROOT
        ge.ROOT = self.tmp
        ge.SRC = self.tmp / "scenes.yaml"
        ge.MARKERS = self.tmp / "markers.json"
        for name in OUTPUT_PATHS:
            if name in ("SRC", "MARKERS"):
                continue
            # CARD_SCENES is a DIRECTORY (the publish tree), not a file; both
            # keep their own basename so a wrong redirect is legible.
            leaf = "card" if name == "CARD_SCENES" else "generated"
            setattr(ge, name, self.tmp / leaf / Path(getattr(ge, name)).name)
        ge.SRC.write_text(yaml.safe_dump(self.DOC))

    def tearDown(self) -> None:
        for name, value in self._saved.items():
            setattr(ge, name, value)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_every_output_path_is_redirected(self) -> None:
        """A new output added to gen_esphome must be added to OUTPUT_PATHS.

        Without this the next one silently writes into the real
        firmware/generated/ during a test run, which is how the last two got
        noticed — by breaking the following build.
        """
        # Two real trees now: the firmware's generated sources, and the card
        # publish directory the show itself is written into since v5.67.
        real = (ROOT / "firmware" / "generated", ROOT / "audio" / "card")
        for name in dir(ge):
            value = getattr(ge, name)
            if not isinstance(value, Path) or name.startswith("_"):
                continue
            if any(value.is_relative_to(r) for r in real):
                self.fail(
                    f"ge.{name} still points at the real tree — add it to OUTPUT_PATHS"
                )

    def test_writes_a_parseable_file_and_the_whole_show_onto_the_card(self) -> None:
        """Was `..._with_every_scene_and_a_stop_script`: the generated file
        held a `scene_<id>` script per scene. It holds three fixed scripts now
        and the scenes are card files, so both halves are asserted here — the
        file the build compiles, and the directory `sd_sync scenes` pushes."""
        self.assertEqual(ge.main(), 0)
        self.assertIn("wrote ", self.out.getvalue())
        doc = yaml.load(ge.OUT.read_text(), EsphomeLoader)
        self.assertEqual(
            [s["id"] for s in doc["script"]],
            ["scene_stop", "run_scene", "show_playlist"],
        )
        self.assertEqual(
            sorted(p.name for p in ge.CARD_SCENES.iterdir()),
            ["a.cue", "b.cue", "show.man"],
        )

    def test_fallback_scene_ids_are_generated_from_the_show(self) -> None:
        self.assertEqual(ge.main(), 0)
        text = ge.FALLBACK_SCENES_OUT.read_text()
        self.assertIn("'a'", text)
        self.assertIn("'b'", text)

    def test_blackout_script_clears_every_zone(self) -> None:
        """One call has to be enough to make the whole castle go dark."""
        ge.main()
        doc = yaml.load(ge.OUT.read_text(), EsphomeLoader)
        lam = next(s for s in doc["script"] if s["id"] == "scene_stop")["then"][0][
            "lambda"
        ]
        for i in range(len(ZONES)):
            self.assertIn(f"id(zone_effect)[{i}] = 0;", lam)
            self.assertIn(f"id(zone_flash)[{i}] = 0.0f;", lam)

    def test_blackout_clears_the_centre_role_and_overlay_too(self) -> None:
        """zone_effect = 0 alone is not dark. The render loop draws the centre
        pixel from zone_center when one is set, and every overlay adds light
        on top of a black base (sparkle glints, a meteor's white drip, the
        chase's white head) — so a stop that left those standing kept vigil's
        centre embers lit and the door sparkling through the playlist gap."""
        ge.main()
        doc = yaml.load(ge.OUT.read_text(), EsphomeLoader)
        lam = next(s for s in doc["script"] if s["id"] == "scene_stop")["then"][0][
            "lambda"
        ]
        for i in range(len(ZONES)):
            self.assertIn(f"id(zone_center)[{i}] = -1;", lam)
            self.assertIn(f"id(zone_overlay)[{i}] = 0;", lam)
            self.assertIn(f"id(zone_flash_target)[{i}] = 0.0f;", lam)

    def test_run_scene_halt_stops_the_runner_and_starts_nothing(self) -> None:
        """/api/play runs run_scene("halt"): the scene runner stopped (a
        looping scene must not come back over the file), its cues given back,
        then no scene — and no blackout, the lights keep their texture.

        The stop used to name every generated scene script; there is one
        runner now, and `castle_scenes::stop()` is the line that also releases
        the cue blob, which matters here of all places: the file about to play
        may bring a `.cue` of its own."""
        ge.main()
        doc = yaml.load(ge.OUT.read_text(), EsphomeLoader)
        lam = next(s for s in doc["script"] if s["id"] == "run_scene")["then"][0][
            "lambda"
        ]
        self.assertIn('if (scene == "halt") { castle_scenes::stop(); return; }', lam)
        self.assertIn("id(scene_run)->stop();", lam)
        self.assertLess(lam.index("->stop();"), lam.index('"halt"'))

    def test_run_scene_hands_the_strips_back_to_show_except_on_halt(self) -> None:
        """A colour or "off" from the desk takes the strips off the Show
        effect; until 2026-09-14 nothing gave them back before a reboot, so
        every scene ran dark after a channel test. run_scene relights any
        zone that is off or on another effect — and "halt" (the /api/play
        path, which must leave the lights alone) is excluded."""
        ge.main()
        doc = yaml.load(ge.OUT.read_text(), EsphomeLoader)
        lam = next(s for s in doc["script"] if s["id"] == "run_scene")["then"][0][
            "lambda"
        ]
        guard = lam[
            lam.index('if (scene != "halt")') : lam.index('if (scene == "stop")')
        ]
        self.assertIn('id(lights_override)->execute("show")', guard)
        self.assertIn('z->get_effect_name() != "Show"', guard)
        self.assertIn("!z->remote_values.is_on()", guard)
        for z in ZONES:
            self.assertIn(f"id(zone_{z['id']})", guard)
        # After the stops, before any scene starts: the relight is never
        # overtaken by a scene's first frame.
        self.assertLess(lam.rindex("->stop();"), lam.index('if (scene != "halt")'))

    def test_max_volume_caps_every_scene_and_reaches_rig_h(self) -> None:
        """hardware.audio.max_volume is the porch's measured ceiling: a scene
        asking for more is generated under it, and rig.h carries the same
        number for /api/volume to clamp to — one source, both builds."""
        doc = dict(
            self.DOC, hardware={"pixels_per_zone": 7, "audio": {"max_volume": 0.8}}
        )
        doc["scenes"] = [scene(id="a", volume=1.0), scene(id="b", volume=0.5)]
        ge.SRC.write_text(yaml.safe_dump(doc))
        self.assertEqual(ge.main(), 0)
        # The cap used to be printed into each scene script's volume lambda
        # (`id(speaker_hush) ? 0.0f : 0.8f`). It is a whole percent in the
        # card manifest now, read by the one generic runner.
        entries = {
            e["id"]: e
            for e in scene_manifest.decode((ge.CARD_SCENES / "show.man").read_bytes())
        }
        self.assertEqual(entries["a"]["volume_pct"], 80)  # 1.0 capped
        self.assertEqual(entries["b"]["volume_pct"], 50)  # under the cap, untouched
        self.assertIn(
            "inline constexpr int kMaxVolumePct = 80;", ge.RIG_OUT.read_text()
        )

    def test_blackout_stops_the_runner_and_releases_its_cues(self) -> None:
        """Clearing the output is not enough: a looping scene waiting on its
        length re-fires after the stop and walks back on, audio and all. The
        stop has to halt the runner — which used to mean naming every scene
        script and every `cont_<id>_N` continuation, 85 of them, and now means
        one script — and hand the cue blob back, or /api/status would report
        cues for a castle that is dark."""
        ge.main()
        doc = yaml.load(ge.OUT.read_text(), EsphomeLoader)
        stop = next(s for s in doc["script"] if s["id"] == "scene_stop")
        lams = "\n".join(a["lambda"] for a in stop["then"] if "lambda" in a)
        self.assertIn("id(scene_run)->stop();", lams)
        self.assertIn("castle_scenes::stop();", lams)
        self.assertIn("castle_web::g_cues.store(0);", lams)

    def test_pixel_map_is_written_into_the_header_comment(self) -> None:
        ge.main()
        text = ge.OUT.read_text()
        self.assertIn("DO NOT EDIT", text)
        # Three strips now, so the ranges are what a SINGLE chain would have
        # been — still the thing you want when tracing a dark window on a
        # one-chain build. See zone_pixels in gen_esphome.py.
        self.assertRegex(text, r"door\s+7 px \(chain equivalent 14-20\)")

    def test_missing_markers_file_still_generates(self) -> None:
        """`make generate` must work before `make audio` has ever run: the
        pulse streams simply expand to nothing. Scene b's `pulse:` block is
        the one that would otherwise need markers."""
        ge.main()
        self.assertEqual(len(yaml.load(ge.OUT.read_text(), EsphomeLoader)["script"]), 3)
        b = cue_file.decode((ge.CARD_SCENES / "b.cue").read_bytes())
        self.assertEqual(b["records"], [])
        self.assertIn("no audio/markers.json", self.out.getvalue())

    def test_markers_file_is_used_when_present(self) -> None:
        """Was asserted as a `delay: 250ms` in the emitted script — the delta
        from the scene's start to the beat. It is the absolute millisecond in
        a cue record now."""
        ge.MARKERS.write_text('{"b": {"h": [[250, 1.0]]}}')
        ge.main()
        b = cue_file.decode((ge.CARD_SCENES / "b.cue").read_bytes())
        self.assertEqual([r["t"] for r in b["records"]], [250])

    def test_a_scene_the_schema_rejects_stops_the_build_with_every_reason(self) -> None:
        """scene_schema runs before a byte is emitted — the same checks the
        studio applies to a splice — so a hand-edited scenes.yaml fails with
        the whole list, named by scene, and writes nothing."""
        doc = dict(
            self.DOC,
            scenes=[
                scene(id="a"),
                scene(
                    id="bad",
                    duration_ms=100,
                    base={"door": "glow"},
                    cues=[{"t": 900, "op": "set", "zone": "door", "effect": "ember"}],
                ),
            ],
        )
        ge.SRC.write_text(yaml.safe_dump(doc))
        with self.assertRaises(SystemExit) as cm:
            ge.main()
        msg = str(cm.exception)
        self.assertTrue(msg.startswith("scene bad:"), msg)
        self.assertIn("unknown effect 'glow'", msg)
        self.assertIn("past the scene's duration_ms", msg)
        self.assertFalse(ge.OUT.exists(), "a rejected show was still written")


if __name__ == "__main__":
    unittest.main()
