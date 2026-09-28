"""Option 9 and what it stands on — song structure and the porch's soften —
on a synthetic song (lab_song). No library track, audio or device."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import beat_grid
import porch
import sections_show
import show_lab
import structure
from lab_song import BEAT, song, source
from looks import WHITE
from pulse_clarity import encode_preview
from rich_show import preview_from_blob

STOP = (45000, 46000)  # inside the sixth phrase, which is groove A


def strikes(cues):
    return [c for c in cues if c["op"] == "strike"]


class Structure(unittest.TestCase):
    def setUp(self):
        self.layers, self.duration = song(stops=(STOP,))
        self.grid = beat_grid.analyse(self.layers, self.duration)

    def test_the_same_groove_gets_the_same_label_and_a_different_one_does_not(self):
        labels = structure.label_phrases(self.grid, self.layers, self.duration)
        a = {labels[i] for i in (0, 1, 4, 8, 9)}
        b = {labels[i] for i in (2, 3, 6, 7)}
        self.assertEqual(len(a), 1, labels)
        self.assertEqual(len(b), 1, labels)
        self.assertNotEqual(a, b)

    def test_neighbours_merge_into_sections_that_know_their_visits(self):
        labels = structure.label_phrases(self.grid, self.layers, self.duration)
        secs = structure.sections(self.grid, labels)
        self.assertEqual(secs[0].phrases, (0, 1))
        self.assertEqual(secs[1].phrases, (2, 3))
        firsts = [s for s in secs if s.label == secs[0].label]
        self.assertEqual([s.visit for s in firsts], list(range(len(firsts))))
        self.assertEqual([s.last_visit for s in firsts][-1], True)
        self.assertFalse(firsts[0].last_visit)

    def test_a_long_run_is_cut_into_sections(self):
        layers, duration = song(plan="AAAAAAAAAA", loud="")
        grid = beat_grid.analyse(layers, duration)
        labels = structure.label_phrases(grid, layers, duration)
        secs = structure.sections(grid, labels, max_phrases=4)
        self.assertTrue(all(len(s.phrases) <= 4 for s in secs))
        self.assertGreater(len(secs), 2)

    def test_the_band_stopping_dead_is_a_break(self):
        bands = structure.rhythm_bands(self.layers)
        found = structure.breaks(self.grid, bands, self.layers, self.duration)
        self.assertEqual(len(found), 1)
        start, end = found[0]
        self.assertLessEqual(abs(start - STOP[0]), BEAT)
        self.assertLessEqual(abs(end - STOP[1]), BEAT)

    def test_a_band_that_thins_out_then_stops_is_one_break_despite_bleed(self):
        # Day-o's call: the kit drops out over the bar before the stop, and a
        # singer's bleed leaves a few faint onsets inside it.
        layers, duration = song(stops=((45000, 47000),))
        onsets = layers["backing"]["both"]["onsets"]
        for hits in onsets.values():
            for hit in hits:
                if 43000 <= hit[0] * 1000 < 45000:
                    hit[1] *= 0.2
        onsets["onset_mid"] += [[45.5, 0.25], [45.75, 0.25], [46.25, 0.25]]
        onsets["onset_mid"].sort()
        grid = beat_grid.analyse(layers, duration)
        bands = structure.rhythm_bands(layers)
        found = structure.breaks(grid, bands, layers, duration)
        self.assertEqual(len(found), 1, found)
        start, end = found[0]
        self.assertLessEqual(abs(start - 45000), BEAT)
        self.assertLessEqual(abs(end - 47000), BEAT)

    def test_a_sparse_intro_and_a_steady_song_have_no_break(self):
        layers, duration = song()
        grid = beat_grid.analyse(layers, duration)
        bands = structure.rhythm_bands(layers)
        self.assertEqual(structure.breaks(grid, bands, layers, duration), [])
        intro, duration = song(stops=((0, 5000),))
        grid = beat_grid.analyse(intro, duration)
        bands = structure.rhythm_bands(intro)
        self.assertEqual(structure.breaks(grid, bands, intro, duration), [])

    def test_a_beat_hit_harder_is_stronger(self):
        bands = structure.rhythm_bands(self.layers)
        strength = structure.beat_strengths(self.grid, bands)
        one = self.grid.beats.index(1000 + 16 * BEAT)  # a 'one' in groove A
        three = one + 2  # the softer kick on three
        self.assertGreater(strength[one], strength[three])

    def test_a_near_silent_vocal_stem_is_no_singer(self):
        quiet, _ = song(voice_level=0.001)
        self.assertFalse(structure.has_singer(quiet))
        self.assertTrue(structure.has_singer(self.layers))

    def test_the_drum_stem_is_what_the_fingerprint_hears_when_there_is_one(self):
        drums = {"both": {"onsets": {"onset_low": [[1.0, 1.0]]}}}
        bands = structure.rhythm_bands({**self.layers, "drums": drums})
        self.assertEqual(bands["kick"], [[1.0, 1.0]])
        self.assertNotIn("onset_low", bands)


class Porch(unittest.TestCase):
    def cue(self, t, zones, intensity=0.8, decay=0.9):
        return {
            "t": t,
            "op": "strike",
            "targets": zones,
            "intensity": intensity,
            "decay": decay,
        }

    def test_a_compensated_hit_softened_renders_as_the_hit_unsoftened(self):
        hit = self.cue(0, ["towerL"])
        comp = porch.compensated(hit)
        self.assertAlmostEqual(comp["intensity"] * 0.55, hit["intensity"] * 0.92, 2)
        softened_decay = 1 - (1 - comp["decay"]) * porch.SOFT_SLOW
        self.assertAlmostEqual(softened_decay, hit["decay"], 3)

    def test_a_strobe_is_left_for_soften_and_a_beat_is_not(self):
        cues = [
            self.cue(0, ["towerL"]),
            self.cue(200, ["towerL"]),
            self.cue(700, ["towerL"]),
        ]
        out = porch.for_todays_castle(cues)
        self.assertEqual([c["intensity"] > 0.8 for c in out], [True, False, True])

    def test_other_zones_do_not_make_a_beat_a_strobe(self):
        cues = [
            self.cue(0, ["towerL"]),
            self.cue(150, ["towerR"]),
            self.cue(300, ["door"]),
        ]
        self.assertTrue(
            all(c["intensity"] > 0.8 for c in porch.for_todays_castle(cues))
        )

    def test_a_hit_that_strobes_on_one_zone_only_is_split(self):
        cues = [self.cue(0, ["towerL"]), self.cue(100, ["towerL", "towerR"])]
        out = porch.for_todays_castle(cues)
        late = [c for c in out if c["t"] == 100]
        self.assertEqual(sorted(c["targets"][0] for c in late), ["towerL", "towerR"])
        by_zone = {c["targets"][0]: c["intensity"] for c in late}
        self.assertGreater(by_zone["towerR"], by_zone["towerL"])

    def test_lead_moves_every_cue_earlier_but_not_before_the_start(self):
        out = porch.lead([self.cue(30, ["door"]), self.cue(500, ["door"])], 50)
        self.assertEqual([c["t"] for c in out], [0, 450])
        self.assertEqual(porch.lead([self.cue(30, ["door"])], 0)[0]["t"], 30)


class SectionsShow(unittest.TestCase):
    def setUp(self):
        self.layers, self.duration = song(stops=(STOP,))
        self.show = sections_show.choreograph_sections(
            source(self.duration), self.layers
        )

    def test_a_kind_of_passage_wears_one_look_every_time_it_comes_back(self):
        looks: dict[str, set[str]] = {}
        for s in self.show["sections"]:
            looks.setdefault(s["label"], set()).add(s["look"])
        self.assertTrue(all(len(v) == 1 for v in looks.values()), looks)
        self.assertNotEqual(looks["A"], looks["B"])

    def test_into_a_louder_section_a_swell_then_one_white_slam_and_no_white_roll(self):
        drop = next(s for s in self.show["sections"] if s["drop"])
        hits = strikes(self.show["cues"])
        slam = [c for c in hits if c["t"] == drop["end"] and c["color"] == WHITE]
        self.assertEqual(len(slam), 1)
        self.assertEqual(len(slam[0]["targets"]), 3)
        before = [c for c in hits if drop["end"] - 2 * BEAT <= c["t"] < drop["end"]]
        self.assertTrue(any(c["attack"] > 0 for c in before))
        self.assertFalse(any(c["color"] == WHITE for c in before))

    def test_where_the_band_stops_the_castle_goes_dark_and_slams_back(self):
        (start, end), = self.show["breaks"]  # fmt: skip
        cues = self.show["cues"]
        dark = [c for c in cues if c["op"] == "set" and c["t"] == start]
        self.assertEqual(sorted(c["level"] for c in dark), [0.0, 0.0, 0.0])
        band = [
            c
            for c in strikes(cues)
            if start <= c["t"] < end and c["targets"] != ["door"]
        ]
        self.assertEqual(band, [])
        sung = [c for c in strikes(cues) if start <= c["t"] < end]
        self.assertTrue(sung, "the singer is what stays lit when the band stops")
        self.assertTrue(
            any(len(c["targets"]) == 3 for c in strikes(cues) if c["t"] == end)
        )

    def test_a_stop_before_a_louder_section_is_kept_and_no_drop_is_built(self):
        # Day-o's call: the silence ends a phrase, so the next looks louder;
        # there is no band to build, and the band's return is the entrance.
        layers, duration = song(stops=((14000, 16500),))
        show = sections_show.choreograph_sections(source(duration), layers)
        self.assertEqual(len(show["breaks"]), 1, show["breaks"])
        before = next(s for s in show["sections"] if s["end"] == 1000 + 32 * BEAT)
        self.assertFalse(before["drop"])
        end = show["breaks"][0][1]
        self.assertTrue(
            any(len(c["targets"]) == 3 for c in strikes(show["cues"]) if c["t"] == end)
        )

    def test_the_towers_follow_the_band_between_looks(self):
        sets = [
            c for c in self.show["cues"] if c["op"] == "set" and c["zone"] == "towerL"
        ]
        self.assertGreater(len({c["level"] for c in sets}), 4)

    def test_a_held_note_glows_and_a_quick_one_flashes(self):
        held = sections_show.sung_cue(1000, 0.8, 900, [1, 0, 0, 0])
        quick = sections_show.sung_cue(1000, 0.8, 200, [1, 0, 0, 0])
        self.assertGreater(held["attack"], 0)
        self.assertEqual(quick["attack"], 0)
        self.assertGreater(held["decay"], quick["decay"])

    def test_bleed_is_not_sung(self):
        quiet, duration = song(voice_level=0.001)
        self.assertEqual(sections_show.voice_notes(quiet, duration), [])
        self.assertTrue(sections_show.voice_notes(self.layers, self.duration))

    def test_a_softer_beat_is_a_softer_hit(self):
        grid = beat_grid.analyse(self.layers, self.duration)
        hit = {"op": "strike", "t": grid.beats[4], "intensity": 1.0}
        strong, weak = sections_show.with_velocity(
            [hit, {**hit, "t": grid.beats[5]}], grid, [1.0] * 4 + [1.0, 0.0]
        )
        self.assertGreater(strong["intensity"], weak["intensity"])

    def test_beat_hits_are_written_for_soften_and_survive_the_card(self):
        self.assertTrue(any(c["intensity"] > 1.0 for c in strikes(self.show["cues"])))
        decoded = preview_from_blob("radio_test", encode_preview(self.show))
        self.assertEqual(len(decoded["cues"]), len(self.show["cues"]))
        self.assertTrue(all(0 <= c["t"] < self.duration for c in decoded["cues"]))

    def test_the_same_song_gives_the_same_bytes(self):
        again = sections_show.choreograph_sections(source(self.duration), self.layers)
        self.assertEqual(encode_preview(again), encode_preview(self.show))


class Lab(unittest.TestCase):
    def test_a_song_never_prepared_gets_a_baseline_in_the_lab_not_the_library(self):
        layers, duration = song()
        with tempfile.TemporaryDirectory() as tmp:
            library, output = Path(tmp) / "tracks", Path(tmp) / "out"
            (library / "stems" / "radio_x").mkdir(parents=True)
            (library / "stems" / "radio_x" / "analysis.json").write_text(
                json.dumps({"layers": layers})
            )
            (library / "radio_x.mp3").write_bytes(b"not really a song")
            output.mkdir()
            prepared = dict(source(duration), cues=[], base={}, levels={})
            with (
                mock.patch.object(show_lab, "waveform", return_value={}) as wave,
                mock.patch.object(show_lab, "build", return_value=(b"", prepared)),
            ):
                first = show_lab.baseline_for(library, output, "radio_x")
                again = show_lab.baseline_for(library, output, "radio_x")
            self.assertEqual(first, output / "radio_x.baseline.show.json")
            self.assertEqual(again, first)
            self.assertEqual(wave.call_count, 1)  # prepared once, then reused
            self.assertEqual(
                sorted(p.name for p in library.iterdir()), ["radio_x.mp3", "stems"]
            )
            self.assertIsNone(show_lab.baseline_for(library, output, "radio_y"))

    def test_the_lab_prefers_its_own_four_stem_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            library, output = Path(tmp) / "tracks", Path(tmp) / "out"
            for root in (library, output):
                (root / "stems" / "k").mkdir(parents=True)
                (root / "stems" / "k" / "analysis.json").write_text("{}")
            self.assertEqual(
                show_lab.analysis_for(library, output, "k"),
                output / "stems" / "k" / "analysis.json",
            )
            (output / "stems" / "k" / "analysis.json").unlink()
            self.assertEqual(
                show_lab.analysis_for(library, output, "k"),
                library / "stems" / "k" / "analysis.json",
            )

    def test_blind_picks_are_summarised_best_first(self):
        picks = [
            {"a": "sections", "b": "current", "verdict": "a"},
            {"a": "show", "b": "sections", "verdict": "b"},
            {"a": "show", "b": "current", "verdict": "same"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "picks.json"
            path.write_text(json.dumps(picks))
            lines = show_lab.verdicts(path)
        self.assertIn("sections", lines[0])
        self.assertIn("picked   2", lines[0])
        self.assertEqual(len(lines), 3)


if __name__ == "__main__":
    unittest.main()
