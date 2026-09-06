"""castle-core's scene validator against tools/scene_schema.py, sentence
for sentence — and its YAML parser against PyYAML's on the way in.

Until the Python studio retired there was ONE implementation of these
rules: the Rust studio piped every splice through `tools/scene_check.py`
so the desk saw identical strings whichever server ran. There is one
server now, and the delegation went with the other one
(docs/RETIREMENT.md phase 2) — but not the second implementation. The
Python rules are still what `gen_esphome.py` runs before it emits, the
Rust ones are what the studio runs before it writes, and a drift between
them is the quiet kind: the desk accepts a block the generator will later
refuse, or refuses one it would have taken.

So both answer the same corpus here. The input is YAML TEXT rather than a
parsed value, because the parser is half of what has to agree: castle-core
carries its own subset parser (`core/src/yaml.rs`, zero dependencies by
crate policy) and the values it resolves are quoted back inside the
messages — `got 0` and `got '0'` are different sentences to an operator.

The corpus is three parts: every scene in the real show (which must be
clean on both sides, or the rules are the wrong rules), the golden
corpus's refusals, and a spread of malformed blocks covering what
tests/test_scene_schema.py holds the Python to.

Skipped, not failed, without cargo — except in CI.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import cargo_gate
import golden_corpus as corpus
import scene_schema as ss
import yaml

CARGO = cargo_gate.CARGO
IN_CI = bool(os.environ.get("CI"))
BIN = ROOT / "core" / "target" / "release" / "scene_dump"

ZONES = ["towerL", "towerR", "door"]

#: One case: a name, the block's YAML text, and the show's zone list (None
#: when the caller does not know it — zone names are then only checked for
#: shape, which is a different set of sentences).
Case = tuple[str, str, list[str] | None]


def base_scene(**over: Any) -> dict[str, Any]:
    """tests/test_scene_schema.scene(), so the two suites hold the same
    idea of a minimal scene."""
    s: dict[str, Any] = {
        "id": "probe",
        "name": "Probe",
        "kind": "triggered",
        "duration_ms": 5000,
        "base": {"towerL": "candle", "towerR": "candle", "door": "ember"},
    }
    s.update(over)
    return s


def dumped(case: str, zones: list[str] | None = None, /, **over: Any) -> Case:
    """A case written by PyYAML from a Python scene — which also means the
    Rust parser is read against PyYAML's own output shapes."""
    return (case, yaml.safe_dump(base_scene(**over), allow_unicode=True), zones)


def show_scenes() -> list[Case]:
    """Every scene in scenes/scenes.yaml, as the text of its own block."""
    text = (ROOT / "scenes" / "scenes.yaml").read_text()
    body = text.split("\nscenes:\n", 1)[1]
    blocks: list[str] = []
    for line in body.splitlines(keepends=True):
        if line.startswith("  - id: "):
            blocks.append(line)
        elif blocks:
            blocks[-1] += line
    zones = [z["id"] for z in yaml.safe_load(text)["zones"]]
    return [(f"show/{b.split()[2]}", b, zones) for b in blocks]


def golden_scenes() -> list[Case]:
    """The splice refusals the desk's UX contract freezes. Only the cases
    that carry a block: the rest test the route, not the rules."""
    out: list[Case] = []
    for name, body in (*corpus.SCENE_CASES, corpus.CEILING_CASE):
        try:
            req = json.loads(body)
        except ValueError:
            continue
        if isinstance(req, dict) and req.get("yaml"):
            out.append((f"golden/{name}", str(req["yaml"]), ZONES))
    return out


#: Hand-written text, for the shapes a round trip through PyYAML would
#: never produce: flow collections, block scalars, comments, the desk's own
#: emitted spelling, and blocks that do not parse at all.
TEXT_CASES: list[Case] = [
    ("text/desk_shaped", corpus.tiny("trial"), ZONES),
    ("text/no_zone_list", corpus.tiny("trial"), None),
    (
        "text/flow_and_folded",
        (
            "  - id: trial\n"
            "    name: Trial\n"
            "    kind: ambient\n"
            "    duration_ms: 4000\n"
            "    blurb: >\n"
            "      Two folded lines that mean\n"
            "      one sentence.\n"
            "    base: {towerL: candle, towerR: candle, door: 'off'}\n"
            "    zones:\n"
            "      towerL: {center: ember, palette: moonlight}\n"
            "      door: {overlay: sparkle, phase: 1.7}\n"
            "    pulse:\n"
            "      - {synth: onset_low, zones: [towerL, door], intensity: 0.8,\n"
            "         colors: [[1, 0, 0, 0], [0, 1, 0]], color_hot: [1, 1, 1, 1]}\n"
            '    cues:\n      - {t: 80, op: strike, ms: 70, note: "lightning — strike"}\n'
        ),
        ZONES,
    ),
    (
        "text/comments_everywhere",
        (
            "  - id: trial   # the id\n"
            "    # a whole-line comment\n"
            "    name: Trial\n"
            "    kind: ambient\n"
            "    duration_ms: 4000\n"
            "    base: {towerL: candle}   # trailing\n"
            "    cues: []\n"
        ),
        ZONES,
    ),
    ("text/unparseable_flow", "nonsense: [", ZONES),
    ("text/unterminated_quote", '  - id: x\n    name: "unclosed\n', ZONES),
    ("text/not_a_mapping", "- just\n- a\n- list\n", ZONES),
    ("text/empty", "", ZONES),
    ("text/scalar_only", "just text\n", ZONES),
    (
        "text/numbers_keep_their_kind",
        (
            "  - id: trial\n"
            "    name: Trial\n"
            "    kind: ambient\n"
            "    duration_ms: 1.5\n"
            "    volume: 9\n"
            "    base: {towerL: candle}\n"
            "    cues:\n      - {t: 99999, op: strike}\n"
        ),
        ZONES,
    ),
    (
        "text/booleans_and_null",
        (
            "  - id: trial\n"
            "    name: Trial\n"
            "    kind: ambient\n"
            "    duration_ms: 1000\n"
            "    loop: maybe\n"
            "    base: {towerL: candle}\n"
            "    levels:\n"
            "    zones:\n"
            "    cues:\n"
        ),
        ZONES,
    ),
]


def dumped_cases() -> list[Case]:
    """The shapes tests/test_scene_schema.py holds the Python to."""
    good_cue = {
        "t": 0,
        "op": "strike",
        "zone": "door",
        "targets": ["towerL"],
        "intensity": 1.2,
        "decay": 0.9,
        "ms": 80,
        "attack": 40,
        "color": [1, 0.2, 0, 0],
    }
    good_pulse = {
        "synth": "onset_low",
        "zones": ["door"],
        "intensity": 0.8,
        "decay": 0.9,
        "ms": 140,
        "attack_ms": 20,
        "colors": [[1, 0, 0, 0], [0, 1, 0]],
        "color_hot": [1, 1, 1, 1],
        "boost_targets": ["towerL"],
        "pixels": "scatter",
    }
    out = [
        dumped("minimal", ZONES),
        dumped("minimal_no_zones"),
        dumped("id_dashed", ZONES, id="a-b"),
        dumped("id_spaced", ZONES, id="a b"),
        dumped("id_traversal", ZONES, id="../x"),
        dumped("id_empty", ZONES, id=""),
        dumped("id_number", ZONES, id=7),
        dumped("name_blank", ZONES, name="   "),
        dumped("kind_number", ZONES, kind=3),
        dumped("duration_zero", ZONES, duration_ms=0),
        dumped("duration_negative", ZONES, duration_ms=-1),
        dumped("duration_fractional", ZONES, duration_ms=1.5),
        dumped("duration_string", ZONES, duration_ms="5000"),
        dumped("duration_nan", ZONES, duration_ms=float("nan")),
        dumped("duration_inf", ZONES, duration_ms=float("inf")),
        dumped("duration_true", ZONES, duration_ms=True),
        dumped("volume_high", ZONES, volume=1.5),
        dumped("volume_zero", ZONES, volume=0),
        dumped("loop_string", ZONES, loop="yes please"),
        dumped("loop_false", ZONES, loop=False),
        dumped("levels_high", ZONES, levels={"door": 2}),
        dumped("levels_not_a_map", ZONES, levels="loud"),
        dumped("audio_absolute", ZONES, audio_file="/etc/passwd"),
        dumped("audio_traversal", ZONES, audio_file="tracks/../secret.mp3"),
        dumped("audio_empty", ZONES, audio_file=""),
        dumped("audio_number", ZONES, audio_file=3),
        dumped("audio_relative", ZONES, audio_file="tracks/x.mp3"),
        dumped("base_not_a_map", ZONES, base="candle"),
        dumped("base_unknown_effect", ZONES, base={"door": "glow"}),
        dumped("base_unknown_zone", ZONES, base={"attic": "candle"}),
        dumped("base_unknown_zone_shapeless", base={"attic": "candle"}),
        dumped("base_effect_is_a_map", ZONES, base={"door": {"a": 1}}),
        dumped("zones_not_a_map", ZONES, zones="towerL"),
        dumped("zone_texture_not_a_map", ZONES, zones={"door": "sparkle"}),
        dumped("zone_overlay_unknown", ZONES, zones={"door": {"overlay": "glitter"}}),
        dumped("zone_palette_unknown", ZONES, zones={"door": {"palette": "neon"}}),
        dumped("zone_center_unknown", ZONES, zones={"towerL": {"center": "nope"}}),
        dumped("zone_phase_text", ZONES, zones={"door": {"phase": "late"}}),
        dumped(
            "zone_texture_good",
            ZONES,
            zones={"door": {"overlay": "chase", "palette": "toxic", "phase": 1}},
        ),
        dumped("cues_not_a_list", ZONES, cues="soon"),
        dumped("cues_a_mapping", ZONES, cues={"t": 0}),
        dumped("cue_not_a_mapping", ZONES, cues=["strike"]),
        dumped("cue_no_time", ZONES, cues=[{"op": "strike"}]),
        dumped("cue_negative_time", ZONES, cues=[{"t": -5, "op": "fade"}]),
        dumped("cue_at_the_end", ZONES, cues=[{"t": 5000, "op": "strike"}]),
        dumped("cue_past_the_end", ZONES, cues=[{"t": 9000, "op": "strike"}]),
        dumped("cue_set_bare", ZONES, cues=[{"t": 0, "op": "set"}]),
        dumped(
            "cue_set_bad_effect_and_level",
            ZONES,
            cues=[{"t": 0, "op": "set", "zone": "door", "effect": "glow", "level": 3}],
        ),
        dumped("cue_strike_good", ZONES, cues=[good_cue]),
        dumped("cue_decay_high", ZONES, cues=[{**good_cue, "decay": 1.5}]),
        dumped("cue_ms_negative", ZONES, cues=[{**good_cue, "ms": -1}]),
        dumped(
            "cue_color_out_of_range", ZONES, cues=[{**good_cue, "color": [2, 0, 0]}]
        ),
        dumped("cue_targets_not_a_list", ZONES, cues=[{**good_cue, "targets": "door"}]),
        dumped("cue_intensity_text", ZONES, cues=[{**good_cue, "intensity": "loud"}]),
        dumped("cue_pixels_unknown", ZONES, cues=[{**good_cue, "pixels": "random"}]),
        dumped("pulse_not_a_list", ZONES, pulse={"synth": "toll"}),
        dumped("pulse_no_synth", ZONES, pulse=[{"zones": ["door"]}]),
        dumped("pulse_good", ZONES, pulse=[good_pulse]),
        dumped("pulse_decay_high", ZONES, pulse=[{**good_pulse, "decay": 2}]),
        dumped("pulse_colors_empty", ZONES, pulse=[{**good_pulse, "colors": []}]),
        dumped(
            "pulse_colors_bad", ZONES, pulse=[{**good_pulse, "colors": [[1, 2, 3]]}]
        ),
        dumped(
            "pulse_boost_unknown_zone",
            ZONES,
            pulse=[{**good_pulse, "boost_targets": ["attic"]}],
        ),
        dumped("pulse_pixels_unknown", ZONES, pulse=[{**good_pulse, "pixels": "all"}]),
        dumped("pulse_zone_scalar", ZONES, pulse=[{**good_pulse, "zone": 4}]),
    ]
    # Every effect the firmware knows must be accepted by both.
    from effect_vocab import EFFECT_IDS

    out += [dumped(f"effect/{e}", ZONES, base={"door": e}) for e in EFFECT_IDS]
    return out


def cases() -> list[Case]:
    return show_scenes() + golden_scenes() + TEXT_CASES + dumped_cases()


def python_answers(corpus_cases: list[Case]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for _name, text, zones in corpus_cases:
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError:
            out.append({"parse_error": True})
            continue
        scene = doc[0] if isinstance(doc, list) and len(doc) == 1 else doc
        out.append({"errors": ss.validate(scene, zones)})
    return out


def rust_answers(corpus_cases: list[Case]) -> list[dict[str, Any]]:
    built = cargo_gate.build("--bin", "scene_dump")
    assert built.returncode == 0, built.stderr
    doc = {"cases": [{"yaml": t, "zones": z} for _n, t, z in corpus_cases]}
    r = subprocess.run(
        [str(BIN)],
        input=json.dumps(doc),
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    return list(json.loads(r.stdout))


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class TestSceneSchemaRustParity(unittest.TestCase):
    def test_the_corpus_is_refused_with_the_same_words(self) -> None:
        corpus_cases = cases()
        py = python_answers(corpus_cases)
        rs = rust_answers(corpus_cases)
        self.assertEqual(len(rs), len(corpus_cases))
        for (name, text, _z), a, b in zip(corpus_cases, py, rs, strict=True):
            with self.subTest(case=name):
                if "parse_error" in a:
                    self.assertIn("parse_error", b, f"{name}: {text!r} -> {b}")
                    continue
                self.assertNotIn("parse_error", b, f"{name}: {text!r} -> {b}")
                self.assertEqual(a["errors"], b["errors"], name)

    def test_the_whole_show_is_clean_on_both_sides(self) -> None:
        """The rule set must accept the show as it is, or it is the wrong
        rule set — and the Rust parser must read the file the show is
        actually written in."""
        show = show_scenes()
        self.assertGreaterEqual(len(show), 6, "the show lost its scenes")
        for (name, _t, _z), a, b in zip(
            show, python_answers(show), rust_answers(show), strict=True
        ):
            self.assertEqual(a.get("errors"), [], name)
            self.assertEqual(b.get("errors"), [], name)

    def test_the_corpus_exercises_both_verdicts_and_every_rule(self) -> None:
        """A gate that only ever saw clean scenes would pass while refusing
        nothing. This also names the rules: every sentence scene_schema can
        write should appear somewhere in the corpus."""
        py = python_answers(cases())
        clean = [a for a in py if a.get("errors") == []]
        refused = [a for a in py if a.get("errors")]
        self.assertGreaterEqual(len(clean), 20)
        self.assertGreaterEqual(len(refused), 40)
        said = " | ".join(e for a in py for e in a.get("errors", []))
        for phrase in (
            "missing required key",
            "letters, digits and _ only",
            "must be a non-empty string",
            "must be a number from 0 to 1",
            "must be a number from 0 to 4",
            "must be true or false",
            "must be a relative path",
            "must be a whole number of ms > 0",
            "must map each zone to an effect",
            "must map zones to a level",
            "must map zones to their texture",
            "zone must be a name",
            "no zone",
            "unknown effect",
            "unknown overlay",
            "unknown palette",
            "unknown pixels",
            "must be a mapping",
            "cues: must be a list",
            "pulse: must be a list",
            "t must be a time in ms >= 0",
            "is past the scene's duration_ms",
            "op must be one of set, strike",
            "a set cue needs a zone",
            "a set cue needs an effect",
            "must be a list of zones",
            "must be a number of ms >= 0",
            "must be [r, g, b(, w)] with each from 0 to 1",
            "must be a non-empty list of colours",
            "needs a synth (the marker stream it follows)",
            "must be a number",
            "scene must be a mapping",
        ):
            self.assertIn(phrase, said, f"no case produces {phrase!r}")

    def test_the_scene_limit_has_one_number_on_each_side(self) -> None:
        """tools/check_loc.py fails `make check` above the ceiling; the
        studio refuses the splice before the show is edited. Two constants,
        one number — the third copy (the Python studio's) went with the
        server."""
        from check_loc import SCENE_LIMIT

        rust = (ROOT / "core" / "src" / "vocab.rs").read_text()
        self.assertIn(f"pub const SCENE_LIMIT: usize = {SCENE_LIMIT};", rust)


if __name__ == "__main__":
    unittest.main()
