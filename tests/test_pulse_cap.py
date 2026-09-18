"""The pulse cap: why v5.25 would not boot, and why it is over.

ESPHome keeps static RAM per generated action; one dense track scene
(1,216 pulse hits -> 2,402 actions) ate ~32 KB of the then-S2's DRAM and the
image panicked before WiFi. So both generators kept each scene's strongest
PULSE_CAP hits, and the gate here was that they kept the SAME ones, or the
desk would lie about the porch.

v5.67 ended the shortage rather than the parity. A scene's cues are a file on
the card walked out of PSRAM (tools/gen_scene_cards.py), so a dense track
costs 20 bytes a hit in a 2 MB pool instead of a compiled action in a full
DRAM segment, and nothing on the device path thins at all. The desk stopped in
the same change (tools/gen_previewer.py). What the second class below checks
is therefore the inverse of what it used to: every hit on both sides, and the
two lists identical — the parity claim is unchanged, the number it is made
about is now "all of them".

`thin_pulses` itself is still live, still exact, and still held equal to
core/src/pulse.rs, because WHICH hits are strongest is what the importer's
preview ranks by. The first class is unchanged.
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
import gen_scene_cards as gc
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


class TestGeneratorsAgreeOnEveryHit(unittest.TestCase):
    """Every real scene: the desk's strikes and the card's, hit for hit."""

    doc: dict
    markers: dict

    @classmethod
    def setUpClass(cls) -> None:
        cls.doc = yaml.safe_load((ROOT / "scenes" / "scenes.yaml").read_text())
        mk = ROOT / "audio" / "markers.json"
        cls.markers = json.loads(mk.read_text()) if mk.exists() else {}

    def test_both_sides_keep_every_hit_and_the_same_ones(self) -> None:
        """Was `test_no_scene_exceeds_the_cap_and_both_sides_match`.

        Its two cap assertions are gone because the fact is gone: no scene is
        thinned, so "no scene exceeds the cap" has nothing to be true of and
        "a dense scene is shorter than its raw stream" is now false by design.
        What replaces them is stronger — not a subset relation between the two
        sides but equality, which is only checkable BECAUSE neither side drops
        anything.
        """
        if not self.markers:
            self.skipTest("no rendered markers (make audio)")
        dense = 0
        for scene in self.doc["scenes"]:
            with self.subTest(scene=scene["id"]):
                raw = pe.pulse_cues(scene, self.markers)
                dense += len(raw) > pd.PULSE_CAP
                # The card side: gen_scene_cards keeps authored cues plus
                # every expanded hit, and that is what cue_file encodes.
                card = gc.scene_cues(scene, self.markers)
                self.assertEqual(len(card), len(scene.get("cues") or []) + len(raw))

                prev = gp.to_previewer(scene, 0, "", self.markers)["cues"]
                hand = [c for c in (scene.get("cues") or []) if c.get("op") == "strike"]
                prev_strikes = [c for c in prev if c.get("op") == "strike"]
                self.assertEqual(len(prev_strikes), len(hand) + len(raw))
                # Same hits, in the same millisecond — the parity claim.
                self.assertEqual(
                    sorted(c["t"] for c in prev_strikes),
                    sorted(c["t"] for c in hand + raw),
                )
        # A guard on the guard: if no real scene is dense any more, this test
        # has stopped being about anything and the cap's removal is untested.
        self.assertGreater(dense, 0, "no scene has more hits than the old cap")


if __name__ == "__main__":
    unittest.main()
