"""Phase-2 stream dynamics parity: pan routing, tempo tails, local accents.

Split from test_generator_parity.py purely for the 500-line cap; same
duplication contract — these numbers are the numbers
web/test/track_lights_logic.mjs pins the TypeScript copy to.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# Importable both as `tests.test_stream_dynamics` and via discovery from
# inside the directory — the sibling carries the shared fixtures.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_generator_parity import PULSE_SCENE, dynamic_strikes, ge  # noqa: E402


class TestStreamDynamicsParity(unittest.TestCase):
    """Phase-2 stream dynamics: pan routing, tempo tails, local accents.

    Same duplication contract as TestPulseDynamicsParity — the numbers here
    are the numbers web/test/track_lights_logic.mjs pins the TS copy to.
    """

    MOVE = {"synth": "heartbeat", "zones": ["towerL", "towerR"],
            "alternate": True, "intensity": 0.6,
            "color": [1.0, 0.0, 0.0, 0.0], "ms": 110}

    def scene(self, **over) -> dict:
        return dict(PULSE_SCENE, pulse=[dict(self.MOVE, **over)])

    def test_decisive_pan_overrides_round_robin(self) -> None:
        m = {"parity": {"heartbeat": [[0, 0.5, -0.6], [300, 0.5, 0.6],
                                      [600, 0.5, 0.1], [900, 0.5]]}}
        for side in ("esphome", "previewer"):
            zones = {t: z for t, z, *_ in dynamic_strikes(side, self.scene(), m)}
            self.assertEqual(zones[0], ("towerL",))     # panned hard left
            self.assertEqual(zones[300], ("towerR",))   # panned hard right
            self.assertEqual(zones[600], ("towerL",))   # centre-ish: round-robin
            self.assertEqual(zones[900], ("towerR",))   # no pan at all: same

    def test_pan_threshold_sits_at_a_quarter(self) -> None:
        m = {"parity": {"heartbeat": [[0, 0.5, 0.25], [300, 0.5, -0.24]]}}
        for side in ("esphome", "previewer"):
            zones = {t: z for t, z, *_ in dynamic_strikes(side, self.scene(), m)}
            self.assertEqual(zones[0], ("towerR",))     # 0.25 is decisive (>=)
            self.assertEqual(zones[300], ("towerR",))   # -0.24 is not: rr i=1

    def test_pan_only_moves_between_the_towers(self) -> None:
        """A door-only stream must ignore pan — there is no left door."""
        m = {"parity": {"heartbeat": [[0, 0.5, -0.9]]}}
        s = self.scene(zones=["door"], alternate=False)
        for side in ("esphome", "previewer"):
            (_t, z, *_), = dynamic_strikes(side, s, m)
            self.assertEqual(z, ("door",))

    def test_fast_material_gets_shorter_tails(self) -> None:
        m = {"parity": {"heartbeat": [[i * 250, 0.5] for i in range(8)]}}
        for side in ("esphome", "previewer"):
            cues = dynamic_strikes(side, self.scene(), m)
            # gap 0.25 s -> factor clamps at 0.7: ms floor(110*0.7+0.5) = 77.
            self.assertEqual({ms for *_r, ms in cues}, {77})
        # decay is not in the tuple; check it straight off the esphome cues.
        cues = ge.pulse_cues(self.scene(), m)
        self.assertEqual({c["decay"] for c in cues}, {0.8571})

    def test_slow_material_rings_longer(self) -> None:
        m = {"parity": {"heartbeat": [[i * 900, 0.5] for i in range(8)]}}
        for side in ("esphome", "previewer"):
            cues = dynamic_strikes(side, self.scene(), m)
            self.assertEqual({ms for *_r, ms in cues}, {176})   # x1.6, clamped
        self.assertEqual({c["decay"] for c in ge.pulse_cues(self.scene(), m)},
                         {0.9375})

    def test_seven_hits_are_not_a_tempo(self) -> None:
        m = {"parity": {"heartbeat": [[i * 250, 0.5] for i in range(7)]}}
        for side in ("esphome", "previewer"):
            self.assertEqual({ms for *_r, ms in dynamic_strikes(side, self.scene(), m)},
                             {110})

    def test_local_accent_fires_boost_below_the_global_bar(self) -> None:
        """vel 0.7 among 0.3s is an accent; a global 0.85 bar never sees it."""
        m = {"parity": {"heartbeat": [[0, 0.3], [200, 0.3], [400, 0.3],
                                      [600, 0.7]]}}
        s = self.scene(zones=["door"], alternate=False,
                       boost_at=0.85, boost_targets=["towerL", "towerR"])
        for side in ("esphome", "previewer"):
            zones = {t: z for t, z, *_ in dynamic_strikes(side, s, m)}
            self.assertEqual(zones[600], ("door", "towerL", "towerR"))
            self.assertEqual(zones[400], ("door",))

    def test_accent_needs_a_neighbourhood_and_a_floor(self) -> None:
        m = {"parity": {"heartbeat": [
            [0, 0.3], [200, 0.7],                       # only 1 prior: no
            [400, 0.3], [600, 0.3], [800, 0.3],
            [1000, 0.5],                                # 0.5 < 0.55 floor: no
        ]}}
        s = self.scene(zones=["door"], alternate=False,
                       boost_at=0.85, boost_targets=["towerL", "towerR"])
        for side in ("esphome", "previewer"):
            zones = {t: z for t, z, *_ in dynamic_strikes(side, s, m)}
            self.assertEqual(zones[200], ("door",))
            self.assertEqual(zones[1000], ("door",))



if __name__ == "__main__":
    unittest.main(verbosity=2)
