"""Option 11 — Spin — on the synthetic song, with a synthetic melody. No
library track, audio or device."""

import unittest
from itertools import pairwise

import sections_show
import spin_show
from lab_song import song, source
from looks import WHITE
from voice_pitch import Pitch

STOP = (45000, 46000)
SUNG = range(8130, 200_000, 730)  # lab_song's sung onsets


def melody(duration, step):
    """A pitch track that moves `step` semitones at every sung onset."""
    notes = []
    for frame in range(duration // 10):
        sung = sum(1 for at in SUNG if at <= frame * 10)
        notes.append(60.0 + step * sung if sung else None)
    return Pitch(10, tuple(notes))


def strikes(cues):
    return [c for c in cues if c["op"] == "strike"]


class Spin(unittest.TestCase):
    def setUp(self):
        self.layers, self.duration = song(stops=(STOP,))
        self.source = source(self.duration)
        self.show = spin_show.choreograph_spin(self.source, self.layers)
        self.d = sections_show.draft(self.source, self.layers, handover=spin_show.race)

    def voice(self, show):
        return [c for c in strikes(show["cues"]) if c.get("layer") == 1]

    def walk(self, pitch):
        show = spin_show.choreograph_spin(self.source, self.layers, pitch)
        sung = {at for at, _s, _r in self.d.notes}  # not the drop's burst
        voice = [c for c in self.voice(show) if c["t"] in sung]
        return [(c["t"], int(c["pixels"][3:])) for c in voice]

    def steps(self, walk):
        """Each move between notes of one sung line (a gap starts a new one
        at the top)."""
        self.assertGreater(len(walk), 10)
        moves = set()
        for (a_t, a), (b_t, b) in pairwise(walk):
            if b_t - a_t >= spin_show.PHRASE_GAP:
                self.assertEqual(b, 0)
            else:
                moves.add((b - a) % 8)
        return moves

    def test_the_heads_replay_the_firmware_overlay_clock(self):
        heads = spin_show.Heads(
            [{"t": 1000, "rate": 0.5, "head": 0.0}, {"t": 3000, "rate": 1.0}]
        )
        self.assertAlmostEqual(heads.at(2000), 0.5)
        self.assertAlmostEqual(heads.at(3000), 0.0)  # continued, not reset
        self.assertAlmostEqual(heads.at(3250), 0.25)

    def test_a_rising_tune_walks_clockwise_and_a_falling_one_back(self):
        up = self.steps(self.walk(melody(self.duration, 2.0)))
        down = self.steps(self.walk(melody(self.duration, -2.0)))
        self.assertIn(1, up)  # a whole tone a note: a step on
        self.assertLessEqual(up, {1, 2, 3})  # a skipped note is a bigger leap
        self.assertIn(7, down)
        self.assertLessEqual(down, {5, 6, 7})

    def test_a_repeated_note_or_no_pitch_still_moves_on(self):
        self.assertEqual(self.steps(self.walk(melody(self.duration, 0.0))), {1})
        self.assertEqual(self.steps(self.walk(None)), {1})  # no vocal stem

    def test_a_held_note_spins_the_door_from_where_its_arc_is(self):
        cues = self.show["cues"]
        held = [c for c in self.voice(self.show) if c["attack"]]
        self.assertTrue(held)
        for note in held[:5]:
            spin = [c for c in cues if c["op"] == "look" and c["t"] == note["t"]
                    and c["targets"] == ["door"]]  # fmt: skip
            if not spin:
                continue  # inside a build, which owns the door's spin
            self.assertEqual(spin[0]["overlay"], "chase")
            self.assertAlmostEqual(spin[0]["head"], int(note["pixels"][3:]) / 8)

    def test_the_one_is_unison_and_the_other_hits_turn_with_the_chase(self):
        ones = set(spin_show.downbeats(self.d.grid))
        heads = spin_show.Heads(
            [c for c in self.show["cues"]
             if c["op"] == "look" and "towerL" in c["targets"]]
        )  # fmt: skip
        towers = [
            c for c in strikes(self.show["cues"])
            if c["targets"][0].startswith("tower") and c.get("layer", 0) == 0
        ]  # fmt: skip
        on_one = [c for c in towers if any(abs(c["t"] - b) <= 40 for b in ones)]
        self.assertTrue(on_one)
        self.assertTrue(all(c["pixels"] == "all" or c["attack"] or c["color"] == WHITE
                            or c["pixels"] != "all" for c in on_one))  # fmt: skip
        self.assertTrue(any(c["pixels"] == "all" for c in on_one))
        turned = [c for c in towers if c["pixels"].startswith("arc")
                  and c["t"] not in {r["t"] for r in self.races()}]  # fmt: skip
        self.assertTrue(turned)
        for c in turned:
            self.assertEqual(int(c["pixels"][3:]), round(heads.at(c["t"]) * 8) % 8)

    def races(self):
        out = []
        for plan in self.d.plans:
            out += spin_show.race(plan, plan.look)[0]
        return out

    def test_a_section_change_races_across_the_castle_and_back(self):
        plan = self.d.plans[0]
        there, _cut = spin_show.race(plan, plan.look)
        path = [(c["targets"][0], c["pixels"]) for c in there]
        self.assertEqual(path, [("towerL", "arc2"), ("door", "arc6"),
                                ("door", "arc2"), ("towerR", "arc6")])  # fmt: skip
        later = next(p for p in self.d.plans if p.section.visit % 2)
        back, _cut = spin_show.race(later, later.look)
        self.assertEqual(
            [c["targets"][0] for c in back], ["towerR", "door", "door", "towerL"]
        )
        self.assertEqual(sorted(c["t"] for c in there), [c["t"] for c in there])

    def test_a_build_spins_faster_and_the_slam_bursts_round_the_door(self):
        ups, builds = spin_show.spin_ups(self.d, "none")
        self.assertTrue(builds)
        start, end = builds[0]
        rates = [c["rate"] for c in ups if c["op"] == "look" and c.get("rate")
                 and start <= c["t"] < end]  # fmt: skip
        self.assertEqual(rates, sorted(rates))
        self.assertGreater(rates[-1], 3 * rates[0])
        burst = [c for c in ups if c["op"] == "strike" and c["t"] > end]
        layers: dict[int, list[int]] = {0: [], 1: []}
        for c in sorted(burst, key=lambda c: c["t"]):
            layers[c["layer"]].append(int(c["pixels"][3:]))
        self.assertEqual(layers[0][:4], [1, 2, 3, 4])
        self.assertEqual(layers[1][:4], [7, 6, 5, 4])

    def test_a_held_note_at_the_very_end_hands_back_inside_the_song(self):
        held = [c for c in self.voice(self.show) if c["attack"]]
        end = held[-1]["t"] + 120  # the note's glow runs past the song
        show = spin_show.choreograph_spin(source(end), self.layers)
        self.assertTrue(show["cues"])
        self.assertLessEqual(max(c["t"] for c in show["cues"]), end - spin_show.END)

    def test_it_is_written_for_the_new_firmware(self):
        self.assertEqual(self.show["firmware"], "v5.71")
        self.assertTrue(any(c.get("pixels", "").startswith("arc")
                            for c in strikes(self.show["cues"])))  # fmt: skip


if __name__ == "__main__":
    unittest.main()
