"""Tests for the studio server's helpers, without standing a server up.

The pure parts: shelling out, reading scene ids, guessing the LAN address,
describing a track file, and parsing an upload. Multipart parsing is
hand-rolled — it only has to handle the one upload shape the page sends, but
it has to handle that one correctly.

Stopping the server lives here too, since it needs a throwaway server of its
own rather than the shared one in test_studio_api: the point is that it dies.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import studio  # noqa: E402


class Quiet(studio.Handler):
    """The real handler logs every request to stderr; tests do not need that."""

    def log_message(self, fmt, *a):
        pass


class TestPureHelpers(unittest.TestCase):
    """The parts that need no socket. Cheap to test, easy to get wrong."""

    def test_run_reports_success_and_output(self) -> None:
        ok, out = studio.run([sys.executable, "-c", "print('hello')"])
        self.assertTrue(ok)
        self.assertIn("hello", out)

    def test_run_reports_failure_with_stderr_folded_in(self) -> None:
        """The log goes straight to the page, so stderr must not be dropped."""
        ok, out = studio.run(
            [sys.executable, "-c",
             "import sys; sys.stderr.write('boom'); sys.exit(1)"])
        self.assertFalse(ok)
        self.assertIn("boom", out)

    def test_scene_ids_reads_the_real_scenes_file(self) -> None:
        ids = studio.scene_ids()
        self.assertIn("vigil", ids)
        self.assertEqual(len(ids), len(set(ids)), "duplicate scene ids")

    def test_scene_ids_survives_an_unreadable_file(self) -> None:
        """A half-edited scenes.yaml must not take the whole panel down."""
        with mock.patch.object(studio, "SCENES", ROOT / "no" / "such.yaml"):
            self.assertEqual(studio.scene_ids(), [])

    def test_lan_ip_is_a_dotted_quad(self) -> None:
        ip = studio.lan_ip()
        parts = ip.split(".")
        self.assertEqual(len(parts), 4, ip)
        for part in parts:
            self.assertTrue(part.isdigit() and 0 <= int(part) <= 255, ip)

    def test_track_info_reports_a_broken_file_instead_of_raising(self) -> None:
        """A corrupt import still has to appear in the list so it can be removed."""
        tmp = Path(tempfile.mkdtemp())
        try:
            bad = tmp / "_t_studio_broken.mp3"
            bad.write_bytes(b"not an mp3 at all")
            info = studio.track_info(bad)
            self.assertEqual(info["id"], "_t_studio_broken")
            self.assertIn("error", info)
            self.assertNotIn("dur", info)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestMultipart(unittest.TestCase):
    """Uploads are parsed by hand rather than with cgi, so parse it properly."""

    def body(self, filename: str, data: bytes, boundary: str = "BOUND") -> bytes:
        return (f"--{boundary}\r\nContent-Disposition: form-data; "
                f'name="file"; filename="{filename}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n").encode() \
            + data + f"\r\n--{boundary}--\r\n".encode()

    def test_extracts_name_and_bytes(self) -> None:
        name, data = studio.parse_multipart(
            self.body("clip.wav", b"\x00\x01\x02payload"),
            "multipart/form-data; boundary=BOUND")
        self.assertEqual(name, "clip.wav")
        self.assertEqual(data, b"\x00\x01\x02payload")

    def test_strips_any_path_from_the_filename(self) -> None:
        """A browser can send a full path; writing it verbatim would escape tracks/."""
        name, _ = studio.parse_multipart(
            self.body("../../etc/passwd", b"x"),
            "multipart/form-data; boundary=BOUND")
        self.assertEqual(name, "passwd")

    def test_quoted_boundary_is_accepted(self) -> None:
        name, _ = studio.parse_multipart(
            self.body("a.wav", b"x"), 'multipart/form-data; boundary="BOUND"')
        self.assertEqual(name, "a.wav")

    def test_no_boundary_yields_nothing(self) -> None:
        self.assertEqual(studio.parse_multipart(b"whatever", "text/plain"), ("", b""))

    def test_a_part_without_a_filename_is_not_a_file(self) -> None:
        body = (b"--B\r\nContent-Disposition: form-data; name=\"opts\"\r\n\r\n"
                b"{}\r\n--B--\r\n")
        self.assertEqual(studio.parse_multipart(body, "multipart/form-data; boundary=B"),
                         ("", b""))


class TestServerStop(unittest.TestCase):
    """Stopping has to answer first, or the page reports a network error.

    Its own server, because the point of the test is that this one dies.
    """

    def test_stop_replies_then_shuts_down(self) -> None:
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Quiet)
        port = srv.server_address[1]
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            r = urllib.request.Request(f"http://127.0.0.1:{port}/api/server/stop",
                                       data=b"{}", method="POST",
                                       headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(r, timeout=10) as f:
                d = json.loads(f.read())
            self.assertTrue(d["ok"])
            self.assertTrue(d["stopping"])
            t.join(timeout=5)
            self.assertFalse(t.is_alive(), "the server did not shut down")
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
