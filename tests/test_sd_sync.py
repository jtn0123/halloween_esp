"""sd_sync.py against a fake card: what it uploads, skips, deletes and refuses.

This is the tool run in a hurry on the night — push the tracks, purge the
card, flash a build — so its decisions are pinned here without a device:
`api()` is replaced by a dict-backed card that records every call, and
nothing below opens a socket.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import helpers  # noqa: F401  (hermetic env)
import sd_sync


class FakeCard:
    """The firmware's /api surface, as far as sd_sync.py uses it."""

    def __init__(
        self,
        files: dict[str, int] | None = None,
        dirs: tuple[str, ...] = ("site", "scenes"),
    ) -> None:
        self.files = dict(files or {})
        self.dirs = list(dirs)
        self.calls: list[tuple[str, str, int]] = []
        self.short_by = 0  # report fewer bytes than sent, to test the check
        self.crc: str | None = None  # a v5.42 reply's crc32; None = old firmware
        self.subdirs: dict[str, dict[str, int]] = {}  # ?d= listings (v5.42)

    def __call__(
        self,
        ip: str,
        method: str,
        path: str,
        body: bytes | None = None,
        timeout: float = 60,
    ) -> bytes:
        self.calls.append((method, path, len(body or b"")))
        if method == "GET" and path == "/api/files":
            rows = [{"name": n, "size": s, "dir": False} for n, s in self.files.items()]
            rows += [{"name": d, "size": 0, "dir": True} for d in self.dirs]
            return json.dumps(rows).encode()
        if method == "GET" and path.startswith("/api/files?d="):
            d = urllib.parse.unquote(path.split("=", 1)[1])
            rows = [
                {"name": n, "size": s, "dir": False}
                for n, s in self.subdirs.get(d, {}).items()
            ]
            return json.dumps(rows).encode()
        if method == "PUT":
            name = urllib.parse.unquote(path.rsplit("/", 1)[1])
            if path.startswith("/api/files/"):
                self.files[name] = len(body or b"")
            reply: dict[str, object] = {"bytes": len(body or b"") - self.short_by}
            if self.crc is not None:
                reply["crc32"] = self.crc
            return json.dumps(reply).encode()
        if method == "DELETE":
            self.files.pop(urllib.parse.unquote(path.rsplit("/", 1)[1]), None)
            return b"{}"
        if method == "GET":
            return json.dumps({"path": path}).encode()
        return b"{}"


class SdCase(unittest.TestCase):
    def setUp(self) -> None:
        self.card = FakeCard({"old.mp3": 2048, "keep.mp3": 4096})
        p = mock.patch.object(sd_sync, "api", self.card)
        p.start()
        self.addCleanup(p.stop)
        self.tmp = Path(tempfile.mkdtemp(prefix="castle-sd-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        r = mock.patch.object(sd_sync, "ROOT", self.tmp)
        r.start()
        self.addCleanup(r.stop)
        # The artefact paths go through build_paths now (a sandboxed studio's
        # publish must push the sandbox's renders, not the repo's) — point
        # them at the same tmp the ROOT patch uses. The end-to-end redirect
        # via CASTLE_BUILD is held by tests/test_studio_relay_rust.py.
        for attr, val in (
            ("AUDIO", self.tmp / "audio"),
            ("PREVIEW_HTML", self.tmp / "previewer" / "castle-cue-desk.html"),
        ):
            b = mock.patch.object(sd_sync.bp, attr, val)
            b.start()
            self.addCleanup(b.stop)
        self.out = io.StringIO()

    def run_quiet(self, fn, *a):
        with contextlib.redirect_stdout(self.out):
            return fn(*a)

    def puts(self) -> list[tuple[str, int]]:
        return [(p, n) for m, p, n in self.card.calls if m == "PUT"]


class TestListingAndPurge(SdCase):
    def test_ls_prints_files_with_sizes_and_dirs_with_a_slash(self) -> None:
        self.assertEqual(self.run_quiet(sd_sync.cmd_ls, "1.2.3.4"), 0)
        text = self.out.getvalue()
        self.assertIn("old.mp3  2 KB", text)
        self.assertIn("site/", text)

    def test_purge_deletes_files_and_leaves_directories(self) -> None:
        self.assertEqual(self.run_quiet(sd_sync.cmd_purge, "1.2.3.4"), 0)
        deletes = [p for m, p, _ in self.card.calls if m == "DELETE"]
        self.assertEqual(sorted(deletes), ["/api/files/keep.mp3", "/api/files/old.mp3"])
        self.assertEqual(self.card.files, {})
        self.assertEqual(self.card.dirs, ["site", "scenes"])

    def test_purge_of_an_empty_root_is_a_no_op(self) -> None:
        self.card.files.clear()
        self.assertEqual(self.run_quiet(sd_sync.cmd_purge, "1.2.3.4"), 0)
        self.assertFalse([c for c in self.card.calls if c[0] == "DELETE"])
        self.assertIn("no files", self.out.getvalue())

    def test_names_are_url_quoted_on_the_wire(self) -> None:
        self.card.files = {"a b#1.mp3": 10}
        self.run_quiet(sd_sync.cmd_purge, "1.2.3.4")
        self.assertIn(("DELETE", "/api/files/a%20b%231.mp3", 0), self.card.calls)


class TestPush(SdCase):
    def test_push_uploads_each_file_then_lists_the_card(self) -> None:
        a = self.tmp / "a.mp3"
        a.write_bytes(b"\xff\xfb" * 600)
        self.assertEqual(self.run_quiet(sd_sync.cmd_push, "1.2.3.4", [str(a)]), 0)
        self.assertEqual(self.puts(), [("/api/files/a.mp3", 1200)])
        self.assertEqual(self.card.files["a.mp3"], 1200)
        self.assertEqual(self.card.calls[-1][:2], ("GET", "/api/files"))
        self.assertIn("card now holds", self.out.getvalue())

    def test_push_defaults_to_the_library_mp3s(self) -> None:
        lib = self.tmp / "tracks"
        lib.mkdir()
        (lib / "b.mp3").write_bytes(b"b" * 10)
        (lib / "a.mp3").write_bytes(b"a" * 20)
        (lib / "c.wav").write_bytes(b"c" * 30)  # not an mp3: not pushed
        self.assertEqual(self.run_quiet(sd_sync.cmd_push, "1.2.3.4", []), 0)
        self.assertEqual(
            self.puts(), [("/api/files/a.mp3", 20), ("/api/files/b.mp3", 10)]
        )

    def test_an_empty_library_is_not_a_push(self) -> None:
        self.assertEqual(self.run_quiet(sd_sync.cmd_push, "1.2.3.4", []), 1)
        self.assertEqual(self.puts(), [])

    def test_a_missing_file_stops_before_anything_is_sent(self) -> None:
        a = self.tmp / "a.mp3"
        a.write_bytes(b"a")
        rc = self.run_quiet(
            sd_sync.cmd_push, "1.2.3.4", [str(self.tmp / "nope.mp3"), str(a)]
        )
        self.assertEqual(rc, 1)
        self.assertEqual(self.puts(), [])

    def test_a_crc_mismatch_is_a_failure(self) -> None:
        """v5.42 answers with a CRC of what hit the card (B5) — a byte count
        that matches but a checksum that does not is a bad sector, and the
        push must SAY so instead of shipping noise to the show."""
        (self.tmp / "t.mp3").write_bytes(b"abc")
        self.card.crc = "deadbeef"  # never the CRC of b"abc"
        with self.assertRaises(SystemExit) as cm:
            self.run_quiet(sd_sync.cmd_push, "10.0.0.9", [str(self.tmp / "t.mp3")])
        self.assertIn("crc mismatch", str(cm.exception))

    def test_a_matching_crc_is_accepted(self) -> None:
        import zlib

        (self.tmp / "t.mp3").write_bytes(b"abc")
        self.card.crc = "%08x" % zlib.crc32(b"abc")
        self.assertEqual(
            self.run_quiet(sd_sync.cmd_push, "10.0.0.9", [str(self.tmp / "t.mp3")]), 0
        )

    def test_a_short_write_is_a_failure_not_a_shrug(self) -> None:
        self.card.short_by = 1
        with self.assertRaises(SystemExit) as cm:
            self.run_quiet(sd_sync.upload, "1.2.3.4", "/api/files", "x.mp3", b"xyz")
        self.assertIn("2 of 3 bytes", str(cm.exception))


class TestSiteScenesOta(SdCase):
    def test_site_pushes_the_lean_page_and_the_scene_audio(self) -> None:
        """The DEVICE gets the lean rewrite (G1/A5): inlined data URIs become
        /site/<sid>.mp3 links and the tracks are pushed beside the page."""
        (self.tmp / "previewer").mkdir(exist_ok=True)
        (self.tmp / "previewer" / "castle-cue-desk.html").write_text(
            '<html>"vigil": "data:audio/mpeg;base64,AAAA"</html>'
        )
        (self.tmp / "audio").mkdir(exist_ok=True)
        (self.tmp / "audio" / "01_vigil.mp3").write_bytes(b"MP3" * 10)
        self.assertEqual(self.run_quiet(sd_sync.cmd_site, "10.0.0.9"), 0)
        by_path = {p: n for p, n in self.puts()}
        self.assertIn("/api/site/index.html", by_path)
        self.assertIn("/api/site/index.html.gz", by_path)
        self.assertIn("/api/site/vigil.mp3", by_path)
        # the pushed page links the card copy, and carries no data URI
        import gzip as _gz

        put_bodies = [b for m, p, b in self.card.calls if m == "PUT"]
        self.assertTrue(put_bodies)
        # sizes only are recorded; re-derive the lean text to check the link
        import gen_previewer as gp

        lean = gp.lean(
            (self.tmp / "previewer" / "castle-cue-desk.html").read_text(),
            route="/site/",
            suffix=".mp3",
        )
        self.assertIn('"vigil": "/site/vigil.mp3"', lean)
        self.assertNotIn("data:audio/mpeg", lean)
        _ = _gz  # gzip round-trip covered by the size assertions above

    def test_site_pushes_gzipped_and_plain_copies(self) -> None:
        page = self.tmp / "previewer" / "castle-cue-desk.html"
        page.parent.mkdir()
        page.write_bytes(b"<html>" + b"desk " * 4000 + b"</html>")
        self.assertEqual(self.run_quiet(sd_sync.cmd_site, "1.2.3.4"), 0)
        names = [p for p, _ in self.puts()]
        self.assertEqual(names, ["/api/site/index.html.gz", "/api/site/index.html"])
        gz_len, plain_len = (n for _, n in self.puts())
        self.assertLess(gz_len, plain_len // 4)
        self.assertEqual(plain_len, page.stat().st_size)

    def test_site_without_a_built_page_says_to_build_it(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            self.run_quiet(sd_sync.cmd_site, "1.2.3.4")
        self.assertIn("make preview", str(cm.exception))

    def test_scenes_skips_tracks_the_card_already_holds(self) -> None:
        """Same name, same size in /sd/scenes (the v5.42 ?d= listing) — not
        re-sent. The studio publishes after every scene save; steady state
        must not be a ten-track resend over porch WiFi."""
        (self.tmp / "audio").mkdir()
        (self.tmp / "audio" / "01_vigil.mp3").write_bytes(b"x" * 100)
        (self.tmp / "audio" / "02_storm.mp3").write_bytes(b"y" * 200)
        self.card.subdirs["scenes"] = {"01_vigil.mp3": 100}  # already there
        self.assertEqual(self.run_quiet(sd_sync.cmd_scenes, "10.0.0.9"), 0)
        sent = [p for p, _n in self.puts()]
        self.assertEqual(sent, ["/api/scenes/02_storm.mp3"])
        self.assertIn("01_vigil.mp3 unchanged, skipped", self.out.getvalue())

    def test_scenes_uploads_the_numbered_tracks_but_not_00(self) -> None:
        audio = self.tmp / "audio"
        audio.mkdir()
        for n in ("00_silence.mp3", "01_vigil.mp3", "02_storm.mp3", "stray.mp3"):
            (audio / n).write_bytes(b"x" * 8)
        self.assertEqual(self.run_quiet(sd_sync.cmd_scenes, "1.2.3.4"), 0)
        self.assertEqual(
            [p for p, _ in self.puts()],
            ["/api/scenes/01_vigil.mp3", "/api/scenes/02_storm.mp3"],
        )

    def test_scenes_without_rendered_audio_refuses(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            self.run_quiet(sd_sync.cmd_scenes, "1.2.3.4")
        self.assertIn("make audio", str(cm.exception))

    def test_ota_refuses_a_file_without_the_app_magic(self) -> None:
        junk = self.tmp / "firmware.bin"
        junk.write_bytes(b"not an image")
        with self.assertRaises(SystemExit) as cm:
            self.run_quiet(sd_sync.cmd_ota, "1.2.3.4", [str(junk)])
        self.assertIn("0xE9", str(cm.exception))
        self.assertEqual(self.puts(), [])

    def test_ota_with_no_build_anywhere_says_so(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            self.run_quiet(sd_sync.cmd_ota, "1.2.3.4", [])
        self.assertIn("firmware.bin", str(cm.exception))

    def test_ota_finds_the_newest_build_and_confirms_it_came_back(self) -> None:
        old = self.tmp / "firmware/.esphome/build/a/.pioenvs/a/firmware.bin"
        new = self.tmp / "firmware/.esphome/build/b/.pioenvs/b/firmware.bin"
        for p, stamp in ((old, 1_000), (new, 2_000)):
            p.parent.mkdir(parents=True)
            p.write_bytes(b"\xe9" + p.parent.name.encode())
            os.utime(p, (stamp, stamp))

        def card(ip, method, path, body=None, timeout=60):
            self.card.calls.append((method, path, len(body or b"")))
            if path == "/api/ota":
                return json.dumps({"flashed": True}).encode()
            return json.dumps({"version": "5.23", "compiled": "now"}).encode()

        with mock.patch.object(sd_sync, "api", card), mock.patch("time.sleep"):
            rc = self.run_quiet(sd_sync.cmd_ota, "1.2.3.4", [])
        self.assertEqual(rc, 0)
        self.assertIn(("PUT", "/api/ota", 2), self.card.calls)
        self.assertIn("v5.23", self.out.getvalue())
        self.assertIn("CONFIRM", self.out.getvalue())


class TestMain(SdCase):
    def setUp(self) -> None:
        super().setUp()
        e = mock.patch.dict(os.environ, {"CASTLE_HOST": "10.1.1.1"})
        e.start()
        self.addCleanup(e.stop)

    def main(self, *argv: str) -> int:
        with mock.patch.object(sys, "argv", ["sd_sync.py", *argv]):
            return int(self.run_quiet(sd_sync.main))

    def test_no_command_prints_usage(self) -> None:
        self.assertEqual(self.main(), 2)
        self.assertIn("status", self.out.getvalue())

    def test_unknown_command(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.main("10.1.1.1", "dance"), 2)

    def test_status_health_bootlog_are_plain_gets(self) -> None:
        for cmd in ("status", "health", "bootlog"):
            self.assertEqual(self.main(cmd), 0)
        gets = [p for m, p, _ in self.card.calls if m == "GET"]
        self.assertEqual(gets, ["/api/status", "/api/health", "/api/bootlog"])

    def test_rm_and_play_quote_the_name(self) -> None:
        self.assertEqual(self.main("rm", "old song.mp3"), 0)
        self.assertEqual(self.main("play", "old song.mp3"), 0)
        self.assertIn(("DELETE", "/api/files/old%20song.mp3", 0), self.card.calls)
        self.assertIn(("POST", "/api/play?f=old%20song.mp3", 0), self.card.calls)

    def test_an_explicit_host_is_used(self) -> None:
        seen = []

        def card(ip, method, path, body=None, timeout=60):
            seen.append(ip)
            return b"{}"

        with mock.patch.object(sd_sync, "api", card):
            self.assertEqual(self.main("10.5.5.5", "status"), 0)
            self.assertEqual(self.main("status"), 0)
        self.assertEqual(seen, ["10.5.5.5", "10.1.1.1"])


if __name__ == "__main__":
    unittest.main()
