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

import gen_esphome as ge
import yaml
from test_gen_esphome import OUTPUT_PATHS, ZONES, scene


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
        # EVERY module-level output path must be redirected here. MEDIA_OUT was
        # forgotten when it was added, and these tests then wrote their two
        # fixture scenes into the real firmware/generated/media_files.yaml —
        # which broke the next firmware build with "cannot find 01_a.mp3".
        # EVERY module-level output path, checked against the module rather
        # than listed by hand — the list went stale twice (MEDIA_OUT when it
        # was added, then RIG_OUT and LIGHTS_OUT), and both times these tests
        # wrote their two fixture scenes into the real firmware/generated/.
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
            setattr(ge, name, self.tmp / "generated" / Path(getattr(ge, name)).name)
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
        real = ROOT / "firmware" / "generated"
        for name in dir(ge):
            value = getattr(ge, name)
            if not isinstance(value, Path) or name.startswith("_"):
                continue
            if value.is_relative_to(real):
                self.fail(
                    f"ge.{name} still points at the real tree — add it to OUTPUT_PATHS"
                )

    def test_writes_a_parseable_file_with_every_scene_and_a_stop_script(self) -> None:
        self.assertEqual(ge.main(), 0)
        self.assertIn("wrote ", self.out.getvalue())
        doc = yaml.safe_load(ge.OUT.read_text())
        self.assertEqual(
            [s["id"] for s in doc["script"]],
            ["scene_a", "scene_b", "scene_stop", "run_scene", "show_playlist"],
        )

    def test_blackout_script_clears_every_zone(self) -> None:
        """One call has to be enough to make the whole castle go dark."""
        ge.main()
        doc = yaml.safe_load(ge.OUT.read_text())
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
        doc = yaml.safe_load(ge.OUT.read_text())
        lam = next(s for s in doc["script"] if s["id"] == "scene_stop")["then"][0][
            "lambda"
        ]
        for i in range(len(ZONES)):
            self.assertIn(f"id(zone_center)[{i}] = -1;", lam)
            self.assertIn(f"id(zone_overlay)[{i}] = 0;", lam)
            self.assertIn(f"id(zone_flash_target)[{i}] = 0.0f;", lam)

    def test_run_scene_halt_stops_the_scripts_and_starts_nothing(self) -> None:
        """/api/play runs run_scene("halt"): every scene script stopped (the
        looping one must not come back over the file), then no scene — and
        no blackout, the lights keep their texture. Not its own script: the
        S2's static RAM is on a diet and a script is a static object."""
        ge.main()
        doc = yaml.safe_load(ge.OUT.read_text())
        lam = next(s for s in doc["script"] if s["id"] == "run_scene")["then"][0][
            "lambda"
        ]
        self.assertIn('else if (scene == "halt") {}', lam)
        for sid in ("scene_a", "scene_b"):
            self.assertIn(f"id({sid})->stop();", lam)
        self.assertLess(lam.index("->stop();"), lam.index('"halt"'))

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
        out = ge.OUT.read_text()
        self.assertIn("0.0f : 0.8f", out)  # 1.0 capped
        self.assertIn("0.0f : 0.5f", out)  # under the cap, untouched
        self.assertNotIn("0.0f : 1.0f", out)
        self.assertIn(
            "inline constexpr int kMaxVolumePct = 80;", ge.RIG_OUT.read_text()
        )

    def test_blackout_stops_the_scene_scripts_themselves(self) -> None:
        """Clearing the output is not enough: a looping scene mid-delay
        re-fires after the stop and walks back on, audio and all. The stop
        has to halt every scene script (continuations included)."""
        ge.main()
        doc = yaml.safe_load(ge.OUT.read_text())
        stop = next(s for s in doc["script"] if s["id"] == "scene_stop")
        lams = [a["lambda"] for a in stop["then"] if "lambda" in a]
        for sid in ("scene_a", "scene_b"):
            self.assertTrue(
                any(f"id({sid})->stop();" in lam for lam in lams),
                f"scene_stop leaves {sid} armed",
            )

    def test_pixel_map_is_written_into_the_header_comment(self) -> None:
        ge.main()
        text = ge.OUT.read_text()
        self.assertIn("DO NOT EDIT", text)
        # Three strips now, so the ranges are what a SINGLE chain would have
        # been — still the thing you want when tracing a dark window on a
        # one-chain build. See zone_pixels in gen_esphome.py.
        self.assertRegex(text, r"door\s+7 px \(chain equivalent 14-20\)")

    def test_missing_markers_file_still_generates(self) -> None:
        """`make generate` must work before `make audio` has ever run."""
        ge.main()
        doc = yaml.safe_load(ge.OUT.read_text())
        self.assertEqual(len(doc["script"]), 5)

    def test_markers_file_is_used_when_present(self) -> None:
        ge.MARKERS.write_text('{"b": {"h": [[250, 1.0]]}}')
        ge.main()
        then = yaml.safe_load(ge.OUT.read_text())["script"][1]["then"]
        self.assertIn({"delay": "250ms"}, then)

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
    unittest.main(verbosity=2)


if __name__ == "__main__":
    unittest.main()
