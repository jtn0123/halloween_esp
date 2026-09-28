"""Option 14 — Palette — on option 12's synthetic song. No library track,
audio or device."""

import unittest
from itertools import pairwise

import colour_show
import palette_show
from ensemble_show import ensemble
from lab_song import source
from looks import BY_NAME
from palette_show import DOOR, DRESS, TOWER_ALT, door_at, doors
from pulse_clarity import encode_preview
from sections_v2 import PALETTE
from test_ensemble_show import chart, ensemble_song, voice
from test_spin_show import melody

#: The effects the firmware colours by the zone's palette (castle_effects.h).
PALETTED = {"seance", "wisp", "mansion", "throb", "chill"}


def looks_of(cues, zones):
    return [c for c in cues if c["op"] == "look" and c["targets"] == zones]


class Table(unittest.TestCase):
    def test_no_door_rests_on_a_fixed_red(self):
        for name, (_t, _tl, door, _dl) in DRESS.items():
            self.assertNotIn(door, {"blood", "ember", "eyes", "candle"}, name)
            self.assertIn(name, BY_NAME)

    def test_every_door_palette_stands_apart_from_its_towers(self):
        for name, (a, b) in DOOR.items():
            self.assertNotEqual(a, b, name)
            own = palette_show.TOWER_PALETTE.get(name, PALETTE[name])
            towers = {own, palette_show.NEAR.get(own, own)}
            self.assertFalse(towers & {a, b}, name)

    def test_an_alternate_palette_is_worn_only_where_the_towers_take_one(self):
        for name, alt in TOWER_ALT.items():
            self.assertIn(DRESS[name][0], PALETTED, name)
            self.assertNotEqual(
                alt, palette_show.TOWER_PALETTE.get(name, PALETTE[name])
            )


class Palette(unittest.TestCase):
    def setUp(self):
        self.layers, self.duration = ensemble_song()
        self.source = source(self.duration)
        self.pitch = melody(self.duration, 2.0)
        self.parts = ensemble(self.source, self.layers, self.pitch,
                              voice(self.duration), chart(self.duration),
                              restyle=palette_show.restyle)  # fmt: skip
        self.d = self.parts.d
        self.show = palette_show.choreograph_palette(
            self.source, self.layers, self.pitch, voice(self.duration),
            chart(self.duration),
        )  # fmt: skip
        self.colour = colour_show.choreograph_colour(
            self.source, self.layers, self.pitch, voice(self.duration),
            chart(self.duration),
        )  # fmt: skip

    def test_it_is_a_card_the_new_firmware_plays(self):
        self.assertEqual(self.show["firmware"], "v5.71")
        self.assertTrue(encode_preview(self.show))
        for tower in ("towerL", "towerR"):
            self.assertNotIn("center", self.show["zones"][tower])

    def test_every_hit_is_where_option_13_put_it(self):
        def hits(show):
            return [(c["t"], tuple(c["targets"]), c["pixels"], c["color"])
                    for c in show["cues"] if c["op"] == "strike"]  # fmt: skip

        self.assertEqual(hits(self.show), hits(self.colour))
        self.assertEqual(self.show["story"], self.colour["story"])

    def test_the_looks_rest_in_palette_effects(self):
        for p in self.d.plans:
            towers, tl, door, dl = DRESS[p.look.name]
            self.assertEqual((p.look.towers, p.look.door), (towers, door))
            self.assertEqual((p.look.tower_level, p.look.door_level), (tl, dl))

    def test_the_door_wears_the_palette_its_song_has_worn_least(self):
        plan = doors(self.d)
        self.assertEqual(
            [t for t, _p in plan], sorted({p.section.start for p in self.d.plans})
        )
        for p in self.d.plans:
            self.assertIn(door_at(plan, p.section.start), DOOR[p.look.name])
        worn: dict[str, int] = {}
        for (t, pal), (end, _n) in zip(
            plan, [*plan[1:], (self.duration, "")], strict=True
        ):
            worn[pal] = worn.get(pal, 0) + end - t
        if len(plan) > 2:
            self.assertGreater(len(worn), 1)
        door = [c for c in self.show["cues"] if c["op"] == "look" and "door" in c["targets"]
                and "palette" in c]  # fmt: skip
        self.assertTrue(door)
        for c in door:
            self.assertEqual(c["targets"], ["door"])
            self.assertEqual(c["palette"], door_at(plan, c["t"]))

    def test_a_long_section_turns_its_towers_phrase_by_phrase(self):
        turns = palette_show.turns(self.d)
        self.assertTrue(turns)
        for i, p in enumerate(self.d.plans):
            want = palette_show._tower(self.d, i)
            if colour_show._nth(self.d, i) % 2 and p.look.name in TOWER_ALT:
                self.assertEqual(want, TOWER_ALT[p.look.name])
            self.assertEqual(palette_show.tower_at(self.d, p.phrase.start), want)
        shown = [c["palette"] for c in turns]
        self.assertTrue(all(a != b for a, b in pairwise(shown)))

    def test_a_moods_palette_is_left_alone(self):
        t = self.d.plans[0].phrase.start + 5
        own = PALETTE[self.d.look_at(t).name]
        other = next(p for p in ("toxic", "moonlight") if p != own)
        mood = {"t": t, "bus": "LED", "op": "look", "targets": ["towerL", "towerR"],
                "palette": other}  # fmt: skip
        out = palette_show.palettes([mood], self.d, doors(self.d))
        self.assertIn(mood, out)


if __name__ == "__main__":
    unittest.main()
