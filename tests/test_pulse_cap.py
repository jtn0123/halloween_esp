"""The pulse cap: why v5.25 would not boot, pinned.

ESPHome keeps static RAM per generated action; one dense track scene
(1,216 pulse hits → 2,402 actions) ate ~32 KB of the S2's DRAM and the
image panicked before WiFi. Both generators now keep each scene's strongest
PULSE_CAP hits — and they must keep the SAME ones, or the desk lies about
the porch.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gen_previewer as gp
import pulse_dynamics as pd
import pulse_expand as pe


class TestThinPulses(unittest.TestCase):
    def test_keeps_the_strongest_in_time_order(self) -> None:
        cues = [
            {"t": t, "intensity": i}
            for t, i in ((0, 0.1), (100, 0.9), (200, 0.5), (300, 0.95), (400, 0.2))
        ]
        kept = pd.thin_pulses(cues, cap=3)
        self.assertEqual([c["t"] for c in kept], [100, 200, 300])

    def test_under_the_cap_is_untouched(self) -> None:
        cues = [{"t": t, "intensity": 0.3} for t in range(10)]
        self.assertIs(pd.thin_pulses(cues, cap=10), cues)

    def test_cap_is_the_documented_number(self) -> None:
        self.assertEqual(pd.PULSE_CAP, 200)


class TestGeneratorsAgreeUnderTheCap(unittest.TestCase):
    """Every real scene: ≤ cap pulse hits on both sides, same times."""

    doc: dict
    markers: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.doc = yaml.safe_load((ROOT / "scenes" / "scenes.yaml").read_text())
        mk = ROOT / "audio" / "markers.json"
        cls.markers = json.loads(mk.read_text()) if mk.exists() else {}

    def test_no_scene_exceeds_the_cap_and_both_sides_match(self) -> None:
        if not self.markers:
            self.skipTest("no rendered markers (make audio)")
        for scene in self.doc["scenes"]:
            raw = pe.pulse_cues(scene, self.markers)
            dev = pd.thin_pulses(raw)
            self.assertLessEqual(len(dev), pd.PULSE_CAP, scene["id"])
            prev = gp.to_previewer(scene, 0, "", self.markers)["cues"]
            hand = [c for c in (scene.get("cues") or []) if c.get("op") == "strike"]
            prev_strikes = [c for c in prev if c.get("op") == "strike"]
            # preview = hand-written strikes + the SAME thinned pulses
            self.assertEqual(
                len(prev_strikes),
                len(hand) + len(dev),
                f"{scene['id']}: preview and device disagree on hit count",
            )
            dev_ts = sorted(c["t"] for c in dev)
            prev_ts = sorted(c["t"] for c in prev_strikes)
            for t_ in dev_ts:
                self.assertIn(
                    t_,
                    prev_ts,
                    f"{scene['id']}: device hit at {t_} missing from preview",
                )
            if len(raw) > pd.PULSE_CAP:
                self.assertLess(len(dev), len(raw))


if __name__ == "__main__":
    unittest.main()
