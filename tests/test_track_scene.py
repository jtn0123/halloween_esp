"""tools/track_scene.py on its own: the JavaScript arithmetic it imitates,
and what the sections do on envelopes built to need them. Text-for-text
parity with the desk's sceneYaml is web/test/scene_parity.ts."""

import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import render_cues
import track_scene as ts


def steps(*parts: tuple[float, float]) -> list[list[float]]:
    """An envelope holding each (seconds, level) in turn, a point per 0.1 s."""
    env: list[list[float]] = []
    t = 0.0
    for length, level in parts:
        end = t + length
        while t < end - 1e-9:
            env.append([round(t, 3), level])
            t += 0.1
    return env


class JavaScriptNumbers(unittest.TestCase):
    def test_round_sends_a_tie_up_not_to_even(self) -> None:
        self.assertEqual([ts.js_round(x) for x in (0.5, 1.5, 2.5, -0.5, -2.5)],
                         [1, 2, 3, 0, -2])  # fmt: skip
        self.assertEqual(ts.js_round(2.4999), 2)

    def test_string_drops_the_point_on_whole_numbers(self) -> None:
        self.assertEqual(ts.js_str(1.0), "1")
        self.assertEqual(ts.js_str(-0.0), "0")
        self.assertEqual(ts.js_str(0.4), "0.4")
        self.assertEqual(ts.num(0.94499999), "0.945")
        self.assertEqual(ts.num(0.0004), "0")


class EnvelopeLookup(unittest.TestCase):
    def test_nearest_point_within_half_a_second_earlier_on_a_tie(self) -> None:
        env = [[1.0, 0.2], [2.0, 0.8]]
        self.assertEqual(ts.env_at(env, 1.45), 0.2)
        self.assertEqual(ts.env_at(env, 1.55), 0.8)
        self.assertEqual(ts.env_at(env, 1.5), 0.0)  # half a second is too far
        self.assertEqual(ts.env_at([[1.0, 0.2], [1.5, 0.8]], 1.25), 0.2)
        self.assertEqual(ts.env_at(env, 0.3), 0.0)  # nothing near: silence
        self.assertEqual(ts.env_at(env, 9.0), 0.0)
        self.assertEqual(ts.env_at([], 1.0), 0.0)

    def test_before_the_first_point_never_wraps_to_the_last(self) -> None:
        self.assertEqual(ts.env_at([[0.2, 0.3], [5.0, 0.9]], 0.0), 0.3)


class Sections(unittest.TestCase):
    def test_no_envelope_or_a_flat_one_holds_the_verse(self) -> None:
        self.assertEqual(ts.sections([], 0, 10), [(0, 1)])
        self.assertEqual(ts.sections(steps((30, 0.5)), 0, 30), [(0, 1)])

    def test_a_loud_chorus_gets_its_dip_and_a_held_pause_goes_dark(self) -> None:
        env = steps((10, 0.3), (10, 0.95), (3, 0.0), (6, 0.3))
        tiers = [tier for _, tier in ts.sections(env, 0, 29)]
        self.assertEqual(tiers[tiers.index(2) :][:3], [2, 3, 0])
        lines = ts._set_lines(env, 29)
        notes = [line.rsplit("note: ", 1)[1].rstrip("}") for line in lines]
        chorus = notes.index("chorus")
        self.assertEqual(notes[chorus - 3 : chorus], ["predim"] * 3)
        # The verse it dips from, at 0.45: towers 0.7 -> 0.315, door 0.8 -> 0.36.
        self.assertIn("level: 0.315,", lines[chorus - 3])
        self.assertIn("level: 0.36,", lines[chorus - 1])
        self.assertIn("silence", notes[chorus:])

    def test_a_breath_is_not_a_silence(self) -> None:
        env = steps((10, 0.3), (10, 0.95), (1, 0.0), (8, 0.95))
        self.assertNotIn(3, [tier for _, tier in ts.sections(env, 0, 29)])


class TheBlock(unittest.TestCase):
    def test_title_cases_ascii_words_only(self) -> None:
        self.assertEqual(ts._title("the_ballad_of_x2"), "The Ballad Of X2")
        # A JS regex without /u calls ü a non-word: the n after it starts one.
        self.assertEqual(ts._title("radio_ünï"), "Radio üNï")

    def test_streams_only_for_bands_with_hits(self) -> None:
        text = ts.scene_yaml("song", 12.3456, {"onset_low": 4, "onset_mid": 0,
                                               "level_low": 2})  # fmt: skip
        self.assertIn("duration_ms: 12346", text)
        self.assertIn("synth: onset_low", text)
        self.assertNotIn("onset_mid", text)
        self.assertNotIn("level_low", text)
        self.assertTrue(text.endswith("    cues: []"))

    def test_scene_parses_into_what_render_cues_validates(self) -> None:
        wave = {
            "duration": 29,
            "env": steps((10, 0.3), (10, 0.95), (9, 0.3)),
            "onsets": {"onset_low": [[1.0, 0.5]], "onset_high": [[2.0, 0.9]]},
        }
        block = render_cues.desk_scene("radio_x", wave, "wav")
        self.assertEqual(block["audio_file"], "tracks/radio_x.wav")
        self.assertEqual([p["synth"] for p in block["pulse"]],
                         ["onset_low", "onset_high"])  # fmt: skip
        self.assertEqual(block["pulse"][0]["boost_targets"], ["towerL", "towerR"])
        self.assertEqual(block["levels"], {"towerL": 0.4, "towerR": 0.4, "door": 0.5})
        self.assertTrue(any(c["note"] == "chorus" for c in block["cues"]))

    def test_check_mode_answers_the_parity_suite(self) -> None:
        cases = {"cases": [{"id": "radio_ü", "dur": 3, "counts": {"onset_mid": 1}}]}
        raw = io.BytesIO(json.dumps(cases).encode("utf-8"))
        stdin = io.TextIOWrapper(raw, encoding="utf-8")
        out = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", stdin),
            mock.patch.object(sys, "stdout", out),
        ):
            self.assertEqual(ts.main(["--check"]), 0)
        (text,) = json.loads(out.getvalue())["yaml"]
        self.assertIn("name: Radio ü", text)
        with mock.patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(ts.main([]), 2)


if __name__ == "__main__":
    unittest.main()
