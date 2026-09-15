"""Raw imported audio must survive streamed light overrides."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class LiveLightPlaybackTests(unittest.TestCase):
    def test_light_override_only_stops_an_authored_scene(self):
        source = (ROOT / "firmware" / "castle_sd_common.yaml").read_text()
        light = source.split("act.type == castle_web::LIGHT", 1)[1].split(
            "act.type == castle_web::SCENE", 1
        )[0]
        self.assertIn('id(current_scene).state != "stop"', light)
        self.assertIn("id(scene_stop)->execute()", light)


if __name__ == "__main__":
    unittest.main()
