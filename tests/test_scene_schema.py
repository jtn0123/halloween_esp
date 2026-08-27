"""Tests for scene_schema: the shape of a scene block, before it is written.

The studio splices a block into scenes.yaml and gen_esphome emits from it;
both run this first. The interesting cases are the ones that used to get
THROUGH — they parsed, so they were written, and the failure came back later
from a subprocess log (grade report B4).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gen_esphome as ge
import scene_schema as ss
import yaml

ZONES = ["towerL", "towerR", "door"]


def scene(**over: object) -> dict[str, Any]:
    s: dict[str, Any] = {
        "id": "probe",
        "name": "Probe",
        "kind": "triggered",
        "duration_ms": 5000,
        "base": {"towerL": "candle", "towerR": "candle", "door": "ember"},
    }
    s.update(over)
    return s


class TestShape(unittest.TestCase):
    def test_a_minimal_scene_is_clean(self) -> None:
        self.assertEqual(ss.validate(scene(), ZONES), [])

    def test_every_real_scene_is_clean(self) -> None:
        """The rule set must accept the show as it is, or it is the wrong
        rule set."""
        doc = yaml.safe_load((ROOT / "scenes" / "scenes.yaml").read_text())
        zones = [z["id"] for z in doc["zones"]]
        for s in doc["scenes"]:
            self.assertEqual(ss.validate(s, zones), [], s["id"])

    def test_not_a_mapping(self) -> None:
        self.assertEqual(ss.validate(["a"]), ["scene must be a mapping"])
        self.assertEqual(ss.validate(None), ["scene must be a mapping"])

    def test_missing_required_keys_are_each_named(self) -> None:
        errs = ss.validate({"id": "x"})
        for k in ("name", "kind", "duration_ms", "base"):
            self.assertTrue(any(f"'{k}'" in e for e in errs), (k, errs))

    def test_id_is_filesystem_and_esphome_safe(self) -> None:
        for bad in ("a-b", "a b", "../x", "", 7):
            self.assertTrue(
                any(e.startswith("id:") for e in ss.validate(scene(id=bad))), bad
            )
        self.assertEqual(ss.validate(scene(id="the_citizens_01")), [])

    def test_duration_is_a_positive_whole_number_of_ms(self) -> None:
        # "NaN" is what the desk writes when a track's length is unknown.
        for bad in (0, -1, 1.5, "5000", float("nan"), True):
            errs = ss.validate(scene(duration_ms=bad))
            self.assertTrue(any("duration_ms" in e for e in errs), bad)
        self.assertEqual(ss.validate(scene(duration_ms=1)), [])

    def test_volume_levels_and_loop_ranges(self) -> None:
        self.assertTrue(ss.validate(scene(volume=1.5)))
        self.assertTrue(ss.validate(scene(loop="yes")))
        self.assertTrue(ss.validate(scene(levels={"door": 2})))
        self.assertEqual(
            ss.validate(scene(volume=0, loop=False, levels={"door": 0.5})), []
        )

    def test_audio_file_is_a_relative_path(self) -> None:
        for bad in ("/etc/passwd", "tracks/../secret.mp3", "", 3):
            self.assertTrue(
                any(
                    e.startswith("audio_file")
                    for e in ss.validate(scene(audio_file=bad))
                ),
                bad,
            )
        self.assertEqual(ss.validate(scene(audio_file="tracks/x.mp3")), [])


class TestVocabulary(unittest.TestCase):
    """Effects, overlays, palettes and pixel modes come from gen_esphome —
    one list, the one the firmware is built from."""

    def test_unknown_effect_in_base_and_centre(self) -> None:
        errs = ss.validate(
            scene(base={"door": "glow"}, zones={"towerL": {"center": "nope"}})
        )
        self.assertEqual(len(errs), 2, errs)
        self.assertIn("base.door: unknown effect 'glow'", errs[0])
        self.assertIn("zones.towerL.center", errs[1])

    def test_every_firmware_effect_is_accepted(self) -> None:
        for eff in ge.EFFECT_IDS:
            self.assertEqual(ss.validate(scene(base={"door": eff})), [], eff)

    def test_overlay_palette_and_pixels(self) -> None:
        self.assertTrue(ss.validate(scene(zones={"door": {"overlay": "glitter"}})))
        self.assertTrue(ss.validate(scene(zones={"door": {"palette": "neon"}})))
        self.assertTrue(
            ss.validate(scene(cues=[{"t": 0, "op": "strike", "pixels": "random"}]))
        )
        self.assertEqual(
            ss.validate(
                scene(
                    zones={
                        "door": {"overlay": "chase", "palette": "toxic", "phase": 1}
                    },
                    cues=[{"t": 0, "op": "strike", "pixels": "scatter"}],
                )
            ),
            [],
        )

    def test_zone_names_are_checked_only_when_the_show_is_known(self) -> None:
        s = scene(base={"attic": "candle"})
        self.assertEqual(ss.validate(s), [])
        errs = ss.validate(s, ZONES)
        self.assertEqual(len(errs), 1)
        self.assertIn("no zone 'attic'", errs[0])


class TestCuesAndPulse(unittest.TestCase):
    def test_cue_past_the_end_is_named_with_its_index(self) -> None:
        errs = ss.validate(
            scene(
                cues=[
                    {"t": 100, "op": "strike"},
                    {"t": 9000, "op": "set", "zone": "door", "effect": "ember"},
                ]
            )
        )
        self.assertEqual(len(errs), 1)
        self.assertTrue(errs[0].startswith("cues[1]"), errs)
        self.assertIn("duration_ms=5000", errs[0])

    def test_cue_at_exactly_the_end_is_fine(self) -> None:
        self.assertEqual(ss.validate(scene(cues=[{"t": 5000, "op": "strike"}])), [])

    def test_set_needs_zone_and_a_known_effect(self) -> None:
        errs = ss.validate(scene(cues=[{"t": 0, "op": "set"}]))
        self.assertEqual(len(errs), 2, errs)
        errs = ss.validate(
            scene(
                cues=[
                    {"t": 0, "op": "set", "zone": "door", "effect": "glow", "level": 3}
                ]
            )
        )
        self.assertEqual(len(errs), 2, errs)

    def test_unknown_op_and_bad_time(self) -> None:
        errs = ss.validate(scene(cues=[{"t": -5, "op": "fade"}]))
        self.assertEqual(len(errs), 2, errs)
        self.assertTrue(ss.validate(scene(cues=[{"op": "strike"}])))
        self.assertTrue(ss.validate(scene(cues=["strike"])))
        self.assertTrue(ss.validate(scene(cues={"t": 0})))

    def test_strike_ranges(self) -> None:
        good = {
            "t": 0,
            "op": "strike",
            "zone": "door",
            "targets": ["towerL"],
            "intensity": 1.2,
            "decay": 0.9,
            "ms": 80,
            "attack": 40,
            "color": [1, 0.2, 0, 0],
        }
        self.assertEqual(ss.validate(scene(cues=[good]), ZONES), [])
        for k, v in (
            ("decay", 1.5),
            ("ms", -1),
            ("color", [2, 0, 0]),
            ("targets", "door"),
            ("intensity", "loud"),
        ):
            self.assertTrue(ss.validate(scene(cues=[{**good, k: v}])), k)

    def test_pulse_needs_a_synth_and_sane_numbers(self) -> None:
        self.assertTrue(ss.validate(scene(pulse=[{"zones": ["door"]}])))
        good = {
            "synth": "onset_low",
            "zones": ["door"],
            "intensity": 0.8,
            "decay": 0.9,
            "ms": 140,
            "attack_ms": 20,
            "colors": [[1, 0, 0, 0], [0, 1, 0]],
            "color_hot": [1, 1, 1, 1],
            "boost_targets": ["towerL"],
            "pixels": "scatter",
        }
        self.assertEqual(ss.validate(scene(pulse=[good]), ZONES), [])
        for k, v in (
            ("decay", 2),
            ("colors", []),
            ("colors", [[1, 2, 3]]),
            ("boost_targets", ["attic"]),
            ("pixels", "all over"),
        ):
            self.assertTrue(ss.validate(scene(pulse=[{**good, k: v}]), ZONES), k)


if __name__ == "__main__":
    unittest.main(verbosity=2)
