"""Small pins for the radio HTTP server's request parsing and routing."""

import io
import re
import tempfile
import threading
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import import_routes
import request_guard
import server

HERE = Path(__file__).resolve().parent
#: A literal castle route in a browser source. The template-literal ones
#: carry their query after a `?`, which stops the match.
DEVICE_PATH = re.compile(r"""['"`](/radio/device[a-zA-Z0-9/_-]*)""")
JSON = {"Content-Type": "application/json"}
MARKED = {request_guard.MARKER_HEADER: request_guard.MARKER_VALUE}


class _Caller(server.Handler):
    """Enough of a Handler to run one request and keep what it answered: the
    real routing, the real guards, a headers mapping and a body in memory
    instead of a socket."""

    def __init__(self, headers=None, body=b"", path="/"):
        # Deliberately not Handler.__init__: a socket would serve the request.
        self.headers = Message()
        for name, value in (headers or {}).items():
            self.headers[name] = value
        if "Content-Length" not in self.headers:
            self.headers["Content-Length"] = str(len(body))
        self.rfile = io.BytesIO(body)
        self.path = path
        self.sent = None
        self.errored = None

    def reply(self, body, status=200):
        self.sent = (status, body)

    def send_error(self, code, message=None, explain=None):
        self.errored = code

    def answer(self):
        """What the route replied. A route that answered nothing at all is a
        failure of the test, not a None to thread through it."""
        assert self.sent is not None, f"{self.path} answered nothing"
        return self.sent


def post(route, headers=None, body=b""):
    """Drive do_POST the way the socket would, and report the answer."""
    caller = _Caller(headers, body, route)
    server.Handler.do_POST(caller)
    return caller.answer()


class DeviceEventsRouteTests(unittest.TestCase):
    """C1: the control room's "Recent castle events" panel had no route to
    call, and the page blamed the firmware for the 404."""

    def test_the_events_route_is_served_and_asks_the_castle_for_its_ring(self):
        self.assertIn("/radio/device/events", server.Handler.GET_ROUTES)
        caller = _Caller()
        with patch(
            "device_bridge.call", return_value=[{"t": 1, "e": "play", "a": "x"}]
        ) as call:
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
        status, answer = caller.answer()
        self.assertEqual(status, 502)
        self.assertIn("no castle", answer["error"])

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


class DesktopRoutesTests(unittest.TestCase):
    def test_tools_endpoint_returns_the_launchers_identity_contract(self):
        payload = {
            "service": "castle-radio",
            "protocol": 1,
            "ready": False,
            "checks": [{"name": "model", "ok": False}],
        }
        caller = _Caller()
        with patch("desktop_tools.status", return_value=payload):
            server.Handler.GET_ROUTES["/radio/tools"](caller, None)
        self.assertEqual(
            caller.sent,
            (200, {**payload, "castle_origin": "http://" + server.device_bridge.HOST}),
        )

    def test_every_page_script_is_served_by_the_desktop(self):
        page = (HERE / "index.html").read_text()
        for name in re.findall(r'<script src="([^"]+)"', page):
            self.assertIn("/" + name, server.STATIC_ROUTES)


class ImportRouteTableTests(unittest.TestCase):
    """The import half lives in import_routes.py; the table is the only thing
    that says so, and a patch target that drifts from it silently stops
    patching the code the test drives."""

    def test_the_three_import_routes_are_the_modules_own_functions(self):
        self.assertEqual(
            {
                route: server.Handler.POST_ROUTES[route]
                for route in ("/radio/import", "/radio/retry", "/radio/reprocess")
            },
            {
                "/radio/import": import_routes.post_import,
                "/radio/retry": import_routes.post_retry,
                "/radio/reprocess": import_routes.post_reprocess,
            },
        )


class ServerFixTests(unittest.TestCase):
    """The same three fixes, asked of the running handler rather than of its
    source text (grade report 2026-09-17 D3): an assertion about a source
    line survives the rename that breaks the behaviour."""

    def test_sync_status_reads_key_as_a_query_param(self):
        caller = _Caller()
        with patch("remote_library.job", return_value={"phase": "done"}) as job:
            server.Handler.GET_ROUTES["/radio/device/sync-status"](
                caller, urlsplit("/radio/device/sync-status?x=1&key=job7")
            )
        job.assert_called_once_with("job7")
        self.assertEqual(caller.sent, (200, {"phase": "done"}))

    def test_retry_and_reprocess_cap_the_body_they_read(self):
        """The cap is json_body's 4 KB, not the upload route's 100 MB."""
        for route in ("/radio/retry", "/radio/reprocess"):
            body = b'{"id":"' + b"x" * 5000 + b'"}'
            status, answer = post(route, JSON, body)
            self.assertEqual(status, 400, route)
            self.assertIn("Invalid", answer["error"])

    def test_imported_show_reads_the_catalog_under_lock(self):
        """Another thread cannot take LOCK while the catalog is being read."""
        taken = []

        def peek():
            thread = threading.Thread(target=lambda: taken.append(_grab()))
            thread.start()
            thread.join()
            return [{"key": "radio_a", "cues": [1]}]

        def _grab():
            got = server.LOCK.acquire(timeout=0.2)
            if got:
                server.LOCK.release()
            return got

        with patch("server.catalog", side_effect=peek):
            self.assertEqual(
                server.imported_show({"action": "file", "key": "radio_a"})["cues"], [1]
            )
        self.assertEqual(taken, [False])


class SimplePostTests(unittest.TestCase):
    """E2 (grade report 2026-09-17 E2): every state-changing route has to be
    one a browser will not send cross-origin without a preflight, because
    this server answers no OPTIONS at all."""

    def setUp(self):
        self.pool = patch("import_routes.POOL").start()
        patch.dict(server.JOBS, {}, clear=True).start()
        self.addCleanup(patch.stopall)

    def test_a_form_shaped_content_type_is_refused_on_a_json_route(self):
        for kind in ("text/plain", "application/x-www-form-urlencoded", None):
            headers = {} if kind is None else {"Content-Type": kind}
            status, answer = post("/radio/retry", headers, b'{"id":"radio_a"}')
            self.assertEqual(status, 415, kind)
            self.assertIn("application/json", answer["error"])

    def test_a_json_route_still_answers_its_own_page(self):
        server.JOBS["radio_a"] = {
            "id": "radio_a",
            "done": True,
            "source": "s",
            "title": "t",
            "split": False,
        }
        status, answer = post(
            "/radio/retry",
            {"Content-Type": "application/json; charset=utf-8"},
            b'{"id":"radio_a"}',
        )
        self.assertEqual((status, answer["id"]), (202, "radio_a"))
        self.assertEqual(self.pool.submit.call_count, 1)

    def test_an_upload_without_the_marker_header_is_refused(self):
        status, answer = post(
            "/radio/import", {"Content-Type": "application/octet-stream"}, b"ID3 audio"
        )
        self.assertEqual(status, 403)
        self.assertIn(request_guard.MARKER_HEADER, answer["error"])
        self.assertEqual(server.JOBS, {})
        self.assertEqual(self.pool.submit.call_count, 0)

    def test_a_marked_upload_is_accepted_and_queued(self):
        headers = {
            "Content-Type": "application/octet-stream",
            "X-Filename": "a.mp3",
            **MARKED,
        }
        with patch(
            "import_routes.DATA", Path(self.enterContext(tempfile.TemporaryDirectory()))
        ):
            status, answer = post("/radio/import", headers, b"ID3 audio bytes")
        self.assertEqual(status, 202)
        self.assertEqual(answer["source_name"], "a.mp3")
        self.assertEqual(self.pool.submit.call_count, 1)

    def test_restore_needs_the_marker_too_since_it_carries_no_body(self):
        with patch("library_ops.restore", return_value={"ok": True}) as restore:
            self.assertEqual(post("/radio/restore/radio_a")[0], 403)
            restore.assert_not_called()
            self.assertEqual(
                post("/radio/restore/radio_a", MARKED), (200, {"ok": True})
            )

    def test_the_ninth_waiting_job_is_refused_rather_than_queued(self):
        for n in range(request_guard.QUEUE_LIMIT):
            server.JOBS[f"radio_{n}"] = {"id": f"radio_{n}", "done": False}
        headers = {"Content-Type": "application/octet-stream", **MARKED}
        status, answer = post("/radio/import", headers, b"ID3 audio bytes")
        self.assertEqual(status, 429)
        self.assertIn("waiting", answer["error"])
        self.assertEqual(self.pool.submit.call_count, 0)
        # One finishing makes room for the next, without a restart.
        server.JOBS["radio_0"]["done"] = True
        with patch(
            "import_routes.DATA", Path(self.enterContext(tempfile.TemporaryDirectory()))
        ):
            self.assertEqual(post("/radio/import", headers, b"ID3 audio")[0], 202)


if __name__ == "__main__":
    unittest.main()
