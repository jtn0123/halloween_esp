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
STATE = (ROOT / "firmware" / "sd_web_state.h").read_text()
BOOT = (ROOT / "firmware" / "castle_sd_common.yaml").read_text()


def handler(name: str) -> str:
    body = WEB.split(f"inline esp_err_t {name}(httpd_req_t *req) {{", 1)[1]
    return body.split("\ninline ", 1)[0]


class SceneIdGuardTests(unittest.TestCase):
    def test_an_unseeded_id_list_is_not_ready_rather_than_permissive(self) -> None:
        h_scene = handler("h_scene")
        self.assertIn("scene_id_state(s)", h_scene)
        self.assertIn("503 Service Unavailable", h_scene)
        # The verdict itself: 0 is "not seeded", and it is read under the
        # lock with the list, never as two questions with a gap between.
        self.assertIn("if (g_scene_ids.empty()) return 0;", STATE)

    def test_a_seeded_list_still_404s_an_unknown_id(self) -> None:
        h_scene = handler("h_scene")
        self.assertIn("404 Not Found", h_scene)
        self.assertIn("unknown scene", h_scene)

    def test_the_pir_route_faces_the_same_list(self) -> None:
        """A4/C7: /api/pir?scene= used to pass any string through to the
        select, where an unknown option is a log line nobody reads."""
        h_pir = handler("h_pir")
        self.assertIn("scene_id_state(s)", h_pir)
        self.assertIn("unknown scene", h_pir)
        self.assertIn("bad separator", h_pir)  # and no '|' in the packing

    def test_the_list_is_seeded_at_boot(self) -> None:
        self.assertIn("castle_web::set_scene_ids(", BOOT)

    def test_the_list_is_seeded_before_the_server_starts(self) -> None:
        """A5. start() returns with the httpd task already taking requests,
        so seeding afterwards raced every first-poll read of the list."""
        self.assertLess(
            BOOT.index("castle_web::set_scene_ids("),
            BOOT.index("castle_web::start();"),
        )

    def test_every_reader_and_writer_of_the_list_holds_the_lock(self) -> None:
        """A5, the other half: a re-seed later in life must be safe too, so
        the vector is guarded rather than merely ordered."""
        for fn in ("inline void set_scene_ids(", "inline int scene_id_state("):
            start = STATE.index(fn)
            self.assertIn(
                "std::scoped_lock lk(g_state_mu);", STATE[start : start + 300]
            )
        # ...and those two are the only places that name it: a third reader
        # in sd_web.h, where it used to live, would be an unlocked one.
        self.assertNotIn("g_scene_ids", WEB.replace("// g_scene_ids,", ""))


if __name__ == "__main__":
    unittest.main()
