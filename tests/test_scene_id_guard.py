"""/api/scene must never answer {"queued":true} for a scene it cannot run.

The id list is seeded at boot from pir_scene's options. Until it is, the
handler used to accept ANYTHING — a client that retries from power-on got
"queued" for a typo and then watched nothing happen. An unseeded list is a
"not ready" answer now, which a retry can act on.

sd_web.h needs esp_http_server to compile, so this reads the handler's
source. tests/test_live_light_playback.py executes the parts that can be.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = (ROOT / "firmware" / "sd_web.h").read_text()


def handler(name: str) -> str:
    body = WEB.split(f"inline esp_err_t {name}(httpd_req_t *req) {{", 1)[1]
    return body.split("\ninline ", 1)[0]


class SceneIdGuardTests(unittest.TestCase):
    def test_an_unseeded_id_list_is_not_ready_rather_than_permissive(self) -> None:
        h_scene = handler("h_scene")
        self.assertIn("g_scene_ids.empty()", h_scene)
        self.assertIn("503 Service Unavailable", h_scene)
        # The old shape: "empty OR found" let every id through before boot.
        self.assertNotIn("!g_scene_ids.empty() &&", h_scene)

    def test_a_seeded_list_still_404s_an_unknown_id(self) -> None:
        h_scene = handler("h_scene")
        self.assertIn("404 Not Found", h_scene)
        self.assertIn("unknown scene", h_scene)

    def test_the_list_is_seeded_at_boot(self) -> None:
        yaml = (ROOT / "firmware" / "castle_sd_common.yaml").read_text()
        self.assertIn("castle_web::set_scene_ids(", yaml)


if __name__ == "__main__":
    unittest.main()
