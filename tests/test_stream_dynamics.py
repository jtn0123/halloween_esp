"""Phase-2 stream dynamics parity: pan routing, tempo tails, local accents.

Split from test_generator_parity.py purely for the 500-line cap; same
duplication contract — these numbers are the numbers
web/test/track_lights_logic.mjs pins the TypeScript copy to.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import ClassVar

# Importable both as `tests.test_stream_dynamics` and via discovery from
# inside the directory — the sibling carries the shared fixtures.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_generator_parity import PULSE_SCENE, dynamic_strikes, ge


class TestStreamDynamicsParity(unittest.TestCase):
    """Phase-2 stream dynamics: pan routing, tempo tails, local accents.

    Same duplication contract as TestPulseDynamicsParity — the numbers here
    are the numbers web/test/track_lights_logic.mjs pins the TS copy to.
    """

    MOVE: ClassVar[dict] = {
        "synth": "heartbeat",
        "zones": ["towerL", "towerR"],
        "alternate": True,
        "intensity": 0.6,
        "color": [1.0, 0.0, 0.0, 0.0],
        "ms": 110,
    }

    def scene(self, **over) -> dict:
        return dict(PULSE_SCENE, pulse=[dict(self.MOVE, **over)])

    def test_decisive_pan_overrides_round_robin(self) -> None:
        m = {
            "parity": {
                "heartbeat": [
                    [0, 0.5, -0.6],
                    [300, 0.5, 0.6],
                    [600, 0.5, 0.05],
                    [900, 0.5],
                ]
            }
        }
        for side in ("esphome", "previewer"):
            zones = {t: z for t, z, *_ in dynamic_strikes(side, self.scene(), m)}
            self.assertEqual(zones[0], ("towerL",))  # panned hard left
            self.assertEqual(zones[300], ("towerR",))  # panned hard right
            self.assertEqual(zones[600], ("towerL",))  # centre-ish: round-robin
            self.assertEqual(zones[900], ("towerR",))  # no pan at all: same

    def test_pan_threshold_sits_at_a_tenth(self) -> None:
        m = {"parity": {"heartbeat": [[0, 0.5, 0.10], [300, 0.5, -0.09]]}}
        for side in ("esphome", "previewer"):
            zones = {t: z for t, z, *_ in dynamic_strikes(side, self.scene(), m)}
            self.assertEqual(zones[0], ("towerR",))  # 0.10 is decisive (>=)
            self.assertEqual(zones[300], ("towerR",))  # -0.09 is not: rr i=1

    def test_pan_only_moves_between_the_towers(self) -> None:
        """A door-only stream must ignore pan — there is no left door."""
        m = {"parity": {"heartbeat": [[0, 0.5, -0.9]]}}
        s = self.scene(zones=["door"], alternate=False)
        for side in ("esphome", "previewer"):
            ((_t, z, *_),) = dynamic_strikes(side, s, m)
            self.assertEqual(z, ("door",))

    def test_fast_material_gets_shorter_tails(self) -> None:
        m = {"parity": {"heartbeat": [[i * 250, 0.5] for i in range(8)]}}
        for side in ("esphome", "previewer"):
            cues = dynamic_strikes(side, self.scene(), m)
            # gap 0.25 s -> factor clamps at 0.7: ms floor(110*0.7+0.5) = 77.
            self.assertEqual({ms for *_r, ms in cues}, {77})
        # decay is not in the tuple; check it straight off the esphome cues.
        cues = ge.pulse_cues(self.scene(), m)  # type: ignore[assignment]  # dict cues reuse the name
        self.assertEqual({c["decay"] for c in cues}, {0.8571})  # type: ignore[call-overload]  # dict cues

    def test_slow_material_rings_longer(self) -> None:
        m = {"parity": {"heartbeat": [[i * 900, 0.5] for i in range(8)]}}
        for side in ("esphome", "previewer"):
            cues = dynamic_strikes(side, self.scene(), m)
            self.assertEqual({ms for *_r, ms in cues}, {176})  # x1.6, clamped
        self.assertEqual({c["decay"] for c in ge.pulse_cues(self.scene(), m)}, {0.9375})

    def test_seven_hits_are_not_a_tempo(self) -> None:
        m = {"parity": {"heartbeat": [[i * 250, 0.5] for i in range(7)]}}
        for side in ("esphome", "previewer"):
            self.assertEqual(
                {ms for *_r, ms in dynamic_strikes(side, self.scene(), m)}, {110}
            )

    def test_local_accent_fires_boost_below_the_global_bar(self) -> None:
        """vel 0.7 among 0.3s is an accent; a global 0.85 bar never sees it."""
        m = {"parity": {"heartbeat": [[0, 0.3], [200, 0.3], [400, 0.3], [600, 0.7]]}}
        s = self.scene(
            zones=["door"],
            alternate=False,
            boost_at=0.85,
            boost_targets=["towerL", "towerR"],
        )
        for side in ("esphome", "previewer"):
            zones = {t: z for t, z, *_ in dynamic_strikes(side, s, m)}
            self.assertEqual(zones[600], ("door", "towerL", "towerR"))
            self.assertEqual(zones[400], ("door",))

    def test_accent_needs_a_neighbourhood_and_a_floor(self) -> None:
        m = {
            "parity": {
                "heartbeat": [
                    [0, 0.3],
                    [200, 0.7],  # only 1 prior: no
                    [400, 0.3],
                    [600, 0.3],
                    [800, 0.3],
                    [1000, 0.5],  # 0.5 < 0.55 floor: no
                ]
            }
        }
        s = self.scene(
            zones=["door"],
            alternate=False,
            boost_at=0.85,
            boost_targets=["towerL", "towerR"],
        )
        for side in ("esphome", "previewer"):
            zones = {t: z for t, z, *_ in dynamic_strikes(side, s, m)}
            self.assertEqual(zones[200], ("door",))
            self.assertEqual(zones[1000], ("door",))


class TestSectionGatingParity(unittest.TestCase):
    """#9/#5: strikes gated by the scene's own section set-cues.

    The section timeline exists only as the exported hush/verse/chorus/
    silence `set` cues; both generators must reconstruct the same gates from
    them, and apply the same table gateMul() applies in the browser.
    """

    GATED: ClassVar[dict] = {
        "synth": "onset_high",
        "zones": ["towerR"],
        "intensity": 0.72,
        "color": [0.0, 1.0, 0.0, 0.0],
        "ms": 130,
    }
    # hush until 5 s, chorus to 10 s, silence after. The predim cue must NOT
    # count as a boundary, and the three zones' cues dedupe to one gate.
    CUES = (
        [
            {
                "t": 0,
                "op": "set",
                "zone": z,
                "effect": "chill",
                "level": 0.4,
                "note": "hush",
            }
            for z in ("towerL", "towerR", "door")
        ]
        + [
            {
                "t": 4550,
                "op": "set",
                "zone": "towerL",
                "effect": "chill",
                "level": 0.18,
                "note": "predim",
            }
        ]
        + [
            {
                "t": 5000,
                "op": "set",
                "zone": z,
                "effect": "mansion",
                "level": 0.95,
                "note": "chorus",
            }
            for z in ("towerL", "towerR", "door")
        ]
        + [
            {
                "t": 10000,
                "op": "set",
                "zone": z,
                "effect": "chill",
                "level": 0.06,
                "note": "silence",
            }
            for z in ("towerL", "towerR", "door")
        ]
    )

    def scene(self, **over) -> dict:
        return dict(PULSE_SCENE, cues=self.CUES, pulse=[dict(self.GATED, **over)])

    M: ClassVar[dict] = {
        "parity": {
            "onset_high": [[1000, 0.8], [6000, 0.8], [11000, 0.8]],
            "onset_mid": [[1000, 0.8], [6000, 0.8], [11000, 0.8]],
            "onset_low": [[1000, 0.8], [6000, 0.8], [11000, 0.8]],
        }
    }

    def test_gates_deduplicate_and_skip_the_predim(self) -> None:
        self.assertEqual(
            ge.section_gates(self.scene()),
            [(0, "hush"), (5000, "chorus"), (10000, "silence")],
        )

    def test_highs_vanish_from_hush_and_silence(self) -> None:
        for side in ("esphome", "previewer"):
            times = [t for t, *_ in dynamic_strikes(side, self.scene(), self.M)]
            self.assertEqual(times, [6000])

    def test_mids_whisper_at_half_in_a_hush(self) -> None:
        s = self.scene(synth="onset_mid", intensity=0.80)
        for side in ("esphome", "previewer"):
            got = {t: i for t, _z, i, *_ in dynamic_strikes(side, s, self.M)}
            self.assertEqual(got, {1000: 0.32, 6000: 0.64})  # 0.8*0.8*{0.5,1}

    def test_lows_keep_their_hush_hits_and_lose_silence(self) -> None:
        s = self.scene(synth="onset_low")
        for side in ("esphome", "previewer"):
            times = [t for t, *_ in dynamic_strikes(side, s, self.M)]
            self.assertEqual(times, [1000, 6000])

    def test_attack_rides_through_both_generators(self) -> None:
        """#10: attack_ms on the stream becomes `attack` on every strike —
        the esphome emitter turns it into a target+rise, the previewer hands
        it to show.ts, and absent means 0/absent (the instant slam)."""
        import gen_previewer as gp

        s = self.scene(synth="onset_low", attack_ms=90)
        self.assertTrue(all(c["attack"] == 90 for c in ge.pulse_cues(s, self.M)))
        pv = [
            c for c in gp.to_previewer(s, 1, "", self.M)["cues"] if c["op"] == "strike"
        ]
        self.assertTrue(all(c.get("attack") == 90 for c in pv))
        plain = self.scene(synth="onset_low")
        self.assertTrue(all(c["attack"] == 0 for c in ge.pulse_cues(plain, self.M)))
        self.assertTrue(
            all(
                "attack" not in c
                for c in [
                    c
                    for c in gp.to_previewer(plain, 1, "", self.M)["cues"]
                    if c["op"] == "strike"
                ]
            )
        )

    def test_drift_walks_the_triad_identically(self) -> None:
        """#1: drift lerps AROUND the colour cycle by time. The half-period
        pin [0, 0.5, 0.5, 0] is the same digit the browser suite asserts."""
        import pulse_dynamics as pd

        cyc = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]]
        self.assertEqual(pd.drift_base(cyc, 0, 30000), [0.0, 0.5, 0.5, 0.0])
        self.assertEqual(pd.drift_base(cyc, 0, 0), [1, 0, 0, 0])
        m = {"parity": {"onset_low": [[0, 0.5], [30000, 0.5]]}}
        s = dict(
            PULSE_SCENE,
            pulse=[
                {
                    "synth": "onset_low",
                    "zones": ["door"],
                    "intensity": 0.6,
                    "colors": cyc,
                    "drift": True,
                    "ms": 110,
                }
            ],
        )
        a = dynamic_strikes("esphome", s, m)
        self.assertEqual(a, dynamic_strikes("previewer", s, m))
        by_t = {t: col for t, _z, _i, col, *_ in a}
        self.assertEqual(by_t[0], (1.0, 0.0, 0.0, 0.0))
        # hit index 1 at half period: pos = 1 + 1.5 -> lerp colors[2]->[0]
        self.assertEqual(by_t[30000], (0.5, 0.0, 0.5, 0.0))

    def test_takeover_unifies_the_chorus_only(self) -> None:
        """#2: chorus hits use the shared warm family; verse hits keep their
        own colours. Both generators agree."""
        import pulse_dynamics as pd

        m = {"parity": {"onset_low": [[1000, 1.0], [6000, 1.0]]}}
        s = dict(
            PULSE_SCENE,
            cues=self.CUES,
            pulse=[
                {
                    "synth": "onset_low",
                    "zones": ["door"],
                    "intensity": 0.6,
                    "colors": [[1, 0, 0, 0]],
                    "color_hot": [1, 0, 0, 0],
                    "takeover": True,
                    "ms": 110,
                }
            ],
        )
        a = dynamic_strikes("esphome", s, m)
        self.assertEqual(a, dynamic_strikes("previewer", s, m))
        by_t = {t: col for t, _z, _i, col, *_ in a}
        self.assertEqual(by_t[1000], (1.0, 0.0, 0.0, 0.0))  # hush: own colour
        self.assertEqual(by_t[6000], tuple(pd.TAKEOVER_HOT))  # chorus, vel 1.0

    def test_a_scene_without_section_cues_is_untouched(self) -> None:
        s = dict(PULSE_SCENE, pulse=[dict(self.GATED)])
        for side in ("esphome", "previewer"):
            self.assertEqual(len(dynamic_strikes(side, s, self.M)), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
