"""Per-drum lights (option 12) on the synthetic song, its band doubling as
the drum stem. No library track, audio or device."""

import unittest

import drum_kit
import sections_show
import spin_show
from drum_kit import Kit
from lab_song import song, source

STOP = (45000, 46000)


def with_drums(layers, level=0.5):
    drums = dict(layers["backing"]["both"], level=level)
    return {**layers, "drums": {"both": drums}}


class DrumKit(unittest.TestCase):
    def setUp(self):
        layers, self.duration = song(stops=(STOP,))
        self.layers = with_drums(layers)
        self.d = sections_show.draft(
            source(self.duration), self.layers, halves=True, handover=spin_show.race
        )
        self.heads = spin_show.Heads([{"t": 0, "rate": 0.5, "head": 0.0}])
        self.kit = drum_kit.kit(self.layers)

    def test_a_quiet_or_missing_drum_stem_is_no_kit(self):
        self.assertIsNone(drum_kit.kit(self.layers | {"drums": {}}))
        self.assertIsNone(drum_kit.kit(with_drums(self.layers, level=0.1)))
        self.assertIsNotNone(self.kit)

    def test_a_band_keeps_its_strong_hits_scaled_to_the_ninetieth_percentile(self):
        hits = [[t / 10, 1.0] for t in range(10)] + [[5.05, 0.2], [7.0, 0.5]]
        band = drum_kit._band(sorted(hits), 90)
        self.assertNotIn(5050, [t for t, _s in band])  # bleed
        self.assertIn((7000, 0.5), band)
        self.assertEqual(drum_kit._band([], 90), ())

    def test_the_pattern_is_free_up_to_the_cut_and_never_in_a_stop(self):
        windows = drum_kit.free_windows(self.d)
        self.assertTrue(windows)
        self.assertFalse(any(a < STOP[1] and STOP[0] < b for a, b in windows))
        last = self.d.plans[-1].phrase
        self.assertEqual(windows[-1], (last.start, last.beats[-1]))

    def test_the_kit_is_playing_at_a_hit_a_bar_or_more(self):
        k = Kit(((1000, 1.0), (3000, 1.0)), (), ())
        self.assertTrue(drum_kit.playing(k, 0, 4000))
        self.assertFalse(drum_kit.playing(k, 0, 8000))

    def test_each_drum_has_its_own_light(self):
        k = Kit(kick=((10250, 1.0), (11500, 0.5)), snare=((10500, 1.0), (11510, 1.0)),
                hat=((10100, 1.0), (10300, 1.0)))  # fmt: skip
        cues = {
            c["t"]: c for c in drum_kit.hits_in(k, self.d, self.heads, 10000, 12000)
        }
        look = self.d.look_at(10250)
        self.assertEqual(
            (cues[10250]["pixels"], cues[10250]["color"]), ("bottom", look.a)
        )
        self.assertTrue(cues[10500]["pixels"].startswith("arc"))
        self.assertEqual(cues[10500]["color"], look.b)
        together = cues[11510]
        self.assertEqual((together["pixels"], together["color"]), ("all", look.a))
        self.assertNotIn(11500, cues)  # the kick is part of the snare's hit
        hats = [cues[10100], cues[10300]]
        self.assertEqual([h["targets"] for h in hats], [["towerL"], ["towerR"]])
        self.assertTrue(all(h["layer"] == 1 and h["pixels"] == "scatter" for h in hats))

    def test_the_kit_takes_over_the_patterns_tower_hits_and_leaves_the_rest(self):
        band = spin_show.turning(self.d.cues, self.heads, self.d.grid)
        drummed = drum_kit.drummed(band, self.kit, self.d, self.heads)
        ones = spin_show.downbeats(self.d.grid)
        a, b, pattern = next(
            (a, b, hits) for a, b in drum_kit.free_windows(self.d)
            if (hits := [c for c in band
                         if a <= c["t"] < b and drum_kit._pattern_hit(c, ones)])
        )  # fmt: skip
        towers = [c for c in drummed if a <= c["t"] < b and c["op"] == "strike"
                  and c["targets"][0].startswith("tower")]  # fmt: skip
        self.assertTrue(any(c["pixels"] == "bottom" for c in towers))
        for cue in pattern:
            self.assertNotIn(cue, drummed)
        on_one = [c for c in band if any(abs(c["t"] - o) <= 40 for o in ones)]
        self.assertTrue(all(c in drummed for c in on_one))
        self.assertEqual(drum_kit.drummed(band, None, self.d, self.heads), band)


if __name__ == "__main__":
    unittest.main()
