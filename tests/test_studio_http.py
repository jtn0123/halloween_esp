"""The transport half of the studio: how the desk page leaves the socket.

The page is 2.4 MB and every load on the LAN path used to send all of it,
uncompressed and uncacheable. The firmware already did better (sd_web_site.h
serves index.html.gz with a validator); this holds studio_http.py to the
same bar — gzip when the browser can take it, ETag + If-None-Match -> 304,
and `no-store` kept for everything that is not the page.

No sockets: a handler is built over a BytesIO, which is all send_response/
send_header/end_headers need. The request headers are a real email.Message,
which is what BaseHTTPRequestHandler parses them into.
"""

from __future__ import annotations

import email.message
import gzip
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import studio_http as sh

HTML = "text/html; charset=utf-8"


class Fake(sh.JsonHandler):
    """A JsonHandler with the socket replaced by a buffer."""

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = email.message.Message()
        for k, v in (headers or {}).items():
            self.headers[k] = v
        self.buf = io.BytesIO()
        self.wfile = self.buf
        self.rfile = io.BytesIO()
        self.requestline = "GET / HTTP/1.1"
        self.request_version = "HTTP/1.1"
        self.command = "GET"
        self.client_address = ("127.0.0.1", 0)
        self.close_connection = False

    def log_message(self, fmt, *a):
        pass

    def response(self) -> tuple[int, dict[str, str], bytes]:
        raw = self.buf.getvalue()
        head, _, body = raw.partition(b"\r\n\r\n")
        lines = head.decode().split("\r\n")
        code = int(lines[0].split()[1])
        hdrs = {}
        for ln in lines[1:]:
            k, _, v = ln.partition(":")
            hdrs[k.strip().lower()] = v.strip()
        return code, hdrs, body


PAGE = ("<!doctype html><title>desk</title>" + "<p>cue</p>" * 2000).encode()


class TestPageDelivery(unittest.TestCase):
    def setUp(self) -> None:
        sh._GZ.clear()
        self.addCleanup(sh._GZ.clear)

    def test_html_without_gzip_support_is_plain_but_validated(self) -> None:
        h = Fake()
        h.send_bytes(PAGE, HTML)
        code, hdrs, body = h.response()
        self.assertEqual(code, 200)
        self.assertEqual(body, PAGE)
        self.assertNotIn("content-encoding", hdrs)
        self.assertEqual(hdrs["content-length"], str(len(PAGE)))
        self.assertEqual(hdrs["cache-control"], "no-cache")
        self.assertTrue(hdrs["etag"].startswith('"'), hdrs["etag"])

    def test_html_is_gzipped_when_the_browser_accepts_it(self) -> None:
        h = Fake({"Accept-Encoding": "gzip, deflate, br"})
        h.send_bytes(PAGE, HTML)
        code, hdrs, body = h.response()
        self.assertEqual(code, 200)
        self.assertEqual(hdrs["content-encoding"], "gzip")
        self.assertEqual(hdrs["vary"], "Accept-Encoding")
        self.assertEqual(hdrs["content-length"], str(len(body)))
        self.assertLess(len(body), len(PAGE) // 4)
        self.assertEqual(gzip.decompress(body), PAGE)

    def test_gzip_is_compressed_once_per_body(self) -> None:
        with mock.patch.object(sh.gzip, "compress", wraps=gzip.compress) as m:
            for _ in range(3):
                Fake({"Accept-Encoding": "gzip"}).send_bytes(PAGE, HTML)
            self.assertEqual(m.call_count, 1)
            Fake({"Accept-Encoding": "gzip"}).send_bytes(PAGE + b"!", HTML)
            self.assertEqual(m.call_count, 2)

    def test_the_gzip_cache_is_bounded(self) -> None:
        with mock.patch.object(sh, "KEEP_GZ", 2):
            for i in range(5):
                Fake({"Accept-Encoding": "gzip"}).send_bytes(
                    PAGE + bytes([i]), HTML)
        self.assertEqual(len(sh._GZ), 2)

    def test_matching_if_none_match_is_a_304_with_no_body(self) -> None:
        first = Fake({"Accept-Encoding": "gzip"})
        first.send_bytes(PAGE, HTML)
        _, hdrs, _ = first.response()
        again = Fake({"Accept-Encoding": "gzip", "If-None-Match": hdrs["etag"]})
        again.send_bytes(PAGE, HTML)
        code, h2, body = again.response()
        self.assertEqual(code, 304)
        self.assertEqual(body, b"")
        self.assertEqual(h2["etag"], hdrs["etag"])
        self.assertNotIn("content-length", h2)

    def test_a_changed_page_gets_a_new_etag(self) -> None:
        a = Fake()
        a.send_bytes(PAGE, HTML)
        b = Fake({"If-None-Match": a.response()[1]["etag"]})
        b.send_bytes(PAGE + b"<!-- rebuilt -->", HTML)
        self.assertEqual(b.response()[0], 200)

    def test_weak_and_listed_validators_match(self) -> None:
        tag = sh.content_etag(PAGE)
        self.assertTrue(sh.etag_matches(f"W/{tag}", tag))
        self.assertTrue(sh.etag_matches(f'"other-1", {tag}', tag))
        self.assertTrue(sh.etag_matches("*", tag))
        self.assertFalse(sh.etag_matches('"other-1"', tag))
        self.assertFalse(sh.etag_matches(None, tag))

    def test_accept_encoding_parsing(self) -> None:
        self.assertTrue(sh.accepts_gzip("gzip"))
        self.assertTrue(sh.accepts_gzip("br, GZIP;q=0.5"))
        self.assertTrue(sh.accepts_gzip("*"))
        self.assertFalse(sh.accepts_gzip("gzip;q=0"))
        self.assertFalse(sh.accepts_gzip("identity"))
        self.assertFalse(sh.accepts_gzip(""))
        self.assertFalse(sh.accepts_gzip(None))

    def test_non_html_bodies_stay_no_store_and_uncompressed(self) -> None:
        h = Fake({"Accept-Encoding": "gzip"})
        h.send_bytes(b"\x00" * 4096, "image/png")
        code, hdrs, body = h.response()
        self.assertEqual(code, 200)
        self.assertEqual(hdrs["cache-control"], "no-store")
        self.assertNotIn("etag", hdrs)
        self.assertNotIn("content-encoding", hdrs)
        self.assertEqual(body, b"\x00" * 4096)

    def test_json_keeps_no_store(self) -> None:
        h = Fake({"Accept-Encoding": "gzip", "If-None-Match": "*"})
        h.send_json({"ok": True})
        code, hdrs, _ = h.response()
        self.assertEqual(code, 200)
        self.assertNotIn("content-encoding", hdrs)
        self.assertNotIn("etag", hdrs)


class TestSendFile(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="castle-http-"))
        self.page = self.tmp / "index.html"
        self.page.write_bytes(PAGE)
        sh._GZ.clear()
        self.addCleanup(sh._GZ.clear)

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_etag_is_mtime_and_size(self) -> None:
        h = Fake({"Accept-Encoding": "gzip"})
        h.send_file(self.page, HTML)
        code, hdrs, body = h.response()
        st = self.page.stat()
        self.assertEqual(code, 200)
        self.assertEqual(hdrs["etag"], f'"{st.st_mtime_ns}-{st.st_size}"')
        self.assertEqual(gzip.decompress(body), PAGE)

    def test_304_does_not_read_the_file(self) -> None:
        st = self.page.stat()
        h = Fake({"If-None-Match": f'"{st.st_mtime_ns}-{st.st_size}"'})
        with mock.patch.object(Path, "read_bytes",
                               side_effect=AssertionError("read")) as m:
            h.send_file(self.page, HTML)
            self.assertEqual(m.call_count, 0)
        code, _, body = h.response()
        self.assertEqual(code, 304)
        self.assertEqual(body, b"")

    def test_a_rewritten_file_misses_the_validator(self) -> None:
        st = self.page.stat()
        old = f'"{st.st_mtime_ns}-{st.st_size}"'
        self.page.write_bytes(PAGE + b"<!-- v2 -->")
        os.utime(self.page, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        h = Fake({"If-None-Match": old})
        h.send_file(self.page, HTML)
        code, hdrs, _ = h.response()
        self.assertEqual(code, 200)
        self.assertNotEqual(hdrs["etag"], old)


class TestMultipartNames(unittest.TestCase):
    """Path("..").name is ".." — a filename the staging dir must never see."""

    @staticmethod
    def body(filename: str) -> bytes:
        return (b"--B\r\nContent-Disposition: form-data; name=\"file\"; "
                b'filename="' + filename.encode() + b'"\r\n\r\nx\r\n--B--\r\n')

    def test_dot_dot_empty_and_dot_are_400_not_500(self) -> None:
        for name in ("..", ".", "", "a/.."):
            with self.assertRaises(sh.BadRequest, msg=name):
                sh.parse_multipart(self.body(name), "multipart/form-data; boundary=B")

    def test_a_real_name_still_parses(self) -> None:
        self.assertEqual(sh.parse_multipart(self.body("../x.wav"),
                                            "multipart/form-data; boundary=B"),
                         ("x.wav", b"x"))


if __name__ == "__main__":
    unittest.main()
