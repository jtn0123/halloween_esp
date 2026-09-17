"""Small pins for the radio HTTP server's request parsing and routing."""

import inspect
import re
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

import server

HERE = Path(__file__).resolve().parent
#: A literal castle route in a browser source. The template-literal ones
#: carry their query after a `?`, which stops the match.
DEVICE_PATH = re.compile(r"""['"`](/radio/device[a-zA-Z0-9/_-]*)""")


class _Caller:
    """Enough of a Handler to run one route and keep what it answered."""

    guard = server.Handler.guard

    def __init__(self):
        self.sent = None

    def reply(self, body, status=200):
        self.sent = (status, body)


class DeviceEventsRouteTests(unittest.TestCase):
    """C1: the control room's "Recent castle events" panel had no route to
    call, and the page blamed the firmware for the 404."""

    def test_the_events_route_is_served_and_asks_the_castle_for_its_ring(self):
        self.assertIn("/radio/device/events", server.Handler.GET_ROUTES)
        caller = _Caller()
        with patch("device_bridge.call", return_value=[{"t": 1, "e": "play", "a": "x"}]) as call:
            server.Handler.GET_ROUTES["/radio/device/events"](caller, None)
        # The path is a constant: nothing the browser sent is forwarded.
        call.assert_called_once_with("/api/events")
        self.assertEqual(caller.sent, (200, [{"t": 1, "e": "play", "a": "x"}]))

    def test_a_castle_that_will_not_answer_is_a_json_502_not_an_html_404(self):
        """The bug was not just the missing route: send_error() answers
        HTML, device-link.js does response.json() on it, and the SyntaxError
        was swallowed into "not supported by this firmware"."""
        caller = _Caller()
        with patch("device_bridge.call", side_effect=OSError("no castle")):
            server.Handler.GET_ROUTES["/radio/device/events"](caller, None)
        self.assertEqual(caller.sent[0], 502)
        self.assertIn("no castle", caller.sent[1]["error"])

    def test_every_device_route_the_page_calls_is_served_by_this_server(self):
        """The drift that made C1 possible: the button, the castle-served
        build's route and the desktop server's table are three lists, and
        only two of them agreed."""
        served = set(server.Handler.GET_ROUTES) | set(server.Handler.POST_ROUTES)
        prefixes = (server.DEVICE_AUDIO_PREFIX, "/radio/device/sync")
        for name in ("device-link.js", "remote-library.js", "castle-direct.js"):
            for path in DEVICE_PATH.findall((HERE / name).read_text()):
                self.assertTrue(
                    path in served or path.startswith(prefixes),
                    f"{name} calls {path}, which server.py does not route",
                )


class ServerFixTests(unittest.TestCase):
    def test_sync_status_reads_key_as_a_query_param(self):
        self.assertEqual((parse_qs("x=1&key=job").get("key") or [""])[0], "job")
        self.assertIn("parse_qs", inspect.getsource(server.Handler.get_sync_status))

    def test_retry_and_reprocess_use_the_json_body_cap(self):
        self.assertIn("json_body", inspect.getsource(server.Handler.post_retry))
        self.assertIn("json_body", inspect.getsource(server.Handler.post_reprocess))
        self.assertNotIn("upload_length", inspect.getsource(server.Handler.post_retry))

    def test_imported_show_reads_the_catalog_under_lock(self):
        src = inspect.getsource(server.imported_show)
        self.assertIn("LOCK", src)
        with patch("server.catalog", return_value=[{"key": "radio_a", "cues": [1]}]):
            self.assertEqual(
                server.imported_show({"action": "file", "key": "radio_a"})["cues"], [1]
            )


if __name__ == "__main__":
    unittest.main()
