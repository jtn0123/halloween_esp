"""The beat grid's drum-stem path, on synthetic four-stem splits.

The weak spot this guards: from the backing alone, "one" is wherever the low
band hits hardest — and a bass line pushing beat 2 puts the whole castle's
downbeat on 2. A four-stem split has the kit, and the kit's backbeat says
which beats are 2 and 4. No library track, audio or device is touched.
"""

import unittest

import beat_grid

BPM = 120
BEAT = 60000 // BPM
BAR = 4 * BEAT
DURATION = 48_000
FIRST = 1000  # ms: beat one of the first bar


def times():
    return list(enumerate(range(FIRST, DURATION - 500, BEAT)))


def band(onsets, level=1.0, peaks=None):
    return {"onsets": onsets, "peaks": peaks or [0.5] * 480, "level": level}


def split(kick=(1.0, 0, 1.0, 0), snare=(0, 1.0, 0, 1.0), bass=None, low=None):
    """A four-stem split at 120 BPM. `kick` / `snare` / `bass` / `low` are the
    strengths on beats 1..4 of every bar; `low` is the BACKING's low band —
    by default loudest on beat 2, the trap the backing-only method falls in."""
    low = low or (0.5, 1.0, 0.5, 0.4)
    drums: dict[str, list[list[float]]] = {
        "onset_low": [], "onset_mid": [], "onset_high": []
    }  # fmt: skip
    back: dict[str, list[list[float]]] = {
        "onset_low": [], "onset_mid": [], "onset_high": []
    }  # fmt: skip
    lows = []
    for i, at in times():
        pos = i % 4
        s = at / 1000
        if kick[pos]:
            drums["onset_low"].append([s, kick[pos]])
        if snare[pos]:
            drums["onset_mid"].append([s, snare[pos]])
        # Hats on every eighth: dense, and not allowed to double the tempo.
        drums["onset_high"] += [[s, 0.6], [(at + BEAT // 2) / 1000, 0.6]]
        back["onset_low"].append([s, low[pos]])
        back["onset_high"].append([(at + BEAT // 2) / 1000, 0.3])
        if bass and bass[pos]:
            lows.append([s, bass[pos]])
    layers = {
        "backing": {"both": band(back)},
        "vocals": {"both": band({}, peaks=[0.0] * 480)},
        "drums": {"both": band(drums)},
    }
    if bass:
        layers["bass"] = {"both": band({"onset_low": lows})}
    return layers


def one(grid):
    """Where the grid's 'one' sits in the bar, in beats (0 = right)."""
    return round((grid.beats[grid.downbeat] - FIRST) / BEAT) % 4


class DrumGrid(unittest.TestCase):
    def test_the_backbeat_beats_a_misleading_low_band(self):
        layers = split(bass=(1.0, 0, 0.4, 0))
        without = {k: layers[k] for k in ("backing", "vocals")}
        old = beat_grid.analyse(without, DURATION)
        self.assertEqual(one(old), 1, "the fixture must fool the backing method")
        new = beat_grid.analyse(layers, DURATION)
        self.assertEqual(new.source, "drums")
        self.assertEqual(one(new), 0, (new.downbeat_cue, new.downbeat_margin))
        self.assertAlmostEqual(new.bpm, BPM, delta=3)
        self.assertGreater(new.downbeat_margin, 0)

    def test_a_stronger_kick_on_one_tells_it_from_three(self):
        grid = beat_grid.analyse(split(kick=(1.0, 0, 0.5, 0)), DURATION)
        self.assertEqual(one(grid), 0)
        self.assertEqual(grid.downbeat_cue, "kick")

    def test_an_even_kick_leaves_one_against_three_to_the_bass(self):
        grid = beat_grid.analyse(split(bass=(1.0, 0, 0.3, 0)), DURATION)
        self.assertEqual(one(grid), 0)
        self.assertEqual(grid.downbeat_cue, "bass")

    def test_chords_changing_on_one_tell_it_from_three(self):
        layers = split()
        stabs = [[at / 1000, 1.0 if i % 4 == 0 else 0.3] for i, at in times()]
        layers["other"] = {"both": band({"onset_mid": stabs})}
        grid = beat_grid.analyse(layers, DURATION)
        self.assertEqual(one(grid), 0)
        self.assertEqual(grid.downbeat_cue, "harmony")

    def test_the_band_growing_on_one_names_the_bar(self):
        """No kick or bass preference at all: the phrase where the band
        doubles its onsets starts on the bar's one, not on its three."""
        layers = split()
        back = layers["backing"]["both"]["onsets"]
        grow = FIRST + 8 * BAR
        back["onset_mid"] = [
            [(at + off) / 1000, 1.0]
            for at in range(grow, DURATION - 500, BEAT)
            for off in (0, BEAT // 4, BEAT // 2)
        ]
        grid = beat_grid.analyse(layers, DURATION)
        self.assertEqual(one(grid), 0)
        self.assertEqual(grid.downbeat_cue, "sections")

    def test_beats_land_on_the_kit_before_the_rest_of_the_band(self):
        kit = [[t / 1000, 1.0] for t in (1000, 1500)]
        band_hits = [[t / 1000, 1.0] for t in (1005, 1490, 2010)]
        snapped = beat_grid.snap_to([1010, 1510, 2000, 2600], kit, band_hits)
        # Kit first even when the band is nearer; the band when the kit
        # rests; the arithmetic when nobody played.
        self.assertEqual(snapped, [1000, 1500, 2010, 2600])


class Fallback(unittest.TestCase):
    def test_a_two_stem_split_keeps_the_low_band_method(self):
        layers = split()
        del layers["drums"]
        grid = beat_grid.analyse(layers, DURATION)
        self.assertEqual(grid.source, "backing")
        self.assertEqual(grid.downbeat_cue, "low band")
        self.assertEqual(one(grid), 1)

    def test_a_drum_stem_of_bleed_is_not_a_kit(self):
        layers = split()
        drums = layers["drums"]["both"]["onsets"]
        drums["onset_low"] = drums["onset_low"][:5]
        drums["onset_mid"] = drums["onset_mid"][:5]
        self.assertIsNone(beat_grid.drum_kit(layers, DURATION))
        self.assertEqual(beat_grid.analyse(layers, DURATION).source, "backing")

    def test_a_near_silent_drum_stem_is_not_a_kit(self):
        layers = split()
        layers["drums"]["both"]["level"] = 0.02
        self.assertIsNone(beat_grid.drum_kit(layers, DURATION))

    def test_a_grid_still_builds_from_its_first_four_fields(self):
        grid = beat_grid.Grid(120.0, (0, 500), 0, ())
        self.assertEqual(
            (grid.source, grid.downbeat_cue, grid.downbeat_margin),
            ("backing", "low band", 0.0),
        )

    def test_a_song_too_short_for_sections_has_none(self):
        layers = split()
        found = beat_grid.section_changes(
            [1000, 1500], layers["backing"]["both"], {}, 3000
        )
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
