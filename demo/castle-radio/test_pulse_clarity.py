"""Behavioral boundaries for the opt-in pulse-clarity experiment."""

import copy
import unittest

from pulse_clarity import clarify, encode_preview
from rich_show import preview_from_blob


def hit(t, intensity=1, targets=None, attack=0, decay=0.95):
    return dict(
        t=t,
        op="strike",
        bus="LED",
        targets=targets or ["towerL"],
        intensity=intensity,
        attack=attack,
        decay=decay,
        pixels="scatter",
        color=[1, 0.2, 0.4, 0],
    )


def scene(cues):
    return dict(id="fixture", dur=10000, base={}, levels={}, zones={}, cues=cues)


class PulseClarity(unittest.TestCase):
    def test_competing_hits_keep_strongest_at_original_time(self):
        source = scene([hit(100, 0.3), hit(120, 1), hit(140, 0.4), hit(220)])
        self.assertEqual([c["t"] for c in clarify(source)["cues"]], [120, 220])

    def test_independent_fixtures_do_not_suppress_each_other(self):
        source = scene(
            [hit(100, targets=["towerL", "door"]), hit(120, targets=["towerR"])]
        )
        out = clarify(source)["cues"]
        self.assertEqual({c["targets"][0] for c in out}, {"towerL", "towerR", "door"})
        self.assertEqual([c["t"] for c in out], [100, 100, 120])

    def test_dense_pulses_recover_and_attack_leaves_a_tail(self):
        out = clarify(scene([hit(0, attack=90), hit(160)]))["cues"]
        self.assertLessEqual(out[0]["attack"], 40)
        ticks = (160 - out[0]["attack"]) // 16 - 1
        self.assertLessEqual(out[0]["decay"] ** ticks, 0.151)

    def test_sparse_hits_and_long_swells_are_preserved(self):
        cues = [hit(0), hit(1000, attack=300), hit(1100, 0.3), hit(4000)]
        out = clarify(scene(cues))["cues"]
        self.assertEqual(out[0], cues[0])
        self.assertIn(cues[1], out)
        self.assertIn(cues[3], out)

    def test_no_transitive_cluster_erases_a_long_passage(self):
        out = clarify(scene([hit(t) for t in range(0, 1000, 32)]))["cues"]
        self.assertEqual([c["t"] for c in out], list(range(0, 1000, 64)))

    def test_original_and_authored_effects_unchanged(self):
        setting = dict(t=0, op="set", bus="LED", zone="towerL", eff="chill", level=0.4)
        source = scene([setting, hit(0), hit(80)])
        saved = copy.deepcopy(source)
        candidate = clarify(source)
        self.assertEqual(source, saved)
        self.assertEqual(candidate["cues"][0], setting)
        decoded = preview_from_blob("fixture", encode_preview(candidate))
        self.assertEqual(decoded["cues"][0], setting)
        self.assertEqual(decoded["cues"][1]["color"], [1, 0.2, 0.4, 0])
        self.assertEqual(decoded["cues"][1]["pixels"], "scatter")


if __name__ == "__main__":
    unittest.main()
