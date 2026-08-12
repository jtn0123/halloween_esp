"""Tests for the ESPHome generator.

scenes.yaml is the source of truth; this turns it into the scripts the device
actually runs. The cue timings are emitted as DELTAS between absolute times,
which is the part most likely to be quietly wrong.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import gen_esphome as ge        # noqa: E402
import gen_previewer as gp      # noqa: E402
import yaml                     # noqa: E402

ZONES = [{"id": "towerL"}, {"id": "towerR"}, {"id": "door"}]
ZIDS = [z["id"] for z in ZONES]


def scene(**over: object) -> dict:
    """A minimal scene that both generators accept, for overriding per test."""
    s = {
        "id": "probe", "name": "Probe", "kind": "triggered",
        "duration_ms": 5000,
        "base": {"towerL": "candle", "towerR": "candle", "door": "ember"},
    }
    s.update(over)
    return s


def parse_script(lines: list[str]) -> dict:
    """emit_scene's lines are a YAML fragment; load them the way ESPHome would."""
    return yaml.safe_load("script:\n" + "\n".join(lines))["script"][0]


def cue_lambdas(lines: list[str]) -> list[str]:
    """The lambdas a scene runs after its audio starts — i.e. the cues."""
    then = parse_script(lines)["then"]
    start = next(i for i, st in enumerate(then)
                 if isinstance(st.get("script.execute"), dict)
                 and st["script.execute"].get("id") == "sfx")
    return [st["lambda"] for st in then[start + 1:] if "lambda" in st]


class TestZonePixels(unittest.TestCase):
    """Wrong pixel ranges mean a zone lights up its neighbour's jewel."""

    def test_ranges_are_contiguous_and_inclusive(self) -> None:
        got = ge.zone_pixels(ZONES, 7)
        self.assertEqual(got["towerL"], (0, 6))
        self.assertEqual(got["towerR"], (7, 13))
        self.assertEqual(got["door"], (14, 20))

    def test_single_pixel_zones(self) -> None:
        """The 8mm through-hole build sets pixels_per_zone: 1 — no off-by-one."""
        got = ge.zone_pixels(ZONES, 1)
        self.assertEqual([got[z] for z in ZIDS], [(0, 0), (1, 1), (2, 2)])

    def test_covers_the_whole_chain_without_gaps(self) -> None:
        per = 7
        covered = sorted(
            i for lo, hi in ge.zone_pixels(ZONES, per).values()
            for i in range(lo, hi + 1))
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

    MARKS = {"probe": {"heart": [[0, 1.0], [500, 0.5], [1000, 0.25]],
                       "whisper": [[100, 1.0], [200, 1.0], [300, 1.0]]}}

    def test_no_pulse_block_is_no_cues(self) -> None:
        self.assertEqual(ge.pulse_cues(scene(), self.MARKS), [])

    def test_synth_without_markers_is_skipped_not_fatal(self) -> None:
        """A silent stream should degrade to no light, not break the build."""
        s = scene(pulse=[{"synth": "absent", "zone": "door"}])
        self.assertEqual(ge.pulse_cues(s, self.MARKS), [])

    def test_scene_with_no_markers_at_all(self) -> None:
        s = scene(pulse=[{"synth": "heart"}])
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
        s = scene(pulse=[{"synth": "whisper", "zones": ["towerL", "towerR"],
                          "alternate": True}])
        cues = ge.pulse_cues(s, self.MARKS)
        self.assertEqual([c["targets"] for c in cues],
                         [["towerL"], ["towerR"], ["towerL"]])

    def test_intensity_scales_with_each_markers_velocity(self) -> None:
        """A quiet thump has to make a dim flash, or the dynamics are lost."""
        s = scene(pulse=[{"synth": "heart", "intensity": 0.8}])
        self.assertEqual([c["intensity"] for c in ge.pulse_cues(s, self.MARKS)],
                         [0.8, 0.4, 0.2])

    def test_defaults_when_the_stream_says_nothing(self) -> None:
        c = ge.pulse_cues(scene(pulse=[{"synth": "heart"}]), self.MARKS)[0]
        self.assertEqual(c["intensity"], 0.3)
        self.assertEqual(c["color"], ge.WHITE)
        self.assertEqual(c["decay"], ge.DEFAULT_DECAY)
        self.assertEqual(c["note"], "heart")

    def test_streams_merge_and_keep_their_own_colour(self) -> None:
        s = scene(pulse=[
            {"synth": "heart", "color": [1, 0, 0, 0], "decay": 0.82},
            {"synth": "whisper", "color": [0, 1, 0, 0], "decay": 0.94}])
        cues = ge.pulse_cues(s, self.MARKS)
        self.assertEqual(len(cues), 6)
        self.assertEqual({c["decay"] for c in cues}, {0.82, 0.94})
class TestEmitScene(unittest.TestCase):
    """The emitted script is stepped by delays, so the arithmetic is the risk."""

    def cue_times(self, lines: list[str]) -> list[int]:
        """Replay the script the way ESPHome would and record when cues land."""
        times, t, started = [], 0, False
        for ln in lines:
            s = ln.strip()
            if s == "id: sfx":
                started = True
            elif started and s.startswith("- delay:"):
                t += int(s.split()[-1].removesuffix("ms"))
            elif started and s.startswith("- lambda:"):
                times.append(t)
        return times

    def test_deltas_replay_to_the_original_absolute_times(self) -> None:
        """The one calculation that turns a correct scene into a wrong show.

        Source cues are absolute; ESPHome only knows `delay:`. Summing the
        emitted deltas back must land exactly on the times the author wrote,
        with no drift accumulated across the scene.
        """
        times = [80, 240, 355, 1400, 2600, 4100]
        s = scene(cues=[{"t": t, "op": "strike"} for t in times])
        self.assertEqual(self.cue_times(ge.emit_scene(s, ZONES, 1, {})), times)

    def test_out_of_order_cues_are_sorted_before_delta_encoding(self) -> None:
        """A negative delay would be silently emitted as `delay: -200ms`."""
        s = scene(cues=[{"t": t, "op": "strike"} for t in (900, 100, 400)])
        self.assertEqual(self.cue_times(ge.emit_scene(s, ZONES, 1, {})),
                         [100, 400, 900])

    def test_simultaneous_cues_share_one_delay(self) -> None:
        s = scene(cues=[{"t": 500, "op": "strike", "zone": "door"},
                        {"t": 500, "op": "set", "zone": "towerL",
                         "effect": "wisp"}])
        lines = ge.emit_scene(s, ZONES, 1, {})
        self.assertEqual(self.cue_times(lines), [500, 500])
        self.assertEqual(sum("delay:" in ln for ln in lines), 2)  # 500 + tail

    def test_pulse_and_hand_written_cues_are_interleaved_in_time(self) -> None:
        s = scene(cues=[{"t": 300, "op": "strike"}],
                  pulse=[{"synth": "h", "zone": "door"}])
        marks = {"probe": {"h": [[100, 1.0], [700, 1.0]]}}
        self.assertEqual(self.cue_times(ge.emit_scene(s, ZONES, 1, marks)),
                         [100, 300, 700])

    def test_tail_delay_pads_the_scene_to_its_full_duration(self) -> None:
        """Without it a looping scene would restart the moment its last cue ran."""
        s = scene(duration_ms=5000, cues=[{"t": 1200, "op": "strike"}])
        lines = ge.emit_scene(s, ZONES, 1, {})
        self.assertIn("      - delay: 3800ms", lines)

    def test_no_tail_delay_when_the_last_cue_is_at_the_end(self) -> None:
        s = scene(duration_ms=1200, cues=[{"t": 1200, "op": "strike"}])
        lines = ge.emit_scene(s, ZONES, 1, {})
        self.assertEqual(sum("delay:" in ln for ln in lines), 1)

    def test_base_state_reset_covers_every_zone_variable(self) -> None:
        """A scene may interrupt another mid-strike, so it starts from known-good.

        Leaving zone_flash_col or zone_level from the previous scene means the
        first seconds of a scene are tinted by whatever ran before it — the
        kind of bug that only shows when scenes are triggered out of order.
        """
        s = scene(levels={"door": 0.4}, base={"towerL": "spirit",
                                              "towerR": "off", "door": "ember"})
        reset = ge.emit_scene(s, ZONES, 1, {})[8]
        for i, eff in enumerate([4, 0, 2]):
            self.assertIn(f"id(zone_effect)[{i}] = {eff};", reset)
            self.assertIn(f"id(zone_flash)[{i}] = 0.0f;", reset)
            self.assertIn(f"id(zone_flash_decay)[{i}] = {ge.DEFAULT_DECAY}f;", reset)
            for k in range(4):
                self.assertIn(f"id(zone_flash_col)[{i * 4 + k}] = 1.0f;", reset)
        self.assertIn("id(zone_level)[2] = 0.40f;", reset)
        self.assertIn("id(zone_level)[0] = 1.00f;", reset)   # unlisted -> full

    def test_zone_missing_from_base_falls_back_to_off(self) -> None:
        reset = ge.emit_scene(scene(base={"door": "candle"}), ZONES, 1, {})[8]
        self.assertIn("id(zone_effect)[0] = 0;", reset)
        self.assertIn("id(zone_effect)[2] = 1;", reset)

    def test_looping_scene_re_executes_itself(self) -> None:
        lines = ge.emit_scene(scene(loop=True), ZONES, 1, {})
        self.assertIn("      - script.execute: scene_probe", lines)

    def test_non_looping_scene_ends(self) -> None:
        """Ambient scenes loop; a triggered scare that looped would never stop."""
        lines = ge.emit_scene(scene(), ZONES, 1, {})
        self.assertNotIn("script.execute: scene_probe", "\n".join(lines))

    def test_set_cue_writes_effect_and_optional_level(self) -> None:
        s = scene(cues=[{"t": 10, "op": "set", "zone": "towerR",
                         "effect": "wisp", "level": 0.25}])
        self.assertEqual(cue_lambdas(ge.emit_scene(s, ZONES, 1, {})),
                         ["id(zone_effect)[1] = 7; id(zone_level)[1] = 0.25f;"])

    def test_strike_cue_writes_flash_decay_and_all_four_colour_channels(self) -> None:
        s = scene(cues=[{"t": 10, "op": "strike", "zone": "door",
                         "intensity": 0.5, "color": [0.1, 0.2, 0.3, 0.4],
                         "decay": 0.75}])
        lam = cue_lambdas(ge.emit_scene(s, ZONES, 1, {}))[0]
        self.assertIn("id(zone_flash)[2] = 0.500f;", lam)
        self.assertIn("id(zone_flash_decay)[2] = 0.75f;", lam)
        for k, v in enumerate([0.10, 0.20, 0.30, 0.40]):
            self.assertIn(f"id(zone_flash_col)[{8 + k}] = {v:.2f}f;", lam)

    def test_strike_with_no_zone_hits_the_whole_chain(self) -> None:
        s = scene(cues=[{"t": 10, "op": "strike"}])
        lam = cue_lambdas(ge.emit_scene(s, ZONES, 1, {}))[0]
        for i in range(3):
            self.assertIn(f"id(zone_flash)[{i}] = 1.000f;", lam)

    def test_unknown_cue_op_fails_the_build(self) -> None:
        s = scene(cues=[{"t": 0, "op": "fade"}])
        with self.assertRaises(SystemExit):
            ge.emit_scene(s, ZONES, 1, {})

    def test_unknown_zone_in_a_cue_is_not_silently_dropped(self) -> None:
        s = scene(cues=[{"t": 0, "op": "set", "zone": "attic", "effect": "wisp"}])
        with self.assertRaises(ValueError):
            ge.emit_scene(s, ZONES, 1, {})

    def test_emitted_yaml_is_loadable(self) -> None:
        """ESPHome parses this file; a quoting slip would break the build late."""
        s = scene(loop=True, volume=0.45, levels={"door": 0.3},
                  cues=[{"t": 80, "op": "strike", "note": "lightning"},
                        {"t": 900, "op": "set", "zone": "door",
                         "effect": "blood", "note": "turn"}],
                  pulse=[{"synth": "h", "zones": ["towerL", "towerR"],
                          "alternate": True, "color": [1, 0, 0, 0]}])
        got = parse_script(ge.emit_scene(s, ZONES, 1,
                                         {"probe": {"h": [[100, 1.0], [200, 0.5]]}}))
        self.assertEqual(got["id"], "scene_probe")
        self.assertEqual(got["mode"], "restart")
        self.assertEqual(got["then"][3]["media_player.volume_set"], 0.45)
class TestGenEsphomeMain(unittest.TestCase):
    """End to end: a scenes.yaml on disk becomes a loadable ESPHome file."""

    DOC = {
        "hardware": {"pixels_per_zone": 7},
        "zones": ZONES,
        "scenes": [
            scene(id="a", loop=True, cues=[{"t": 100, "op": "strike"}]),
            scene(id="b", pulse=[{"synth": "h", "zone": "door"}]),
        ],
    }

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        # EVERY module-level output path must be redirected here. MEDIA_OUT was
        # forgotten when it was added, and these tests then wrote their two
        # fixture scenes into the real firmware/generated/media_files.yaml —
        # which broke the next firmware build with "cannot find 01_a.mp3".
        self._saved = (ge.SRC, ge.MARKERS, ge.OUT, ge.MEDIA_OUT,
                       ge.AUDIO_FLASH, ge.AUDIO_SD, ge.ROOT)
        ge.ROOT = self.tmp
        ge.SRC = self.tmp / "scenes.yaml"
        ge.MARKERS = self.tmp / "markers.json"
        ge.OUT = self.tmp / "generated" / "scenes.yaml"
        ge.MEDIA_OUT = self.tmp / "generated" / "media_files.yaml"
        ge.AUDIO_FLASH = self.tmp / "generated" / "audio_flash.yaml"
        ge.AUDIO_SD = self.tmp / "generated" / "audio_sd.yaml"
        ge.SRC.write_text(yaml.safe_dump(self.DOC))

    def tearDown(self) -> None:
        (ge.SRC, ge.MARKERS, ge.OUT, ge.MEDIA_OUT,
         ge.AUDIO_FLASH, ge.AUDIO_SD, ge.ROOT) = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_writes_a_parseable_file_with_every_scene_and_a_stop_script(self) -> None:
        self.assertEqual(ge.main(), 0)
        doc = yaml.safe_load(ge.OUT.read_text())
        self.assertEqual([s["id"] for s in doc["script"]],
                         ["scene_a", "scene_b", "scene_stop", "run_scene"])

    def test_blackout_script_clears_every_zone(self) -> None:
        """One call has to be enough to make the whole castle go dark."""
        ge.main()
        doc = yaml.safe_load(ge.OUT.read_text())
        lam = doc["script"][-2]["then"][0]["lambda"]
        for i in range(len(ZONES)):
            self.assertIn(f"id(zone_effect)[{i}] = 0;", lam)
            self.assertIn(f"id(zone_flash)[{i}] = 0.0f;", lam)

    def test_pixel_map_is_written_into_the_header_comment(self) -> None:
        ge.main()
        text = ge.OUT.read_text()
        self.assertIn("DO NOT EDIT", text)
        self.assertIn("door     pixels 14-20", text)

    def test_missing_markers_file_still_generates(self) -> None:
        """`make generate` must work before `make audio` has ever run."""
        ge.main()
        doc = yaml.safe_load(ge.OUT.read_text())
        self.assertEqual(len(doc["script"]), 4)

    def test_markers_file_is_used_when_present(self) -> None:
        ge.MARKERS.write_text('{"b": {"h": [[250, 1.0]]}}')
        ge.main()
        then = yaml.safe_load(ge.OUT.read_text())["script"][1]["then"]
        self.assertIn({"delay": "250ms"}, then)


if __name__ == "__main__":
    unittest.main(verbosity=2)
