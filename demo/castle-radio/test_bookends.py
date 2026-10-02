"""How a show begins and ends (option 12) on hand-built envelopes: one value
per 100 ms. No library track, audio or device."""

import unittest

import bookends
from looks import LOOKS, WHITE, ZONES

LOOK = LOOKS[0]
BARS = list(range(0, 120_000, 2000))


def env(*parts):
    """(seconds, level) runs, 10 frames a second."""
    out = []
    for seconds, level in parts:
        out += [level] * round(seconds * 10)
    return out


class Intro(unittest.TestCase):
    def test_the_band_comes_in_on_the_bar_it_lands_in(self):
        e = env((9.3, 0.02), (50, 0.8))
        self.assertEqual(bookends.band_in(e, len(e) * 100, BARS), 8000)

    def test_a_band_from_the_start_or_no_envelope_has_no_intro(self):
        self.assertEqual(bookends.band_in(env((60, 0.8)), 60000, BARS), 0)
        self.assertEqual(bookends.band_in([], 60000, BARS), 0)

    def test_one_huge_chorus_does_not_make_the_rest_an_intro(self):
        e = env((4, 0.02), (40, 0.4), (10, 1.0))
        self.assertEqual(bookends.band_in(e, len(e) * 100, BARS), 4000)

    def test_the_candles_never_burn_longer_than_the_cap(self):
        e = env((80, 0.02), (40, 0.8))
        self.assertEqual(bookends.band_in(e, len(e) * 100, BARS), bookends.MAX_INTRO)

    def test_the_candles_climb_until_the_look_arrives(self):
        self.assertEqual(bookends.intro(1000, LOOK, "chase"), [])
        cues = bookends.intro(8000, LOOK, "chase")
        self.assertEqual(cues[0]["overlay"], "none")
        self.assertEqual(cues[0]["targets"], list(ZONES))
        door = [c for c in cues if c["op"] == "set" and c["zone"] == "door"]
        candles = [c["level"] for c in door if c["eff"] == "candle"]
        self.assertEqual(candles, sorted(candles))
        self.assertEqual((candles[0], candles[-1]), bookends.CANDLE)
        self.assertTrue(all(c["t"] < 8000 for c in door if c["eff"] == "candle"))
        self.assertEqual((door[-1]["t"], door[-1]["eff"]), (8000, LOOK.door))
        self.assertEqual(cues[-1], {"t": 8000, "bus": "LED", "op": "look",
                                    "targets": ["door"], "overlay": "chase"})  # fmt: skip


class Ending(unittest.TestCase):
    def test_a_band_loud_to_the_last_moment_stops_cold(self):
        e = env((60, 1.0), (2, 0.0))
        end = bookends.ending(e, len(e) * 100, 59500)
        self.assertEqual(end, bookends.Ending("cold", 59500, 60000))

    def test_a_band_dying_away_is_a_fade_that_starts_where_it_did(self):
        e = env((50, 1.0)) + [1.0 - 0.087 * i / 10 for i in range(100)] + env((2, 0.0))
        end = bookends.ending(e, len(e) * 100, 59000)
        self.assertEqual(end.kind, "fade")
        # walked back from the tail to where it still held the level of 3-6 s
        # before the end: inside the fade, ahead of its last 1.5 s
        self.assertLess(end.at, end.end - 1500)
        self.assertGreater(end.at, 50000)

    def test_a_last_chord_left_to_ring_is_a_ring(self):
        e = env((59.8, 1.0), (0.2, 0.3), (2, 0.0))
        end = bookends.ending(e, len(e) * 100, 59800)
        self.assertEqual(end.kind, "ring")
        self.assertEqual(bookends.ending([], 60000, 1234).kind, "ring")


class Finale(unittest.TestCase):
    DUR = 62000

    def finale(self, kind, at=59000, end=60000, e=()):
        return bookends.finale(bookends.Ending(kind, at, end), LOOK, 500, self.DUR,
                               list(e) or env((62, 1.0)))  # fmt: skip

    def assert_inside(self, cues):
        self.assertTrue(cues)
        self.assertLessEqual(max(c["t"] for c in cues), self.DUR - bookends.LAST_MS)

    def test_a_cold_stop_slams_white_bursts_round_the_door_and_goes_black(self):
        cues = self.finale("cold")
        self.assert_inside(cues)
        self.assertEqual((cues[0]["t"], cues[0]["color"]), (59000, WHITE))
        arcs = {(c.get("layer"), c["pixels"]) for c in cues[1:] if c["op"] == "strike"}
        self.assertEqual(arcs, {(0, f"arc{k}") for k in range(1, 5)}
                         | {(1, f"arc{k}") for k in range(4, 8)})  # fmt: skip
        dark = [c for c in cues if c["op"] == "set"]
        self.assertEqual({(c["t"], c["eff"]) for c in dark}, {(59500, "off")})
        self.assertEqual(cues[-1]["overlay"], "none")

    def test_a_fade_dims_bar_by_bar_and_slows_the_chase(self):
        e = env((50, 1.0)) + [1.0 - 0.09 * i / 10 for i in range(100)] + env((2, 0.0))
        cues = self.finale("fade", at=52000, end=60000, e=e)
        self.assert_inside(cues)
        tower = [c["level"] for c in cues if c["op"] == "set" and c["zone"] == "towerL"]
        self.assertEqual(tower[:-1], sorted(tower[:-1], reverse=True))
        self.assertEqual(tower[-1], 0.0)
        rates = [c["rate"] for c in cues if c["op"] == "look"]
        self.assertEqual(rates, sorted(rates, reverse=True))
        last = max(cues, key=lambda c: c["t"])
        self.assertEqual((last["zone"], last["eff"]), ("door", "off"))

    def test_a_ringing_chord_holds_one_long_hit_and_sinks_to_embers(self):
        cues = self.finale("ring")
        self.assert_inside(cues)
        self.assertEqual((cues[0]["op"], cues[0]["color"]), ("strike", LOOK.a))
        self.assertEqual(
            cues[0]["decay"], max(c["decay"] for c in cues if "decay" in c)
        )
        door = [c for c in cues if c["op"] == "set" and c["zone"] == "door"]
        self.assertEqual([c["eff"] for c in door], ["candle", "off"])
        self.assertEqual([c["rate"] for c in cues if c["op"] == "look"], [0.1])

    def test_nothing_lands_after_the_songs_end(self):
        cues = bookends.finale(bookends.Ending("ring", 61900, 61990), LOOK, 500,
                               self.DUR, env((62, 1.0)))  # fmt: skip
        self.assert_inside(cues)
        self.assertEqual(max(c["t"] for c in cues), self.DUR - bookends.LAST_MS)


if __name__ == "__main__":
    unittest.main()
