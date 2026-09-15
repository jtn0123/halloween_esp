"""Control requests must address an installed scene and reject unsupported transport."""

import unittest
from unittest.mock import patch

import device_bridge


class DeviceBridgeTests(unittest.TestCase):
    @patch("device_bridge.call")
    def test_installed_song_dispatch(self, call):
        call.side_effect = [
            {"scenes": "vigil,the_citizens_of_halloween___this,stop"},
            {"ok": True},
        ]
        device_bridge.command(
            {"action": "scene", "scene": "the_citizens_of_halloween___this"}
        )
        self.assertEqual(
            call.call_args.args,
            ("/api/scene?s=the_citizens_of_halloween___this", "POST"),
        )

    @patch("device_bridge.call", return_value={"scenes": "vigil,stop"})
    def test_uninstalled_show_never_dispatched(self, call):
        with self.assertRaisesRegex(ValueError, "not installed"):
            device_bridge.command({"action": "scene", "scene": "radio_new_import"})
        self.assertEqual(call.call_count, 1)

    @patch("device_bridge.call")
    def test_synced_import_dispatches_sd_file(self, call):
        call.side_effect = [
            [{"name": "radio_monster.mp3", "size": 1234}],
            {"queued": True},
        ]
        device_bridge.command({"action": "file", "file": "radio_monster.mp3"})
        self.assertEqual(call.call_args.args, ("/api/play?f=radio_monster.mp3", "POST"))

    @patch("device_bridge.call", return_value=[])
    def test_missing_sd_file_is_not_dispatched(self, call):
        with self.assertRaisesRegex(ValueError, "not on the castle"):
            device_bridge.command({"action": "file", "file": "radio_missing.mp3"})
        self.assertEqual(call.call_count, 1)

    @patch("device_bridge.call")
    def test_unsupported_controls_never_dispatched(self, call):
        for action in ("pause", "seek", "reboot"):
            with self.assertRaises(ValueError):
                device_bridge.command({"action": action})
        call.assert_not_called()

    @patch("device_bridge.call")
    def test_volume_bounds(self, call):
        for volume in (-1, 101):
            with self.assertRaises(ValueError):
                device_bridge.command({"action": "volume", "volume": volume})
        call.assert_not_called()

    @patch("device_bridge.call", return_value={"queued": True})
    def test_light_bench_dispatches_valid_zone_specs(self, call):
        device_bridge.command({"action": "light", "value": "towerL:ff00aa@25"})
        self.assertEqual(
            call.call_args.args, ("/api/light?c=towerL%3Aff00aa%4025", "POST")
        )

    @patch("device_bridge.call")
    def test_light_bench_rejects_unsafe_specs(self, call):
        for value in ("tower-L:red", "door:ff00", "door:ffffff@0", "../off"):
            with self.assertRaisesRegex(ValueError, "light test"):
                device_bridge.command({"action": "light", "value": value})
        call.assert_not_called()

    @patch("device_bridge.time.sleep")
    @patch("device_bridge.call")
    def test_speaker_bench_sets_level_before_playing_tone(self, call, sleep):
        call.side_effect = [
            [{"name": "test_1k.mp3", "size": 1234}],
            {"queued": True},
            {"queued": True},
        ]
        device_bridge.command({"action": "tone", "file": "test_1k.mp3", "volume": 25})
        self.assertEqual(
            [item.args for item in call.call_args_list],
            [
                ("/api/files",),
                ("/api/volume?v=25", "POST"),
                ("/api/play?f=test_1k.mp3", "POST"),
            ],
        )
        sleep.assert_called_once_with(0.3)

    def test_imported_light_frames_coalesce_dense_cues(self):
        frames = device_bridge.imported_light_frames(
            [
                [0.01, "left", 0.2, 0.9],
                [0.05, "door", 0.8, 0.9],
                [0.19, "right", 0.5, 0.9],
                [0.26, "right", 0.4, 0.9],
            ],
            frame_s=0.2,
        )
        self.assertEqual(frames, [(0.0, "door:ff1f05@80"), (0.2, "towerR:4dff8c@40")])

    @patch("device_bridge.start_imported_show")
    @patch("device_bridge.time.sleep")
    @patch("device_bridge.call")
    def test_imported_file_starts_texture_audio_then_generated_frames(
        self, call, sleep, start
    ):
        call.side_effect = [
            [{"name": "radio_monster.mp3", "size": 1234}],
            {"version": "5.51"},
            {"queued": True},
            {"queued": True},
        ]
        show = {"cues": [[0.1, "door", 0.8, 0.9]], "duration": 10}
        device_bridge.command({"action": "file", "file": "radio_monster.mp3"}, show)
        self.assertEqual(
            [item.args for item in call.call_args_list],
            [
                ("/api/files",),
                ("/api/status",),
                ("/api/light?c=show", "POST"),
                ("/api/play?f=radio_monster.mp3", "POST"),
            ],
        )
        sleep.assert_called_once_with(0.3)
        start.assert_called_once_with("radio_monster.mp3", show["cues"], 10)

    @patch("device_bridge.call")
    def test_imported_lights_require_firmware_551(self, call):
        call.side_effect = [
            [{"name": "radio_monster.mp3", "size": 1234}],
            {"version": "5.50"},
        ]
        with self.assertRaisesRegex(ValueError, "5.51"):
            device_bridge.command(
                {"action": "file", "file": "radio_monster.mp3"},
                {"cues": [], "duration": 10},
            )


class PlaybackClockTests(unittest.TestCase):
    @patch("device_bridge.time.monotonic")
    def test_same_scene_poll_does_not_reset_elapsed(self, now):
        now.side_effect = [100, 112, 120, 121]
        device_bridge.playback_clock({"scene": "vigil"}, started_scene="vigil")
        result = device_bridge.playback_clock({"scene": "vigil"})
        self.assertEqual(result["position_s"], 12)
        self.assertTrue(result["estimated"])
        self.assertEqual(result["origin"], "command")
        device_bridge.playback_clock({"scene": "vigil"}, started_scene="vigil")
        self.assertEqual(
            device_bridge.playback_clock({"scene": "vigil"})["position_s"], 1
        )

    @patch("device_bridge.time.monotonic")
    def test_external_scene_change_and_stop(self, now):
        now.side_effect = [200, 220, 230]
        device_bridge.playback_clock({"scene": "vigil"}, started_scene="vigil")
        result = device_bridge.playback_clock({"scene": "storm"})
        self.assertEqual(result["position_s"], 0)
        self.assertEqual(result["origin"], "observed")
        self.assertEqual(
            device_bridge.playback_clock({"scene": "stop"})["position_s"], 0
        )

    @patch("device_bridge.time.monotonic")
    def test_raw_sd_track_has_an_estimated_clock(self, now):
        now.side_effect = [300, 312]
        device_bridge.playback_clock(
            {"scene": "stop", "track": "radio_monster.mp3"},
            started_track="radio_monster.mp3",
        )
        result = device_bridge.playback_clock(
            {"scene": "stop", "track": "radio_monster.mp3"}
        )
        self.assertEqual(result["position_s"], 12)
        self.assertEqual(result["track"], "radio_monster.mp3")
