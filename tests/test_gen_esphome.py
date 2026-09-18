"""Tests for the firmware generator.

scenes.yaml is the source of truth; this turns it into what the device runs.
Until v5.67 that meant an ESPHome script per scene, stepped by `delay:`, and
most of the tests below were about the delta arithmetic that turned absolute
cue times into deltas. There are no deltas any more: a scene is a cue file on
the card, whose records carry the authored absolute milliseconds, plus one
entry in a manifest carrying the numbers the script held as literals.

So every assertion about the emitted script has been ported to the thing that
replaced it — the cue records and the manifest entry — and the two that had no
successor are named where they used to be. tests/test_scene_cue_equivalence.py
is the proof that the port lost nothing.
"""

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path
from typing import Any, ClassVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import cue_file
import gen_esphome as ge
import gen_previewer as gp
import gen_scene_cards as gc
import gen_show as gs
import rig_layout as rl
import scene_manifest
import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent

#: Every path gen_esphome writes through, plus its two inputs. Redirected
#: wholesale in setUp so a test run cannot touch the real firmware tree — or,
#: since v5.67, the real PUBLISH directory: CARD_SCENES is audio/card/scenes/,
#: which `sd_sync scenes` pushes to the castle, so a fixture scene left there
#: would be on the card the next time the user published.
OUTPUT_PATHS = (
    "SRC",
    "MARKERS",
    "OUT",
    "AUDIO_SD",
    "RIG_OUT",
    "LIGHTS_OUT",
    "FALLBACK_SCENES_OUT",
    "CARD_SCENES",
)

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


class EsphomeLoader(yaml.SafeLoader):
    """SafeLoader that accepts ESPHome's own tags. `!lambda 'return …;'` is a
    scalar as far as YAML is concerned, and the C inside it is exactly what a
    test that reads hand-written firmware YAML wants to assert on."""


EsphomeLoader.add_multi_constructor(
    "!", lambda loader, suffix, node: loader.construct_scalar(node)
)


def parse_script(lines: list[str]) -> dict[str, Any]:
    """A generated YAML fragment, loaded the way ESPHome would."""
    return dict(yaml.safe_load("script:\n" + "\n".join(lines))["script"][0])


def card(sc: dict[str, Any], markers: dict[str, Any] | None = None) -> dict[str, Any]:
    """One scene, through the whole card path: expand its pulses, encode the
    cue file, decode it back. What the device will actually read."""
    cues = gc.scene_cues(sc, markers or {})
    return cue_file.decode(cue_file.encode(sc, cues, ZIDS))


def manifest(*scenes: dict[str, Any]) -> list[dict[str, Any]]:
    return scene_manifest.decode(scene_manifest.encode(list(scenes)))


class TestZonePixels(unittest.TestCase):
    """Wrong pixel ranges mean a zone lights up its neighbour's jewel."""

    def test_ranges_are_contiguous_and_inclusive(self) -> None:
        got = ge.zone_pixels(rl.zone_layouts(ZONES, 7), ZONES)
        self.assertEqual(got["towerL"], (0, 6))
        self.assertEqual(got["towerR"], (7, 13))
        self.assertEqual(got["door"], (14, 20))

    def test_single_pixel_zones(self) -> None:
        """The 8mm through-hole build sets pixels_per_zone: 1 — no off-by-one."""
        got = ge.zone_pixels(rl.zone_layouts(ZONES, 1), ZONES)
        self.assertEqual([got[z] for z in ZIDS], [(0, 0), (1, 1), (2, 2)])

    def test_fixture_zone_uses_its_own_count(self) -> None:
        """A ring12 door is 12 px even though pixels_per_zone says 7 — the
        count lives in the fixture, not in a `pixels:` key on the zone."""
        zones = [
            {"id": "towerL"},
            {"id": "towerR"},
            {"id": "door", "fixture": "ring12"},
        ]
        got = ge.zone_pixels(rl.zone_layouts(zones, 7), zones)
        self.assertEqual(got["door"], (14, 25))

    def test_covers_the_whole_chain_without_gaps(self) -> None:
        per = 7
        covered = sorted(
            i
            for lo, hi in ge.zone_pixels(rl.zone_layouts(ZONES, per), ZONES).values()
            for i in range(lo, hi + 1)
        )
        self.assertEqual(covered, list(range(len(ZONES) * per)))


class TestEffectIds(unittest.TestCase):
    def test_known_effect_maps_to_its_firmware_integer(self) -> None:
        self.assertEqual(ge.eff_id("off", "s"), 0)
        self.assertEqual(ge.eff_id("blood", "s"), 12)

    def test_unknown_effect_fails_the_build(self) -> None:
        """A scene naming an effect the firmware lacks must stop the build.

        Emitting the script anyway would compile — the lambda would just set a
        number the render switch does not handle — and the zone would be dark
        on the night with nothing in the logs.
        """
        with self.assertRaises(SystemExit) as cm:
            ge.eff_id("nosuch", "storm")
        self.assertIn("storm", str(cm.exception))
        self.assertIn("nosuch", str(cm.exception))

    def test_both_generators_know_the_same_effects(self) -> None:
        """Two hand-maintained lists of the same enum; they must not drift.

        gen_esphome maps names to integers, gen_previewer only validates names.
        If one gains an effect the other rejects, the previewer and the device
        disagree about which scenes are even legal.
        """
        self.assertEqual(set(ge.EFFECT_IDS), gp.KNOWN_EFFECTS)

    def test_ids_are_distinct(self) -> None:
        self.assertEqual(len(set(ge.EFFECT_IDS.values())), len(ge.EFFECT_IDS))


class TestPulseCues(unittest.TestCase):
    """Beat markers are what lock the lights to the audio; this is the join."""

    MARKS: ClassVar[dict[str, Any]] = {
        "probe": {
            "heart": [[0, 1.0], [500, 0.5], [1000, 0.25]],
            "whisper": [[100, 1.0], [200, 1.0], [300, 1.0]],
        }
    }

    def test_no_pulse_block_is_no_cues(self) -> None:
        self.assertEqual(ge.pulse_cues(scene(), self.MARKS), [])

    def test_synth_without_markers_is_skipped_not_fatal(self) -> None:
        """A silent stream should degrade to no light, not break the build."""
        s = scene(pulse=[{"synth": "absent", "zone": "door"}])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(ge.pulse_cues(s, self.MARKS), [])
        self.assertIn("no markers for synth 'absent'", out.getvalue())

    def test_scene_with_no_markers_at_all(self) -> None:
        s = scene(pulse=[{"synth": "heart"}])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ge.pulse_cues(s, {}), [])

    def test_single_zone(self) -> None:
        s = scene(pulse=[{"synth": "heart", "zone": "door"}])
        cues = ge.pulse_cues(s, self.MARKS)
        self.assertEqual([c["targets"] for c in cues], [["door"]] * 3)

    def test_zone_list_hits_every_named_zone(self) -> None:
        s = scene(pulse=[{"synth": "heart", "zones": ["towerL", "towerR"]}])
        cues = ge.pulse_cues(s, self.MARKS)
        self.assertTrue(all(c["targets"] == ["towerL", "towerR"] for c in cues))

    def test_no_zone_means_every_zone(self) -> None:
        """`targets: None` is the emitter's signal for the whole chain."""
        s = scene(pulse=[{"synth": "heart"}])
        self.assertTrue(all(c["targets"] is None for c in ge.pulse_cues(s, self.MARKS)))

    def test_alternate_round_robins_across_the_zones(self) -> None:
        """Whispers move between the towers; they must not fire on both."""
        s = scene(
            pulse=[
                {"synth": "whisper", "zones": ["towerL", "towerR"], "alternate": True}
            ]
        )
        cues = ge.pulse_cues(s, self.MARKS)
        self.assertEqual(
            [c["targets"] for c in cues], [["towerL"], ["towerR"], ["towerL"]]
        )

    def test_intensity_scales_with_each_markers_velocity(self) -> None:
        """A quiet thump has to make a dim flash, or the dynamics are lost."""
        s = scene(pulse=[{"synth": "heart", "intensity": 0.8}])
        self.assertEqual(
            [c["intensity"] for c in ge.pulse_cues(s, self.MARKS)], [0.8, 0.4, 0.2]
        )

    def test_defaults_when_the_stream_says_nothing(self) -> None:
        c = ge.pulse_cues(scene(pulse=[{"synth": "heart"}]), self.MARKS)[0]
        self.assertEqual(c["intensity"], 0.3)
        self.assertEqual(c["color"], ge.WHITE)
        self.assertEqual(c["decay"], ge.DEFAULT_DECAY)
        self.assertEqual(c["note"], "heart")

    def test_streams_merge_and_keep_their_own_colour(self) -> None:
        s = scene(
            pulse=[
                {"synth": "heart", "color": [1, 0, 0, 0], "decay": 0.82},
                {"synth": "whisper", "color": [0, 1, 0, 0], "decay": 0.94},
            ]
        )
        cues = ge.pulse_cues(s, self.MARKS)
        self.assertEqual(len(cues), 6)
        self.assertEqual({c["decay"] for c in cues}, {0.82, 0.94})


class TestSceneOnTheCard(unittest.TestCase):
    """What a scene BECOMES: a cue file and a manifest entry.

    Every test here is the successor of one that read the emitted script.
    Two have no successor and are listed at the end of the class, with why.
    """

    def times(
        self, sc: dict[str, Any], markers: dict[str, Any] | None = None
    ) -> list[int]:
        return [r["t"] for r in card(sc, markers)["records"]]

    def test_records_carry_the_authored_absolute_times(self) -> None:
        """Was `test_deltas_replay_to_the_original_absolute_times`.

        The one calculation that turned a correct scene into a wrong show was
        the delta encoding — ESPHome only knows `delay:`, so every cue time
        was a subtraction. A record carries the author's own millisecond, so
        the arithmetic is gone; what is left to check is that the number
        arrives unchanged, including through the u32 it is packed into.
        """
        times = [80, 240, 355, 1400, 2600, 4100]
        s = scene(cues=[{"t": t, "op": "strike"} for t in times])
        self.assertEqual(self.times(s), times)

    def test_out_of_order_cues_are_sorted(self) -> None:
        """A negative delay used to be emitted as `delay: -200ms`; a record
        out of order would be walked past and never fire at all, because
        tick() stops at the first record whose time has not come."""
        s = scene(cues=[{"t": t, "op": "strike"} for t in (900, 100, 400)])
        self.assertEqual(self.times(s), [100, 400, 900])

    def test_simultaneous_cues_are_two_records_at_one_time(self) -> None:
        s = scene(
            cues=[
                {"t": 500, "op": "strike", "zone": "door"},
                {"t": 500, "op": "set", "zone": "towerL", "effect": "wisp"},
            ]
        )
        doc = card(s)
        self.assertEqual([r["t"] for r in doc["records"]], [500, 500])
        # And the authored order survives the tie, because tick() applies
        # both in one frame and the second overwrites the first.
        self.assertEqual([r["op"] for r in doc["records"]], ["strike", "set"])

    def test_pulse_and_hand_written_cues_are_interleaved_in_time(self) -> None:
        s = scene(
            cues=[{"t": 300, "op": "strike"}], pulse=[{"synth": "h", "zone": "door"}]
        )
        marks = {"probe": {"h": [[100, 1.0], [700, 1.0]]}}
        self.assertEqual(self.times(s, marks), [100, 300, 700])

    def test_the_scene_length_is_the_manifest_entry(self) -> None:
        """Was `test_tail_delay_pads_the_scene_to_its_full_duration`. A script
        padded to `duration_ms` with a trailing `delay:`; the runner reads the
        number and waits on it (castle_scenes::finished), and the cue file
        carries it too for a raw song that has no manifest row."""
        s = scene(duration_ms=5000, cues=[{"t": 1200, "op": "strike"}])
        self.assertEqual(card(s)["duration_ms"], 5000)
        self.assertEqual(manifest(s)[0]["duration_ms"], 5000)

    def test_the_base_look_is_the_cue_files_zone_records(self) -> None:
        """Was `test_base_state_reset_covers_every_zone_variable`.

        A scene may interrupt another mid-strike, so it starts from
        known-good: castle_cues::apply_base writes all thirteen zone globals
        from these records, flash colour and decay included, which is what
        keeps the first seconds of a scene from being tinted by the last one.
        """
        s = scene(
            levels={"door": 0.4},
            base={"towerL": "spirit", "towerR": "off", "door": "ember"},
        )
        zones = card(s)["zones"]
        self.assertEqual([z["effect"] for z in zones], [4, 0, 2])
        self.assertEqual(zones[2]["level"], 0.40)
        self.assertEqual(zones[0]["level"], 1.00)  # unlisted -> full
        self.assertEqual([z["center"] for z in zones], [-1, -1, -1])

    def test_zone_missing_from_base_falls_back_to_off(self) -> None:
        zones = card(scene(base={"door": "candle"}))["zones"]
        self.assertEqual([z["effect"] for z in zones], [0, 0, 1])

    def test_looping_is_a_manifest_flag(self) -> None:
        """Was `test_looping_scene_re_executes_itself` / `..._scene_ends`.
        Ambient scenes loop; a triggered scare that looped would never stop.
        One bit in the entry now, read by castle_scenes::loops()."""
        self.assertTrue(manifest(scene(loop=True))[0]["loop"])
        self.assertFalse(manifest(scene())[0]["loop"])

    def test_the_scene_level_is_the_manifest_entry(self) -> None:
        """Was the `id(speaker_hush) ? 0.0f : 0.45f` literal in the emitted
        volume lambda. Whole percent, because that is the precision
        /api/volume and rig.h's ceiling already work in."""
        self.assertEqual(manifest(scene(volume=0.45))[0]["volume_pct"], 45)
        self.assertEqual(manifest(scene())[0]["volume_pct"], 80)  # the default

    def test_the_audio_token_is_the_one_sfx_is_given(self) -> None:
        self.assertEqual(manifest(scene())[0]["audio"], "01_probe")
        self.assertEqual(scene_manifest.audio_token(9, "x"), "09_x")

    def test_set_cue_writes_effect_and_optional_level(self) -> None:
        s = scene(
            cues=[
                {"t": 10, "op": "set", "zone": "towerR", "effect": "wisp",
                 "level": 0.25},
            ]
        )  # fmt: skip
        rec = card(s)["records"][0]
        self.assertEqual((rec["op"], rec["mask"], rec["effect"]), ("set", 0b010, 7))
        self.assertEqual(rec["level"], 0.25)

    def test_set_cue_without_a_level_leaves_it_alone(self) -> None:
        """The script simply did not emit the assignment; a fixed-width record
        has to say "keep" out loud, and 255 is how (cue_file.LEVEL_KEEP)."""
        s = scene(cues=[{"t": 10, "op": "set", "zone": "towerR", "effect": "wisp"}])
        self.assertIsNone(card(s)["records"][0]["level"])

    def test_strike_cue_writes_flash_decay_and_all_four_colour_channels(self) -> None:
        s = scene(
            cues=[
                {"t": 10, "op": "strike", "zone": "door", "intensity": 0.5,
                 "color": [0.1, 0.2, 0.3, 0.4], "decay": 0.75},
            ]
        )  # fmt: skip
        rec = card(s)["records"][0]
        self.assertEqual(rec["mask"], 0b100)
        self.assertEqual(rec["intensity"], 0.5)
        self.assertEqual(rec["decay"], 0.75)
        self.assertEqual(rec["color"], [0.1, 0.2, 0.3, 0.4])

    def test_strike_with_no_zone_hits_the_whole_chain(self) -> None:
        rec = card(scene(cues=[{"t": 10, "op": "strike"}]))["records"][0]
        self.assertEqual((rec["mask"], rec["intensity"]), (0b111, 1.0))

    def test_unknown_cue_op_fails_the_build(self) -> None:
        s = scene(cues=[{"t": 0, "op": "fade"}])
        with self.assertRaises(SystemExit):
            card(s)

    def test_unknown_zone_in_a_cue_is_not_silently_dropped(self) -> None:
        s = scene(cues=[{"t": 0, "op": "set", "zone": "attic", "effect": "wisp"}])
        with self.assertRaises(SystemExit):
            card(s)

    def test_unknown_effect_in_a_cue_fails_the_build(self) -> None:
        s = scene(cues=[{"t": 0, "op": "set", "zone": "door", "effect": "nosuch"}])
        with self.assertRaises(SystemExit):
            card(s)

    # WITHOUT A SUCCESSOR, deliberately:
    #
    #   test_no_tail_delay_when_the_last_cue_is_at_the_end — counted the
    #   emitted `delay:` lines. There are none; the length is a number in the
    #   manifest whether or not a cue lands on it, which the two tests above
    #   already pin.
    #
    #   test_emitted_yaml_is_loadable — a scene's script had to survive
    #   safe_load, so a quote in a note or a colon in a name could break the
    #   ESPHome build late. Nothing about a scene reaches YAML any more; the
    #   generated file is three fixed scripts. What replaced the risk is the
    #   fixed-width encoding itself, and its refusals are
    #   tests/test_cue_file_cxx.py's.


class TestShowPlaylistLength(unittest.TestCase):
    """B04/B60: the playlist has to bill the same speaker wait the scene
    script spends before its own timeline starts, or `scene_stop` lands
    SOUND_WAIT_MS early and cuts the authored tail off every scene.

    J1 (grade report 2026-09-17 pm): the DURATION half of that sum is read at
    run time now — castle_scenes::length_ms(), the number the card's manifest
    carries for the scene `run_scene` just started. Compiled, a republished
    duration_ms was cut short or left a gap until the next OTA, which is the
    one thing "a scene edit is a publish" cannot be allowed to mean.
    """

    #: What one hold is, on both sides of the sum.
    HOLD = f"return {gs.SOUND_WAIT_MS} + castle_scenes::length_ms();"

    @staticmethod
    def _doc(*scenes: dict[str, Any]) -> dict[str, Any]:
        return {"scenes": list(scenes), "show": {"gap_ms": 12000}}

    def _then(self, doc: dict[str, Any]) -> list[dict[str, Any]]:
        lines = gs.emit_show_playlist(doc)
        return list(
            yaml.load("script:\n" + "\n".join(lines), EsphomeLoader)["script"][0][
                "then"
            ]
        )

    def test_playlist_delay_is_the_wait_plus_the_cards_own_length(self) -> None:
        then = self._then(self._doc(scene(duration_ms=6500)))
        self.assertEqual(
            then[0]["script.execute"], {"id": "run_scene", "scene": "probe"}
        )
        self.assertEqual(then[1], {"delay": self.HOLD})
        self.assertEqual(then[2], {"script.execute": "scene_stop"})
        self.assertEqual(then[3], {"delay": "12000ms"})

    def test_every_scene_in_the_order_gets_the_wait(self) -> None:
        doc = self._doc(
            scene(id="a", duration_ms=1000), scene(id="b", duration_ms=193360)
        )
        delays = [st["delay"] for st in self._then(doc) if "delay" in st]
        self.assertEqual(delays, [self.HOLD, "12000ms", self.HOLD, "12000ms"])

    def test_an_edited_duration_leaves_the_generated_playlist_alone(self) -> None:
        """The property J1 actually bought: the emitted YAML does not carry a
        scene's length at all, so changing one is a publish and nothing else.
        Only the ORDER and the gap are still facts about the build."""
        short = gs.emit_show_playlist(self._doc(scene(duration_ms=1000)))
        long = gs.emit_show_playlist(self._doc(scene(duration_ms=193360)))
        self.assertEqual(short, long)

    def test_the_runner_waits_for_exactly_that_long(self) -> None:
        """The two numbers are one constant. The scene side is hand-written
        YAML now (firmware/castle_scenes.yaml), so this reads it out of the
        file rather than out of an emitter — a `timeout:` edited there without
        the playlist would cut the tail off every scene of the evening."""
        self.assertIs(ge.SOUND_WAIT_MS, gs.SOUND_WAIT_MS)
        runner = yaml.load(
            (ROOT_DIR / "firmware" / "castle_scenes.yaml").read_text(), EsphomeLoader
        )
        run = next(s for s in runner["script"] if s["id"] == "scene_run")
        body = next(st for st in run["then"] if "if" in st)["if"]["then"]
        wait = next(st["wait_until"] for st in body if "wait_until" in st)
        self.assertEqual(wait["timeout"], f"{gs.SOUND_WAIT_MS}ms")
        self.assertEqual(
            wait["condition"]["lambda"], "return id(castle_speaker)->is_running();"
        )
