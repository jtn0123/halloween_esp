"""The Rust studio's import group against the Python's — B5 pass 4.

Sync imports (JSON url validation, multipart uploads through the real
import_track.py), refresh from the kept source, the failure body with its
one-line reason, and an async job polled to completion on both servers.
Timestamps and sandbox paths are the only licensed differences: `imported`
stamps are dropped and each side's paths are masked before comparing.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from helpers import make_click_track
from studio_rust_case import CARGO, IN_CI, StudioPair, fetch

BOUND = "XPARITYBOUND"


def multipart(
    name: str, data: bytes, opts: dict[str, object] | None = None
) -> tuple[dict[str, str], bytes]:
    body = (
        (
            f"--{BOUND}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "\r\n"
        ).encode()
        + data
        + f"\r\n--{BOUND}--\r\n".encode()
    )
    headers = {"Content-Type": f"multipart/form-data; boundary={BOUND}"}
    if opts is not None:
        headers["X-Import-Opts"] = json.dumps(opts)
    return headers, body


@unittest.skipIf(CARGO is None and not IN_CI, "no cargo")
class ImportRoutes(StudioPair):
    def norm_tracks(self, tracks: object, side: str) -> list[dict[str, Any]]:
        """Track rows with the licensed differences taken out."""
        assert isinstance(tracks, list)
        out = []
        for row in tracks:
            assert isinstance(row, dict)
            cleaned = dict(row)
            cleaned.pop("imported", None)
            cleaned["source"] = self.masked(str(cleaned.get("source", "")), side)
            out.append(cleaned)
        return out

    def test_01_validation_shapes_match(self) -> None:
        posts: list[tuple[str, bytes]] = [
            ("/studio/import", json.dumps({"url": ""}).encode()),
            ("/studio/import", json.dumps({"url": "ftp://x"}).encode()),
            ("/studio/import/async", json.dumps({"url": "notaurl"}).encode()),
            (
                "/studio/import/async",
                json.dumps({"url": "http://onward.test/x", "id": "bad id!"}).encode(),
            ),
            ("/studio/refresh", json.dumps({}).encode()),
            ("/studio/refresh", json.dumps({"id": "../evil"}).encode()),
            ("/studio/stems", json.dumps({}).encode()),
            ("/studio/stems", json.dumps({"id": "zzz"}).encode()),
        ]
        hdrs = {"Content-Type": "application/json"}
        for path, body in posts:
            a, b = self.both(path, "POST", hdrs, body)
            self.assertEqual(a[0], 400, f"{path} {body!r} -> {a[2]!r}")
            self.assertEqual(a[2], b[2], f"{path} {body!r}")
        a, b = self.both("/studio/job/zzz")
        self.assertEqual(a[0], 404)
        self.assertEqual(a[2], b[2])
        h, empty = multipart("x.wav", b"")
        a, b = self.both("/studio/import", "POST", h, empty)
        self.assertEqual(a[0], 400)
        self.assertEqual(a[2], b[2])

    def test_02_multipart_import_lands_in_both_libraries(self) -> None:
        src = self.tmp / "fresh_import.wav"
        if not src.exists():
            make_click_track(src, seconds=1.0)
        h, body = multipart("fresh_import.wav", src.read_bytes(), {"id": "fresh"})
        a, b = self.both("/studio/import", "POST", h, body)
        self.assertEqual(a[0], 200, a[2][:600])
        da, db = self.parsed(a), self.parsed(b)
        assert isinstance(da, dict) and isinstance(db, dict)
        self.assertTrue(da["ok"] and db["ok"])
        self.assertEqual(
            self.masked(str(da["log"]), "py"), self.masked(str(db["log"]), "rs")
        )
        self.assertEqual(
            self.norm_tracks(da["tracks"], "py"), self.norm_tracks(db["tracks"], "rs")
        )
        for tracks in (self.py_tracks, self.rs_tracks):
            self.assertTrue((tracks / "fresh.mp3").exists())
            # --keep-source names the kept copy by TRACK id, not upload name.
            self.assertTrue((tracks / "_src" / "fresh.wav").exists())
            self.assertFalse((tracks / "_upload").exists())
        ma = json.loads((self.py_tracks / "tracks.json").read_text())
        mb = json.loads((self.rs_tracks / "tracks.json").read_text())
        for side, m in (("py", ma), ("rs", mb)):
            entry = m["fresh"]
            entry.pop("imported", None)
            entry["source"] = self.masked(str(entry["source"]), side)
        self.assertEqual(ma, mb)

    def test_03_refresh_rebuilds_from_the_kept_source(self) -> None:
        hdrs = {"Content-Type": "application/json"}
        body = json.dumps({"id": "fresh", "take": 0.5}).encode()
        a, b = self.both("/studio/refresh", "POST", hdrs, body)
        self.assertEqual(a[0], 200, a[2][:600])
        da, db = self.parsed(a), self.parsed(b)
        assert isinstance(da, dict) and isinstance(db, dict)
        self.assertEqual(
            self.masked(str(da["log"]), "py"), self.masked(str(db["log"]), "rs")
        )
        self.assertEqual(
            self.norm_tracks(da["tracks"], "py"), self.norm_tracks(db["tracks"], "rs")
        )
        rows = [r for r in self.norm_tracks(da["tracks"], "py") if r["id"] == "fresh"]
        self.assertEqual(len(rows), 1)
        self.assertLessEqual(float(rows[0]["dur"]), 0.6)

    def test_04_a_failed_import_answers_one_reason(self) -> None:
        h, body = multipart("not-audio.wav", b"\x00garbage-not-a-riff")
        a, b = self.both("/studio/import", "POST", h, body)
        self.assertEqual(a[0], 500, a[2][:400])
        da, db = self.parsed(a), self.parsed(b)
        assert isinstance(da, dict) and isinstance(db, dict)
        self.assertFalse(da["ok"] or db["ok"])
        self.assertEqual(da["reason"], db["reason"], (da["reason"], db["reason"]))
        self.assertEqual(
            self.masked(str(da["log"]), "py"), self.masked(str(db["log"]), "rs")
        )
        self.assertEqual(list(da.keys()), list(db.keys()))

    def test_05_async_import_polls_to_the_same_end(self) -> None:
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            partial(SimpleHTTPRequestHandler, directory=str(self.tmp)),
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        clip = self.tmp / "clip.wav"
        if not clip.exists():
            make_click_track(clip, seconds=1.0)
        url = f"http://127.0.0.1:{server.server_address[1]}/clip.wav"
        hdrs = {"Content-Type": "application/json"}
        body = json.dumps({"url": url, "id": "fetched"}).encode()
        finals: list[dict[str, Any]] = []
        for port in (self.py_port, self.rs_port):
            code, _, raw = fetch(port, "/studio/import/async", "POST", hdrs, body)
            self.assertEqual(code, 200, raw[:300])
            job = json.loads(raw)
            assert isinstance(job, dict)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                code, _, raw = fetch(port, f"/studio/job/{job['id']}")
                self.assertEqual(code, 200)
                d = json.loads(raw)
                assert isinstance(d, dict)
                if d["done"]:
                    finals.append(d)
                    break
                time.sleep(0.2)
            else:
                self.fail(f"job on {port} never finished: {raw[:300]!r}")
        pa, pb = finals
        self.assertEqual(pa["phase"], pb["phase"], (pa.get("error"), pb.get("error")))
        self.assertEqual(pa["error"], pb["error"])
        if pa["phase"] == "done":
            self.assertTrue((self.py_tracks / "fetched.mp3").exists())
            self.assertTrue((self.rs_tracks / "fetched.mp3").exists())
            self.assertEqual(
                self.norm_tracks(pa["tracks"], "py"),
                self.norm_tracks(pb["tracks"], "rs"),
            )


if __name__ == "__main__":
    unittest.main()
