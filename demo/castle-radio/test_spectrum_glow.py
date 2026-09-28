"""Option 16 — Spectrum, any-colour glow — on option 12's synthetic song. No
library track, audio or device."""

import unittest

import spectrum_glow
import spectrum_show
from ensemble_show import blend
from lab_song import source
from pulse_clarity import encode_preview
from sections_v2 import PALETTE
from spectrum_glow import MOOD, any_colour, door_of, poles, towers_of
from test_ensemble_show import chart, ensemble_song, voice
from test_spin_show import melody


def rec(t, targets, **extra):
    return {"t": t, "bus": "LED", "op": "look", "targets": targets, **extra}


class Glow(unittest.TestCase):
    def setUp(self):
        layers, self.duration = ensemble_song()
        self.source = source(self.duration)
        self.args = (self.source, layers, melody(self.duration, 2.0),
                     voice(self.duration), chart(self.duration))  # fmt: skip
        self.parts, self.cues = spectrum_show.spectrum(*self.args)
        self.d = self.parts.d
        self.show = spectrum_glow.choreograph_spectrum_glow(*self.args)

    def test_it_is_option_15s_hits_without_a_palette(self):
        self.assertTrue(encode_preview(self.show))
        self.assertFalse([c for c in self.show["cues"] if "palette" in c])
        hits = [c for c in self.show["cues"] if c["op"] == "strike"]
        spectrum = spectrum_show.choreograph_spectrum(*self.args)
        self.assertEqual(hits, [c for c in spectrum["cues"] if c["op"] == "strike"])

    def test_every_section_starts_with_its_own_colours(self):
        glow = self.show["preview"]
        self.assertEqual(glow[0]["t"], 0)
        first = self.d.plans[0].look
        self.assertEqual(glow[0]["glow"], towers_of(first))
        for p in self.d.plans:
            at = [g for g in glow if g["t"] == p.section.start]
            self.assertIn(towers_of(p.look), [g["glow"] for g in at])
            self.assertIn(door_of(p.look), [g["glow"] for g in at])
        self.assertEqual(glow, sorted(glow, key=lambda g: g["t"]))

    def test_a_record_with_more_than_a_palette_keeps_the_rest(self):
        t = self.d.plans[0].phrase.start + 5
        both = rec(t, ["towerL", "door"], palette="ember", effect="seance")
        card, glow = any_colour([both], self.d)
        self.assertEqual(card, [rec(t, ["towerL", "door"], effect="seance")])
        look = self.d.look_at(t)
        self.assertIn(rec(t, ["towerL"], glow=towers_of(look)), glow)
        self.assertIn(rec(t, ["door"], glow=door_of(look)), glow)

    def test_the_race_hands_a_tower_the_next_sections_glow(self):
        later = self.d.plans[-1]
        t = later.section.start - 10
        _card, glow = any_colour([rec(t, ["towerR"], palette="haunt")], self.d)
        self.assertIn(rec(t, ["towerR"], glow=towers_of(later.look)), glow)
        last = self.duration + 10
        _card, glow = any_colour([rec(last, ["towerL"], palette="haunt")], self.d)
        self.assertIn(rec(last, ["towerL"], glow=towers_of(self.d.look_at(last))), glow)

    def test_a_moods_glow_leans_toward_its_tint(self):
        t = self.d.plans[0].phrase.start + 5
        look = self.d.look_at(t)
        name = next(m for m in MOOD if m != PALETTE.get(look.name))
        mood = rec(t, ["towerL", "towerR"], palette=name)
        card, glow = any_colour([mood], self.d)
        self.assertEqual(card, [])
        tint = MOOD[name]
        want = poles(blend(look.a, tint, 0.5), blend(look.b, tint, 0.5))
        self.assertIn(rec(t, ["towerL", "towerR"], glow=want), glow)

    def test_the_towers_own_palette_is_their_themes_glow(self):
        t = self.d.plans[0].phrase.start + 5
        look = self.d.look_at(t)
        own = rec(t, ["towerL", "towerR"], palette=PALETTE.get(look.name, "haunt"))
        _card, glow = any_colour([own], self.d)
        self.assertIn(rec(t, ["towerL", "towerR"], glow=towers_of(look)), glow)


if __name__ == "__main__":
    unittest.main()
