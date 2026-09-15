"""Raw imported audio must survive streamed light overrides and report its clock."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = (ROOT / "firmware" / "castle_sd_common.yaml").read_text()


def branch(start, end):
    return SOURCE.split(start, 1)[1].split(end, 1)[0]


class LiveLightPlaybackTests(unittest.TestCase):
    def test_light_override_only_stops_an_authored_scene(self):
        light = branch(
            "act.type == castle_web::ActionType::LIGHT",
            "act.type == castle_web::ActionType::SCENE",
        )
        self.assertIn('id(current_scene).state != "stop"', light)
        self.assertIn("id(scene_stop)->execute()", light)

    def test_raw_play_reports_no_scene_and_restarts_the_clock(self):
        play = branch(
            "act.type == castle_web::ActionType::PLAY",
            "act.type == castle_web::ActionType::STOP",
        )
        self.assertIn('id(current_scene).publish_state("stop")', play)
        self.assertIn("castle_web::restart_audio_clock(", play)
        scene = branch(
            "act.type == castle_web::ActionType::SCENE",
            "act.type == castle_web::ActionType::PIRCFG",
        )
        self.assertIn("castle_web::restart_audio_clock(", scene)

    def test_mirror_tick_clears_a_raw_track_when_audio_ends(self):
        mirror = branch("interval: 200ms", "castle_web::take_pending()")
        self.assertIn("castle_web::mirror_audio(playing", mirror)
        self.assertIn("MEDIA_PLAYER_STATE_ANNOUNCING", mirror)
        self.assertIn('id(current_track).publish_state("")', mirror)

    def test_status_reports_the_audio_clock(self):
        web = (ROOT / "firmware" / "sd_web.h").read_text()
        self.assertIn('"playing":%s,"position_ms":%lld', web)
        state = (ROOT / "firmware" / "sd_web_state.h").read_text()
        self.assertIn("inline bool mirror_audio(bool playing, long long now_us)", state)


if __name__ == "__main__":
    unittest.main()
