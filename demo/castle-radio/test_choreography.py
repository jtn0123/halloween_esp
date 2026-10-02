"""The beat grid and the choreography candidates, on a synthetic song.

No library track, audio or device is touched: the 'song' is a list of onset
times at a known tempo, and the lab writes into a temporary directory.
"""

import json
import tempfile
import unittest
from itertools import pairwise
from pathlib import Path

import beat_grid
import choreography
import show_lab
from pulse_clarity import encode_preview
from rich_show import preview_from_blob

BPM = 120
BEAT = 60000 // BPM
DURATION = 64_000


def song(loud_from=32_000):
    """Kick on every beat, loudest on 'one'; hats on the off-beats; the second
    half is louder; a voice enters at 8 s."""
    low, high = [], []
    for i, at in enumerate(range(1000, DURATION - 500, BEAT)):
        low.append([at / 1000, 1.0 if i % 4 == 0 else 0.5])
        high.append([(at + BEAT // 2) / 1000, 0.3])
    peaks = [0.3 if i * DURATION / 640 < loud_from else 0.9 for i in range(640)]
    onsets = {"onset_low": low, "onset_mid": [], "onset_high": high}
    band = {"onsets": onsets, "peaks": peaks, "level": 1.0}
    voice = [[t / 1000, 0.8] for t in range(8130, DURATION - 500, 730)]
    sung = {"onsets": {"onset_mid": voice}, "peaks": [0.5] * 640, "level": 1.0}
    return {
        "backing": {"both": band, "left": band, "right": band},
        "vocals": {"both": sung},
    }


SOURCE = {
    "id": "radio_test", "name": "Test song", "dur": DURATION,
    "zones": {"towerL": {"palette": "haunt"}, "towerR": {"palette": "moonlight"},
              "door": {"overlay": "sparkle", "palette": "ember"}},
}  # fmt: skip


class BeatGrid(unittest.TestCase):
    def setUp(self):
        self.grid = beat_grid.analyse(song(), DURATION)

    def test_the_tempo_and_the_beats_are_the_songs(self):
        self.assertAlmostEqual(self.grid.bpm, BPM, delta=3)
        inside = [b for b in self.grid.beats if 2000 < b < DURATION - 2000]
        self.assertTrue(all((b - 1000) % BEAT == 0 for b in inside), inside[:8])
        self.assertGreater(len(inside), 100)

    def test_one_is_where_the_kick_is_loudest(self):
        first = self.grid.beats[self.grid.downbeat]
        self.assertEqual((first - 1000) % (4 * BEAT), 0)

    def test_phrases_are_four_bars_and_the_loud_half_outranks_the_quiet(self):
        whole = self.grid.phrases[1:-1]
        self.assertTrue(all(len(p.beats) == 16 for p in whole))
        quiet = [p.rank for p in self.grid.phrases if p.end <= 32_000]
        loud = [p.rank for p in self.grid.phrases if p.start >= 34_000]
        self.assertLess(max(quiet), min(loud))

    def test_a_song_with_no_onsets_still_answers(self):
        empty = {"backing": {"both": {"onsets": {}, "peaks": [], "level": 0}}}
        grid = beat_grid.analyse(empty, 5000)
        self.assertTrue(all(0 <= b < 5000 for b in grid.beats))


class Patterns(unittest.TestCase):
    def setUp(self):
        self.grid = beat_grid.analyse(song(), DURATION)
        self.phrase = self.grid.phrases[2]
        self.look = choreography.LOOKS[0]

    def test_pingpong_trades_towers_and_lands_everything_on_one(self):
        cues = choreography.pingpong(self.phrase, self.look)
        targets = [tuple(c["targets"]) for c in cues[:4]]
        self.assertEqual(targets[0], choreography.ZONES)
        self.assertNotEqual(targets[1], targets[2])
        self.assertEqual(targets[1], targets[3])

    def test_a_sweep_crosses_the_door_and_turns_round_each_bar(self):
        cues = choreography.chase(self.phrase, self.look)
        first = [c["targets"][0] for c in cues[:3]]
        second_bar = [c["targets"][0] for c in cues[12:15]]
        self.assertEqual(first, ["towerL", "door", "towerR"])
        self.assertEqual(second_bar, first[::-1])
        self.assertTrue(all(a["t"] < b["t"] for a, b in pairwise(cues[:3])))

    def test_a_flash_is_back_down_before_the_next_beat(self):
        for cue in choreography.pingpong(self.phrase, self.look):
            ticks = BEAT / choreography.TICK_MS
            self.assertLess(cue["intensity"] * cue["decay"] ** ticks, 0.2)

    def test_a_drop_is_a_roll_then_darkness_then_one_slam(self):
        cues = choreography.drop(self.phrase, choreography.LOOKS[1])
        strikes = [c for c in cues if c["op"] == "strike"]
        dark = [c for c in cues if c["op"] == "set"]
        self.assertEqual([c["level"] for c in dark], [0.0, 0.0, 0.0])
        roll, slam = strikes[:-1], strikes[-1]
        self.assertEqual(
            [c["intensity"] for c in roll], sorted(c["intensity"] for c in roll)
        )
        self.assertTrue(all(c["t"] < dark[0]["t"] < slam["t"] for c in roll))
        self.assertEqual((slam["t"], slam["intensity"]), (self.phrase.end, 1.0))


class Placement(unittest.TestCase):
    def test_an_ornament_never_cuts_a_live_hit_or_crowds_the_next(self):
        placer = choreography.Placer()
        placer.fixed([choreography.strike(1000, ["door"], [1, 0, 0, 0], 1.0, 500)])
        placer.fixed([choreography.strike(2000, ["door"], [1, 0, 0, 0], 1.0, 500)])
        placer.settle()

        def small(t):
            return choreography.strike(t, ["door"], [0, 1, 0, 0], 0.4, 200, "scatter")

        self.assertFalse(placer.ornament(small(1100)))  # the hit is still bright
        self.assertTrue(placer.ornament(small(1700)))  # it has faded
        self.assertFalse(placer.ornament(small(1950)))  # the next hit is 50 ms off
        placer.reserved.append((3000, 4000))
        self.assertFalse(placer.ornament(small(3500)))  # a roll owns this window


class Candidates(unittest.TestCase):
    def test_every_style_survives_the_card_format(self):
        for name, style in choreography.STYLES.items():
            with self.subTest(style=name):
                planned = choreography.choreograph(SOURCE, song(), style)
                decoded = preview_from_blob("radio_test", encode_preview(planned))
                self.assertEqual(decoded["dur"], DURATION)
                self.assertEqual(len(decoded["cues"]), len(planned["cues"]))
                self.assertTrue(all(0 <= c["t"] < DURATION for c in decoded["cues"]))
                looks = {c["t"] for c in decoded["cues"] if c["op"] == "set"}
                self.assertGreaterEqual(len(looks), len(planned["sections"]))

    def test_the_full_show_drops_into_the_loud_half_and_keeps_its_slam(self):
        planned = choreography.choreograph(SOURCE, song(), choreography.STYLES["show"])
        drops = [s for s in planned["sections"] if s["drop"]]
        self.assertTrue(drops)
        for section in drops:
            at = [
                c for c in planned["cues"]
                if c["op"] == "strike" and abs(c["t"] - section["end"]) <= 120
                and "door" in c["targets"] and "towerL" in c["targets"]
            ]  # fmt: skip
            self.assertEqual(
                len(at), 1, "a second downbeat strike would replace the slam"
            )
            self.assertEqual(at[0]["color"], choreography.WHITE)

    def test_the_door_is_the_singers_while_there_is_singing(self):
        planned = choreography.choreograph(SOURCE, song(), choreography.STYLES["duet"])
        slams = {s["end"] for s in planned["sections"] if s["drop"]}
        door = [
            c for c in planned["cues"]
            if c["op"] == "strike" and "door" in c["targets"] and c["t"] not in slams
        ]  # fmt: skip
        sung = [c for c in door if c["t"] >= 8130]  # the voice enters here
        self.assertTrue(sung)
        self.assertTrue(all(c["targets"] == ["door"] for c in sung[:-1]), sung[:3])
        voice = choreography.LOOKS[0].voice
        self.assertIn(voice, [c["color"] for c in sung])

    def test_an_instrumental_passage_gives_the_door_back_to_the_band(self):
        grid = beat_grid.analyse(song(), DURATION)
        cues = choreography.stomp(grid.phrases[2], choreography.LOOKS[0])
        first = cues[0]["t"]
        kept = choreography.without_door(cues, [(first + 5000, 0.8)])
        self.assertEqual(kept[0]["targets"], list(choreography.ZONES))
        late = [c for c in kept if abs(c["t"] - first - 5000) < 400]
        self.assertTrue(late)
        self.assertTrue(all("door" not in c["targets"] for c in late))

    def test_neighbouring_phrases_never_share_a_look(self):
        planned = choreography.choreograph(SOURCE, song(), choreography.STYLES["show"])
        names = [s["look"] for s in planned["sections"]]
        self.assertTrue(all(a != b for a, b in pairwise(names)))

    def test_the_same_song_gives_the_same_bytes(self):
        style = choreography.STYLES["show"]
        once = encode_preview(choreography.choreograph(SOURCE, song(), style))
        again = encode_preview(choreography.choreograph(SOURCE, song(), style))
        self.assertEqual(once, again)


class Lab(unittest.TestCase):
    def test_a_song_with_no_audio_file_stays_a_silent_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(show_lab.link_audio(Path(tmp), Path(tmp) / "out", "x"))
            self.assertFalse((Path(tmp) / "out").exists())

    def test_the_lab_writes_candidates_and_a_page_and_leaves_the_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            library, output = Path(tmp) / "tracks", Path(tmp) / "out"
            (library / "stems" / "radio_test").mkdir(parents=True)
            baseline = dict(SOURCE, cues=[], base={}, levels={})
            (library / "radio_test.show.json").write_text(json.dumps(baseline))
            (library / "stems" / "radio_test" / "analysis.json").write_text(
                json.dumps({"layers": song()})
            )
            (library / "radio_test.mp3").write_bytes(b"not really a song")
            before = (library / "radio_test.show.json").read_bytes()
            report = show_lab.candidates(library, output)
            page = show_lab.page(library, output)
            self.assertEqual({r["style"] for r in report}, set(choreography.STYLES))
            self.assertEqual(sorted(p.name for p in library.iterdir()),
                             ["radio_test.mp3", "radio_test.show.json", "stems"])  # fmt: skip
            self.assertEqual((library / "radio_test.show.json").read_bytes(), before)
            html = page.read_text()
            self.assertNotIn("/*{{", html)
            # Sound is the listener's choice: off until ticked, never on load.
            self.assertIn('<input id="sound" type="checkbox">', html)
            self.assertNotIn("autoplay", html)
            self.assertIn('"audio":"audio/radio_test.mp3"', html)
            linked = output / "audio" / "radio_test.mp3"
            self.assertTrue(linked.is_symlink())
            self.assertEqual(linked.read_bytes(), b"not really a song")
            with self.assertRaises(ValueError):
                show_lab.candidates(library, library)


if __name__ == "__main__":
    unittest.main()
