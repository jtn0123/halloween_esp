"""Option 13 — Colour — on option 12's synthetic song. No library track,
audio or device."""

import unittest
from types import SimpleNamespace

import colour_show
import spin_show
from colour_show import ACCENT, COLOURS, family, hue
from ensemble_show import ensemble
from lab_song import source
from looks import BY_NAME, GREEN, RED, WHITE
from pulse_clarity import encode_preview
from sections_show import draft
from test_ensemble_show import chart, ensemble_song, voice
from test_spin_show import melody


def apart(a, b):
    d = abs(hue(a) - hue(b)) % 360
    return min(d, 360 - d)


def strikes(cues):
    return [c for c in cues if c["op"] == "strike"]


class Looks(unittest.TestCase):
    def test_every_look_answers_from_across_the_wheel(self):
        for name, look in COLOURS.items():
            self.assertNotEqual(family(look.b), "white", name)
            self.assertGreaterEqual(apart(look.a, look.b), colour_show.MIN_HUE, name)

    def test_every_accent_is_a_third_family(self):
        for name, look in COLOURS.items():
            self.assertNotIn(family(ACCENT[name]), {family(look.a), family(look.b)})

    def test_the_other_options_keep_their_looks(self):
        self.assertEqual(BY_NAME["Furnace"].b, RED)
        self.assertEqual(BY_NAME["Blood moon"].b, WHITE)

    def test_families_on_the_castle(self):
        self.assertEqual([family(c) for c in (WHITE, RED, GREEN)],
                         ["white", "warm", "green"])  # fmt: skip
        self.assertEqual(family(BY_NAME["Séance"].a), "magenta")
        self.assertEqual(family(BY_NAME["Séance"].b), "blue")

    def test_the_chorus_is_the_loudest_section_that_comes_back(self):
        def plan(label, energy):
            return SimpleNamespace(section=SimpleNamespace(label=label, energy=energy))

        plans = [plan(0, 0.99), plan(1, 0.7), plan(2, 0.5), plan(1, 0.7), plan(2, 0.5)]
        self.assertEqual(colour_show.chorus(plans), 1)
        self.assertEqual(colour_show.chorus(plans[:3]), 0)  # nothing comes back


class Colour(unittest.TestCase):
    def setUp(self):
        self.layers, self.duration = ensemble_song()
        self.source = source(self.duration)
        self.pitch = melody(self.duration, 2.0)
        self.parts = ensemble(self.source, self.layers, self.pitch,
                              voice(self.duration), chart(self.duration),
                              restyle=colour_show.restyle)  # fmt: skip
        self.d = self.parts.d
        self.show = colour_show.choreograph_colour(
            self.source, self.layers, self.pitch, voice(self.duration),
            chart(self.duration),
        )  # fmt: skip

    def test_it_is_a_card_the_new_firmware_plays_inside_the_song(self):
        self.assertEqual(self.show["firmware"], "v5.71")
        self.assertTrue(encode_preview(self.show))
        self.assertLessEqual(max(c["t"] for c in self.show["cues"]),
                             self.duration - spin_show.END)  # fmt: skip
        story = self.show["story"]
        self.assertLessEqual(max(story["families"].values()), colour_show.FAMILY_CAP)
        self.assertLess(story["white"], 0.1)

    def test_a_returning_section_leads_with_its_answer_and_the_last_chorus_accents(
        self,
    ):
        plain = draft(self.source, self.layers)
        chorus = colour_show.chorus(self.d.plans)
        for p, before in zip(self.d.plans, plain.plans, strict=True):
            own = COLOURS[before.look.name]
            sec = p.section
            if sec.label == chorus and sec.last_visit and sec.visit:
                self.assertEqual((p.look.a, p.look.b), (own.a, ACCENT[own.name]))
            elif sec.visit:
                self.assertEqual((p.look.a, p.look.b), (own.b, own.a))
            else:
                self.assertEqual((p.look.a, p.look.b), (own.a, own.b))

    def test_white_is_kept_for_the_big_moments(self):
        cues = colour_show.budget_white(self.parts.shown, self.d, self.parts.end.at)
        keep = colour_show._windows(self.d, self.parts.end.at)
        ones = colour_show.chorus_ones(self.d)
        self.assertTrue(ones)
        whites = [
            c for c in strikes(cues) if c["color"] == WHITE and not c.get("layer")
        ]
        self.assertTrue(whites)
        for c in whites:
            big = any(a <= c["t"] < b for a, b in keep)
            self.assertTrue(big or any(abs(c["t"] - t) <= 60 for t in ones), c["t"])
        for t in ones:
            self.assertTrue(any(c["t"] == t and c["color"] == WHITE for c in whites))

    def test_the_race_leaves_the_new_colour_behind_it(self):
        glows, held = colour_show.handoff(self.parts.shown, self.d)
        self.assertTrue(held)
        lit = strikes(glows)
        self.assertTrue(all(c["layer"] == 1 and c["pixels"] == "all" for c in lit))
        palettes = [c for c in glows if c["op"] == "look"]
        self.assertTrue(palettes)
        self.assertTrue(all(len(c["targets"]) == 1 for c in palettes))
        self.assertEqual({z for z, _a, _b in held}, {"towerL", "towerR", "door"})
        for z, a, b in held:
            hats = [c for c in strikes(self.show["cues"]) if c["targets"] == [z]
                    and c.get("layer") == 1 and c["pixels"] == "scatter"
                    and a < c["t"] < b]  # fmt: skip
            self.assertEqual(hats, [])

    def test_a_higher_note_is_paler_and_a_lower_one_deeper(self):
        shaded = colour_show.shade(self.parts.shown, self.d, self.pitch)
        sung = {at for at, _s, _r in self.d.notes}
        arcs = [c for c in strikes(shaded) if c["t"] in sung and c.get("layer") == 1
                and c["pixels"].startswith("arc")]  # fmt: skip
        self.assertGreater(len(arcs), 10)
        early, late = arcs[1], arcs[-2]  # the melody only climbs
        self.assertGreater(late["color"][3], early["color"][3])
        self.assertEqual(colour_show.shade(self.parts.shown, self.d, None),
                         self.parts.shown)  # fmt: skip

    def test_no_family_holds_more_than_its_share(self):
        green = [{**c, "color": GREEN} if c["op"] == "strike" else c
                 for c in self.parts.shown]  # fmt: skip
        self.assertEqual(colour_show.shares(green), {"green": 1.0})
        capped = colour_show.capped(green, self.d)
        self.assertLessEqual(
            colour_show.shares(capped)["green"], colour_show.FAMILY_CAP
        )
        balanced = colour_show.capped(self.show["cues"], self.d)
        self.assertEqual(balanced, self.show["cues"])  # already under: untouched


if __name__ == "__main__":
    unittest.main()
