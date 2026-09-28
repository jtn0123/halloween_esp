"""Option 12 — Ensemble — on the synthetic song: its band doubling as the
drum stem, a hand-built chord chart, singer and voice. No library track,
audio or device."""

import tempfile
import unittest
from pathlib import Path

import bookends
import ensemble_show
import show_lab
import spin_show
from harmony import Chroma
from lab_song import START, song, source
from pulse_clarity import encode_preview
from structure import absolute
from test_harmony import note, triad
from test_spin_show import melody
from voice_kinds import VoiceTrack
from voice_pitch import Pitch

G, DB = 7, 1
QUIET_UNTIL = 9000  # the band's first bars are near silence: a candle intro
CHOIR = (30000, 40000)


def chart(duration):
    """G for two bars, Db (a tritone away: strange) for two, 100 ms frames."""
    bass, other = [], []
    for t in range(0, duration, 100):
        root = DB if (t - START) // 2000 % 4 >= 2 else G
        bass.append(note(root))
        other.append(triad(root))
    n = len(bass)
    return Chroma(100.0, tuple(bass), tuple(other), (1.0,) * n)


def voice(duration):
    frames = duration // 10
    inside = [CHOIR[0] // 10 <= i < CHOIR[1] // 10 for i in range(frames)]
    return VoiceTrack(
        tuple(0.4 if c else 0.05 for c in inside),
        (1000.0,) * frames,
        tuple(0.3 if c else 0.1 for c in inside),
    )


def ensemble_song():
    layers, duration = song(stops=((45000, 46000),))
    band = dict(layers["backing"]["both"])
    n = len(band["peaks"])
    band["peaks"] = [0.02 if i * duration / n < QUIET_UNTIL else p
                     for i, p in enumerate(band["peaks"])]  # fmt: skip
    layers = {**layers, "backing": {**layers["backing"], "both": band},
              "drums": {"both": dict(band, level=0.5)}}  # fmt: skip
    return layers, duration


def strikes(cues):
    return [c for c in cues if c["op"] == "strike"]


class Ensemble(unittest.TestCase):
    def setUp(self):
        self.layers, self.duration = ensemble_song()
        self.source = source(self.duration)
        self.show = ensemble_show.choreograph_ensemble(
            self.source, self.layers, melody(self.duration, 2.0),
            voice(self.duration), chart(self.duration),
        )  # fmt: skip
        self.cues = self.show["cues"]

    def test_it_is_a_card_the_new_firmware_plays_inside_the_song(self):
        self.assertEqual(self.show["firmware"], "v5.71")
        self.assertTrue(encode_preview(self.show))
        self.assertLessEqual(max(c["t"] for c in self.cues),
                             self.duration - spin_show.END)  # fmt: skip

    def test_the_story_says_what_it_heard(self):
        story = self.show["story"]
        self.assertTrue(story["drums"])
        self.assertEqual(set(story["moods"]), {"home", "strange"})
        self.assertGreater(story["choir"], 0)
        self.assertEqual(story["spoken"], 0)  # the melody sits on its notes
        self.assertTrue(story["intro"])
        self.assertIn(story["ending"], ("cold", "fade", "ring"))

    def test_the_band_waits_for_its_entrance_behind_candles(self):
        env = absolute(self.layers["backing"]["both"])
        first = bookends.band_in(env, self.duration, spin_show.downbeats(
            ensemble_show.draft(self.source, self.layers).grid))  # fmt: skip
        self.assertGreaterEqual(first, bookends.INTRO_MS)
        towers = [c for c in strikes(self.cues) if c["t"] < first
                  and c["targets"][0].startswith("tower")]  # fmt: skip
        self.assertEqual(towers, [])
        candles = [c for c in self.cues if c["op"] == "set" and c["t"] < first]
        self.assertTrue(candles)
        self.assertTrue(all(c["eff"] == "candle" for c in candles))

    def test_strange_bars_turn_the_chase_toxic(self):
        palettes = [c["palette"] for c in self.cues
                    if c["op"] == "look" and c.get("palette")
                    and c["targets"] == ensemble_show.TOWERS]  # fmt: skip
        self.assertIn("toxic", palettes)
        self.assertGreater(len(palettes), 2)  # it changes back

    def test_a_choir_swells_on_the_towers_and_the_hats_keep_out_of_it(self):
        swells = [c for c in strikes(self.cues) if c.get("layer") == 1
                  and c["pixels"] == "ring"]  # fmt: skip
        self.assertTrue(swells)
        self.assertTrue(all(CHOIR[0] - 500 <= c["t"] < CHOIR[1] for c in swells))
        hats = [c for c in strikes(self.cues) if c.get("layer") == 1
                and c["pixels"] == "scatter" and c["targets"][0].startswith("tower")]  # fmt: skip
        self.assertTrue(hats)
        for swell in swells:
            self.assertFalse(any(swell["t"] <= h["t"] < swell["t"] + 500 for h in hats))

    def test_a_spoken_line_turns_the_door_to_watching_eyes(self):
        frames = self.duration // 10
        glide = Pitch(10, tuple(None if i % 40 >= 35 else 60 + 0.06 * (i % 40)
                                for i in range(frames)))  # fmt: skip
        show = ensemble_show.choreograph_ensemble(self.source, self.layers, glide)
        self.assertGreater(show["story"]["spoken"], 0)
        eyes = [c for c in show["cues"] if c["op"] == "set" and c["zone"] == "door"
                and c["eff"] == "eyes"]  # fmt: skip
        self.assertTrue(eyes)
        pale = [c for c in strikes(show["cues"]) if c["color"] == ensemble_show.PALE]
        self.assertTrue(all(c["layer"] == 1 and c["targets"] == ["door"] for c in pale))

    def test_the_lab_builds_it_with_whatever_stems_are_there(self):
        with tempfile.TemporaryDirectory() as tmp:
            analysis = Path(tmp) / "stems" / "analysis.json"
            analysis.parent.mkdir()
            show = show_lab.ensemble_for(self.source, self.layers, analysis,
                                         Path(tmp), "radio_test")  # fmt: skip
        self.assertEqual(show["story"]["moods"], {})  # no bass or other stem
        self.assertEqual(show["story"]["choir"], 0)


class Tint(unittest.TestCase):
    def test_a_bars_mood_recolours_its_hits_but_not_a_white_slam(self):
        layers, duration = ensemble_song()
        d = ensemble_show.draft(source(duration), layers)
        bars = ensemble_show.bars_of(d)
        t = bars[3][0] + 500
        look = d.look_at(t)
        hit = {"t": t, "op": "strike", "color": look.a}
        white = {"t": t, "op": "strike", "color": ensemble_show.WHITE}

        def tinted(mood):
            moods = [mood] * len(bars)
            return ensemble_show.tint([hit, white], moods, bars, d)

        self.assertEqual(tinted("home"), [hit, white])
        self.assertEqual(tinted(None), [hit, white])
        self.assertEqual(tinted("away")[0]["color"], look.b)
        self.assertEqual(tinted("away")[1], white)
        shadow = tinted("shadow")[0]["color"]
        self.assertEqual(
            shadow, ensemble_show.blend(look.a, ensemble_show.SHADOW, 0.55)
        )


if __name__ == "__main__":
    unittest.main()
