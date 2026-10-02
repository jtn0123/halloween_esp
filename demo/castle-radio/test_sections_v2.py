"""Option 10 — option 9's plan written for cue format v2 (firmware v5.71) —
on the synthetic song. No library track, audio or device."""

import unittest

import sections_show
import sections_v2
from lab_song import BEAT, BPM, song, source
from looks import BY_NAME, HOT
from pulse_clarity import encode_preview
from rich_show import cue_file, preview_from_blob

STOP = (45000, 46000)


def strikes(cues):
    return [c for c in cues if c["op"] == "strike"]


class SectionsV2(unittest.TestCase):
    def setUp(self):
        self.layers, self.duration = song(stops=(STOP,))
        self.show = sections_v2.choreograph_sections_v2(
            source(self.duration), self.layers
        )
        self.looks = [c for c in self.show["cues"] if c["op"] == "look"]

    def test_it_is_a_version_2_file_that_todays_castle_refuses(self):
        blob = encode_preview(self.show)
        self.assertEqual(cue_file.decode(blob)["version"], 2)
        with self.assertRaises(ValueError):
            cue_file.decode(blob, versions=(1,))
        v1 = encode_preview(
            sections_show.choreograph_sections(source(self.duration), self.layers)
        )
        self.assertEqual(cue_file.decode(v1, versions=(1,))["version"], 1)

    def test_every_sung_note_is_on_the_ornament_layer_and_none_is_refused(self):
        d = sections_show.draft(source(self.duration), self.layers, halves=True)
        sung = [c for c in self.show["cues"] if c.get("layer") == 1]
        self.assertTrue(sung)
        self.assertTrue(all(c["targets"] == ["door"] for c in sung))
        wanted = [
            at for at, _s, _r in d.notes
            if at < self.duration - 50 and not any(a <= at < b for a, b in d.drops)
        ]  # fmt: skip
        self.assertEqual(sorted(c["t"] for c in sung), sorted(wanted))

    def test_each_section_turns_its_chase_once_a_bar_from_the_downbeat(self):
        starts = {s["t"] for s in self.show["sections"]}
        bar = BPM / 240
        for cue in self.looks:
            if cue.get("overlay") in (None, "none"):
                continue
            self.assertEqual(cue["targets"], ["towerL", "towerR"])
            self.assertEqual(cue["head"], 0.0)
            self.assertIn(cue["t"], starts | {STOP[1] - BEAT, STOP[1], STOP[1] + BEAT})
            look = next(
                s["look"]
                for s in self.show["sections"]
                if s["t"] <= cue["t"] < s["end"]
            )
            if look in HOT:
                self.assertEqual(cue["overlay"], "chase")
                self.assertIn(cue["rate"], (round(bar, 3), round(2 * bar, 3)))
            else:
                self.assertEqual(cue["overlay"], "meteor")
                self.assertEqual(cue["rate"], round(bar / 2, 3))
            self.assertIn(cue["palette"], set(sections_v2.PALETTE.values()))
        self.assertTrue(set(sections_v2.PALETTE) <= set(BY_NAME))

    def test_a_stop_stills_the_overlay_and_the_return_restarts_it(self):
        (start, end), = self.show["breaks"]  # fmt: skip
        still = [c for c in self.looks if c.get("overlay") == "none"]
        self.assertEqual([c["t"] for c in still], [start])
        self.assertTrue(any(c["t"] == end and c.get("rate") for c in self.looks))
        self.assertFalse(any(start < c["t"] < end for c in self.looks))

    def test_hand_overs_run_across_the_door_halves_with_the_towers(self):
        halves = [c for c in strikes(self.show["cues"]) if c["pixels"] != "all"]
        sides = {c["pixels"] for c in halves}
        self.assertTrue({"left", "right"} <= sides, sides)
        towers = {
            c["t"] for c in strikes(self.show["cues"]) if c["targets"] == ["towerL"]
        }
        lefts = [c["t"] for c in halves if c["pixels"] == "left"]
        self.assertTrue(all(t in towers for t in lefts))

    def test_no_soften_compensation_is_baked_in(self):
        self.assertTrue(all(c["intensity"] <= 1.0 for c in strikes(self.show["cues"])))
        v1 = sections_show.choreograph_sections(source(self.duration), self.layers)
        self.assertTrue(any(c["intensity"] > 1.0 for c in strikes(v1["cues"])))

    def test_the_card_round_trip_keeps_layers_looks_and_halves(self):
        blob = encode_preview(self.show)
        back = preview_from_blob("radio_test", blob)
        self.assertEqual(
            sum(1 for c in back["cues"] if c.get("layer") == 1),
            sum(1 for c in self.show["cues"] if c.get("layer") == 1),
        )
        self.assertEqual(
            sum(1 for c in back["cues"] if c["op"] == "look"), len(self.looks)
        )
        self.assertEqual(self.show["firmware"], "v5.71")


if __name__ == "__main__":
    unittest.main()
