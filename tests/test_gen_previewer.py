"""Tests for the previewer generator.

Turns scenes.yaml into the JSON the browser page runs on, and splices it —
along with the bundle and stylesheet — into a single self-contained file.

The verbatim YAML slice had a real bug once: the regex used `.+` and so
stopped at the first blank line inside a scene block.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gen_previewer as gp
import yaml

ZONES = [{"id": "towerL"}, {"id": "towerR"}, {"id": "door"}]
ZIDS = [z["id"] for z in ZONES]


def scene(**over: object) -> dict[str, Any]:
    """A minimal scene that both generators accept, for overriding per test."""
    s = {
        "id": "probe",
        "name": "Probe",
        "kind": "triggered",
        "duration_ms": 5000,
        "base": {"towerL": "candle", "towerR": "candle", "door": "ember"},
    }
    s.update(over)
    return s


def parse_script(lines: list[str]) -> dict[str, Any]:
    """emit_scene's lines are a YAML fragment; load them the way ESPHome would."""
    return dict(yaml.safe_load("script:\n" + "\n".join(lines))["script"][0])


def cue_lambdas(lines: list[str]) -> list[str]:
    """The lambdas a scene runs after its audio starts — i.e. the cues."""
    then = parse_script(lines)["then"]
    start = next(
        i
        for i, st in enumerate(then)
        if "media_player.speaker.play_on_device_media_file" in st
    )
    return [st["lambda"] for st in then[start + 1 :] if "lambda" in st]


class TestSceneYamlSlice(unittest.TestCase):
    """The source panel shows the author their own YAML, verbatim."""

    TEXT = (
        "scenes:\n"
        "  - id: alpha\n"
        "    name: Alpha\n"
        "\n"
        "    # a comment after a blank line\n"
        "    cues: []\n"
        "  - id: beta\n"
        "    name: Beta\n"
    )

    def test_blank_lines_inside_a_block_do_not_end_it(self) -> None:
        """This was a real bug: `.+` in the regex stopped at the first blank line.

        Authors separate a scene's score from its cues with a blank line, so
        the truncated slice looked plausible while hiding most of the scene.
        """
        got = gp.scene_yaml_slice(self.TEXT, "alpha")
        self.assertIn("# a comment after a blank line", got)
        self.assertIn("cues: []", got)
        self.assertNotIn("beta", got)

    def test_slice_starts_at_its_own_id(self) -> None:
        self.assertTrue(
            gp.scene_yaml_slice(self.TEXT, "beta").startswith("  - id: beta")
        )

    def test_trailing_whitespace_is_trimmed(self) -> None:
        self.assertEqual(
            gp.scene_yaml_slice(self.TEXT, "beta"), "  - id: beta\n    name: Beta"
        )

    def test_unknown_id_returns_a_visible_placeholder(self) -> None:
        """A missing slice must read as missing, not as an empty scene."""
        self.assertIn("not found", gp.scene_yaml_slice(self.TEXT, "ghost"))

    def test_final_scene_needs_a_trailing_newline(self) -> None:
        """KNOWN BUG, documented: no trailing newline loses the last scene.

        `(?:.*\\n)*?` can only consume whole lines, so when the file's last
        line has no newline the lookahead never reaches \\Z and the match
        fails. The real scenes.yaml ends with a newline, so this is latent —
        but an editor that strips the final newline would silently blank the
        last scene's source panel with no error anywhere.
        """
        self.assertIn("not found", gp.scene_yaml_slice(self.TEXT.rstrip("\n"), "beta"))


class TestToPreviewer(unittest.TestCase):
    """The previewer's scene objects are the browser's whole view of the show."""

    def test_documented_shape(self) -> None:
        s = scene(
            volume=0.6, loop=True, blurb="two\n  lines   here", levels={"door": 0.5}
        )
        got = gp.to_previewer(s, 7, "", {})
        self.assertEqual(got["id"], "probe")
        self.assertEqual(got["dur"], 5000)
        self.assertEqual(got["volume"], 0.6)
        self.assertTrue(got["loop"])
        self.assertEqual(got["kind"], "triggered · loops")
        self.assertEqual(got["blurb"], "two lines here")
        self.assertEqual(got["levels"], {"door": 0.5})
        self.assertEqual(got["file"], "07_probe.mp3")

    def test_defaults_for_an_untagged_scene(self) -> None:
        got = gp.to_previewer(scene(), 1, "", {})
        self.assertEqual(got["volume"], 0.8)
        self.assertFalse(got["loop"])
        self.assertEqual(got["kind"], "triggered")
        self.assertEqual(got["blurb"], "")
        self.assertEqual(got["levels"], {})

    def test_score_becomes_audio_bus_cues_in_milliseconds(self) -> None:
        """The score is written in seconds; the transport counts in ms."""
        s = scene(score=[{"t": 1.5, "synth": "toll"}])
        cue = gp.to_previewer(s, 1, "", {})["cues"][0]
        self.assertEqual(
            (cue["t"], cue["bus"], cue["op"], cue["snd"]), (1500, "AUD", "play", "toll")
        )

    def test_wind_loops_only_in_a_looping_scene(self) -> None:
        """Wind is the bed under ambient scenes; a one-shot scare must not loop it."""
        sc = scene(
            loop=True, score=[{"t": 0, "synth": "wind"}, {"t": 0, "synth": "organ"}]
        )
        ops = [c["op"] for c in gp.to_previewer(sc, 1, "", {})["cues"]]
        self.assertEqual(ops, ["play_loop", "play"])
        one = scene(score=[{"t": 0, "synth": "wind"}])
        self.assertEqual(gp.to_previewer(one, 1, "", {})["cues"][0]["op"], "play")

    def test_set_cue_carries_zone_effect_and_level(self) -> None:
        s = scene(
            cues=[
                {
                    "t": 40,
                    "op": "set",
                    "zone": "door",
                    "effect": "blood",
                    "level": 0.25,
                    "note": "turn",
                }
            ]
        )
        cue = gp.to_previewer(s, 1, "", {})["cues"][0]
        self.assertEqual(
            cue,
            {
                "t": 40,
                "bus": "LED",
                "op": "set",
                "zone": "door",
                "eff": "blood",
                "detail": "turn",
                "level": 0.25,
            },
        )

    def test_strike_cue_keeps_its_zone_and_length(self) -> None:
        s = scene(cues=[{"t": 5, "op": "strike", "zone": "towerL", "ms": 70}])
        cue = gp.to_previewer(s, 1, "", {})["cues"][0]
        self.assertEqual((cue["zone"], cue["ms"]), ("towerL", 70))
        bare = scene(cues=[{"t": 5, "op": "strike"}])
        self.assertNotIn("zone", gp.to_previewer(bare, 1, "", {})["cues"][0])

    def test_cues_come_out_in_time_order(self) -> None:
        """The transport walks the list forward; unsorted cues would be skipped."""
        s = scene(
            score=[{"t": 2.0, "synth": "toll"}],
            cues=[{"t": 3000, "op": "strike"}, {"t": 100, "op": "strike"}],
        )
        ts = [c["t"] for c in gp.to_previewer(s, 1, "", {})["cues"]]
        self.assertEqual(ts, sorted(ts))

    def test_unknown_effect_in_a_cue_is_rejected(self) -> None:
        s = scene(cues=[{"t": 0, "op": "set", "zone": "door", "effect": "nope"}])
        with self.assertRaises(SystemExit):
            gp.to_previewer(s, 1, "", {})

    def test_unknown_base_effect_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            gp.to_previewer(scene(base={"door": "nope"}), 1, "", {})

    def test_unknown_cue_op_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            gp.to_previewer(scene(cues=[{"t": 0, "op": "fade"}]), 1, "", {})

    def test_yaml_slice_is_attached(self) -> None:
        raw = "scenes:\n  - id: probe\n    name: Probe\n"
        self.assertIn("name: Probe", gp.to_previewer(scene(), 1, raw, {})["yaml"])


class TestInjection(unittest.TestCase):
    """A half-spliced page is worse than none: it would load and misbehave."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._saved = (
            gp.STYLES,
            gp.PANELS,
            gp.MOBILE,
            gp.WEB,
            gp.BUNDLE,
            gp.subprocess,
        )
        gp.STYLES = self.tmp / "styles.css"
        gp.STYLES.write_text("body { color: red }\n\n")
        gp.PANELS = self.tmp / "panels.css"
        gp.PANELS.write_text("/* panels */\n")
        gp.MOBILE = self.tmp / "mobile.css"
        gp.MOBILE.write_text("/* mobile */\n")
        gp.WEB = self.tmp / "web"
        (gp.WEB / "node_modules").mkdir(parents=True)
        gp.BUNDLE = gp.WEB / "dist" / "bundle.js"
        gp.BUNDLE.parent.mkdir(parents=True)
        self.js = "console.log(1);"
        gp.subprocess = self.fake_subprocess(0)  # type: ignore[assignment]  # test double

    def tearDown(self) -> None:
        (gp.STYLES, gp.PANELS, gp.MOBILE, gp.WEB, gp.BUNDLE, gp.subprocess) = (
            self._saved
        )
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_subprocess(self, rc: int) -> types.SimpleNamespace:
        """Stand in for esbuild: the bundler is not what these tests are about."""

        def run(*_a: object, **_k: object) -> types.SimpleNamespace:
            gp.BUNDLE.write_text(self.js)
            return types.SimpleNamespace(returncode=rc, stdout="out", stderr="err")

        return types.SimpleNamespace(run=run)

    def test_styles_replace_the_marker_comment(self) -> None:
        got = gp.inject_styles("<style>/* @STYLES filler */</style>")
        self.assertEqual(
            got, "<style>body { color: red }\n\n/* panels */\n\n/* mobile */</style>"
        )

    def test_missing_style_marker_exits_instead_of_writing_a_broken_page(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            gp.inject_styles("<style></style>")
        self.assertIn("@STYLES", str(cm.exception))

    def test_bundle_replaces_the_marker_comment(self) -> None:
        self.assertEqual(
            gp.inject_bundle("<script>/* @BUNDLE x */</script>"),
            "<script>console.log(1);</script>",
        )

    def test_missing_bundle_marker_exits(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            gp.inject_bundle("<script></script>")
        self.assertIn("@BUNDLE", str(cm.exception))

    def test_missing_node_modules_says_what_to_run(self) -> None:
        shutil.rmtree(gp.WEB / "node_modules")
        with self.assertRaises(SystemExit) as cm:
            gp.inject_bundle("/* @BUNDLE */")
        self.assertIn("npm install", str(cm.exception))

    def test_failed_build_is_not_silently_inlined(self) -> None:
        gp.subprocess = self.fake_subprocess(1)  # type: ignore[assignment]  # test double
        with self.assertRaises(SystemExit) as cm:
            gp.inject_bundle("/* @BUNDLE */")
        self.assertIn("esbuild failed", str(cm.exception))

    def test_bundle_containing_a_script_tag_is_refused(self) -> None:
        """A literal </script> in the JS would close the tag and truncate the page."""
        self.js = 'x = "</script>";'
        with self.assertRaises(SystemExit) as cm:
            gp.inject_bundle("/* @BUNDLE */")
        self.assertIn("</script>", str(cm.exception))


class TestLeanRewrite(unittest.TestCase):
    """lean() serves two masters: the studio's /studio/scene-audio/<id> and
    the device's /site/<sid>.mp3 (grade report 2026-08-23 A5/G1)."""

    HTML = '{"vigil": "data:audio/mpeg;base64,AAAA", "storm": "data:audio/mpeg;base64,BB=="}'

    def test_default_is_the_studio_route(self) -> None:
        out = gp.lean(self.HTML)
        self.assertIn('"vigil": "/studio/scene-audio/vigil"', out)
        self.assertNotIn("data:audio/mpeg", out)

    def test_device_route_and_suffix(self) -> None:
        out = gp.lean(self.HTML, route="/site/", suffix=".mp3")
        self.assertIn('"vigil": "/site/vigil.mp3"', out)
        self.assertIn('"storm": "/site/storm.mp3"', out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
