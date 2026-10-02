"""Option 15 — Spectrum — on option 12's synthetic song. No library track,
audio or device."""

import colorsys
import unittest

import spectrum_show
from ensemble_show import ensemble
from lab_song import source
from looks import HOT_FROM
from pulse_clarity import encode_preview
from spectrum_show import GOLDEN, JITTER, SHIFT, hue_of, nearest, restyler, theme
from test_ensemble_show import chart, ensemble_song, voice
from test_spin_show import melody


def apart(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


class Wheel(unittest.TestCase):
    def test_a_quiet_theme_is_neighbours_and_a_loud_one_opposites(self):
        a, b, sung = theme(30.0, loud=False)
        self.assertLess(apart(hue_of(a), hue_of(b)), 60)
        a, b, sung = theme(30.0, loud=True)
        self.assertGreater(apart(hue_of(a), hue_of(b)), 140)
        self.assertLess(colorsys.rgb_to_hsv(*sung[:3])[1], 0.6)  # the singer is paler

    def test_neighbouring_kinds_of_passage_sit_apart(self):
        for label in range(6):
            self.assertGreater(apart(0, GOLDEN * (label + 1) - GOLDEN * label), 90)

    def test_the_nearest_palette(self):
        self.assertEqual(nearest(15), "ember")
        self.assertEqual(nearest(215), "moonlight")
        self.assertEqual(nearest(290), "haunt")
        self.assertEqual(nearest(100), "toxic")
        self.assertNotEqual(nearest(15, "ember"), "ember")

    def test_a_hit_turns_by_its_moment_and_white_stays_white(self):
        red = {"t": 1000, "op": "strike", "targets": ["door"], "color": [1, 0, 0, 0]}
        white = {**red, "color": [0.7, 0.7, 0.8, 1.0]}
        turned, kept = spectrum_show.jitter([red, white])
        self.assertLessEqual(apart(hue_of(turned["color"]), 0), JITTER + 0.5)
        self.assertEqual(kept, white)
        self.assertEqual(spectrum_show.jitter([red]), [turned])  # the same every time


class Spectrum(unittest.TestCase):
    def setUp(self):
        layers, self.duration = ensemble_song()
        self.source = source(self.duration)
        self.args = (self.source, layers, melody(self.duration, 2.0),
                     voice(self.duration), chart(self.duration))  # fmt: skip
        self.show = spectrum_show.choreograph_spectrum(*self.args)
        self.parts = ensemble(*self.args, restyle=restyler(7))

    def test_it_is_a_card_the_new_firmware_plays(self):
        self.assertEqual(self.show["firmware"], "v5.71")
        self.assertTrue(encode_preview(self.show))

    def test_each_kind_of_passage_has_its_own_theme_and_a_return_turns(self):
        for p in self.parts.d.plans:
            sec = p.section
            hue = (7 + GOLDEN * sec.label + SHIFT * sec.visit) % 360
            a, b, sung = theme(hue, sec.energy >= HOT_FROM)
            self.assertEqual((p.look.a, p.look.b, p.look.voice), (a, b, sung))

    def test_the_hits_spread_round_the_wheel(self):
        hues = {round(hue_of(c["color"]) / 30) % 12 for c in self.show["cues"]
                if c["op"] == "strike" and c["color"][3] <= 0.5}  # fmt: skip
        self.assertGreaterEqual(len(hues), 3)

    def test_the_glow_follows_each_section(self):
        records, plan = spectrum_show.glow(self.parts.d)
        self.assertEqual([t for t, _a, _b in plan],
                         sorted({p.section.start for p in self.parts.d.plans}))  # fmt: skip
        for _t, towers, door in plan:
            self.assertNotEqual(towers, door)
        self.assertEqual(len(records), 2 * len(plan))
        on_door = [c for c in self.show["cues"] if c["op"] == "look"
                and "door" in c["targets"] and "palette" in c]  # fmt: skip
        self.assertTrue(all(c["targets"] == ["door"] for c in on_door))


if __name__ == "__main__":
    unittest.main()
