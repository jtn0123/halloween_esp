"""tools/ytdlp_update.py: Update the downloader, against a fake GitHub.

A real HTTP server on port 0 plays GitHub — the Releases-API answer, the
release's SHA2-256SUMS and the build — so the production fetch (urllib) is
what runs, and nothing reaches the network. The "build" is a few bytes that
name their own version, and the trial run (`--version`) is injected: the
same test runs on Windows, where a script is not an .exe.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import helpers  # noqa: F401  (the sandbox scrub)
import import_reason as ir
import portable_fs
import ytdlp_update as yu

TAG = "2026.10.01"
ASSET = "yt-dlp_macos"


def build(version: str) -> bytes:
    return f"#!fake yt-dlp\n{version}\n".encode()


def fake_version(program: str) -> str:
    """The trial run: a fake build's second line is its version."""
    lines = Path(program).read_bytes().decode().splitlines()
    if len(lines) != 2 or not lines[0].startswith("#!fake"):
        raise OSError(8, "Exec format error")
    return lines[1]


class FakeGitHub:
    """The three answers an update reads, served from a dict; `hits` counts
    each path, so a test can tell an update that downloaded from one that
    did not."""

    def __init__(self) -> None:
        self.pages: dict[str, bytes] = {}
        self.hits: dict[str, int] = {}
        pages, hits = self.pages, self.hits

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                hits[self.path] = hits.get(self.path, 0) + 1
                body = pages.get(self.path)
                self.send_response(200 if body is not None else 404)
                self.send_header("Content-Length", str(len(body or b"")))
                self.end_headers()
                self.wfile.write(body or b"")

            def log_message(self, *_: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        serve = lambda: self.server.serve_forever(poll_interval=0.02)  # noqa: E731
        threading.Thread(target=serve, daemon=True).start()

    def publish(
        self, tag: str, body: bytes, sums: bytes | None = None, with_sums: bool = True
    ) -> None:
        digest = hashlib.sha256(body).hexdigest()
        assets = [{"name": ASSET, "browser_download_url": f"{self.base}/dl/{ASSET}"}]
        if with_sums:
            assets.append(
                {"name": yu.SUMS, "browser_download_url": f"{self.base}/dl/sums"}
            )
        release = {"tag_name": tag, "draft": False, "prerelease": False}
        self.pages["/latest"] = json.dumps({**release, "assets": assets}).encode()
        self.pages[f"/dl/{ASSET}"] = body
        self.pages["/dl/sums"] = sums or f"{digest}  {ASSET}\n".encode()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class UpdateCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "downloader"
        self.github = FakeGitHub()
        self.addCleanup(self.github.stop)
        env = {
            "CASTLE_DOWNLOADER_RELEASES": self.github.base + "/latest",
            "CASTLE_DOWNLOADER_DIR": str(self.home),
            "CASTLE_YTDLP": "",
        }
        self.enterContext(mock.patch.dict(os.environ, env))

    def update(self, force: bool = False) -> dict[str, object]:
        return yu.update(
            self.home, run=fake_version, system="Darwin", machine="arm64", force=force
        )

    def failed(self, force: bool = False) -> yu.UpdateError:
        with self.assertRaises(yu.UpdateError) as cm:
            self.update(force)
        return cm.exception

    def installed(self) -> bytes:
        return (self.home / "yt-dlp").read_bytes()

    def no_part_left(self) -> None:
        left = (
            [p.name for p in self.home.glob("*.download*")]
            if self.home.is_dir()
            else []
        )
        self.assertEqual(left, [])


class TestUpdate(UpdateCase):
    def test_a_first_update_installs_the_verified_build(self) -> None:
        self.github.publish(TAG, build(TAG))
        got = self.update()
        self.assertEqual(
            got, {"changed": True, "version": TAG, "path": str(self.home / "yt-dlp")}
        )
        self.assertEqual(self.installed(), build(TAG))
        record = yu.read_record(self.home)
        self.assertEqual((record["tag"], record["asset"]), (TAG, ASSET))
        self.assertEqual(record["sha256"], hashlib.sha256(build(TAG)).hexdigest())
        if os.name != "nt":
            self.assertTrue(os.access(self.home / "yt-dlp", os.X_OK))
        self.no_part_left()

    def test_an_up_to_date_copy_is_left_alone_unless_forced(self) -> None:
        self.github.publish(TAG, build(TAG))
        self.update()
        self.assertEqual(self.update()["changed"], False)
        self.assertEqual(self.github.hits.get(f"/dl/{ASSET}"), 1)
        self.assertEqual(self.update(force=True)["changed"], True)
        self.assertEqual(self.github.hits.get(f"/dl/{ASSET}"), 2)

    def test_a_newer_release_replaces_the_old_copy(self) -> None:
        self.github.publish("2026.09.01", build("2026.09.01"))
        self.update()
        self.github.publish(TAG, build(TAG))
        self.assertEqual(self.update()["version"], TAG)
        self.assertEqual(self.installed(), build(TAG))

    def test_bytes_that_do_not_match_the_sums_are_never_installed(self) -> None:
        self.github.publish("2026.09.01", build("2026.09.01"))
        self.update()
        wrong = f"{'0' * 64}  {ASSET}\n".encode()
        self.github.publish(TAG, build(TAG), sums=wrong)
        err = self.failed()
        self.assertEqual(str(err), yu.TAMPERED)
        self.assertIn("checksum mismatch", err.detail)
        self.assertEqual(self.installed(), build("2026.09.01"))
        self.no_part_left()

    def test_a_build_that_will_not_start_is_not_installed(self) -> None:
        self.github.publish(TAG, b"not a program at all")
        self.assertEqual(str(self.failed()), yu.BROKEN)
        self.assertFalse((self.home / "yt-dlp").exists())
        self.no_part_left()

    def test_a_release_without_sums_is_refused(self) -> None:
        self.github.publish(TAG, build(TAG), with_sums=False)
        err = self.failed()
        self.assertEqual(str(err), yu.UNREADABLE)
        self.assertIn(yu.SUMS, err.detail)

    def test_an_answer_that_is_not_a_release(self) -> None:
        self.github.pages["/latest"] = b"<html>rate limited</html>"
        self.assertEqual(str(self.failed()), yu.UNREADABLE)

    def test_offline_says_so_and_keeps_the_old_copy(self) -> None:
        self.github.publish(TAG, build(TAG))
        self.update()
        self.github.stop()  # its port now refuses the connection
        err = self.failed(force=True)
        self.assertEqual(str(err), yu.OFFLINE)
        self.assertEqual(self.installed(), build(TAG))

    def test_a_busy_copy_is_kept_and_named_as_busy(self) -> None:
        self.github.publish("2026.09.01", build("2026.09.01"))
        self.update()
        self.github.publish(TAG, build(TAG))
        locked = PermissionError(13, "The process cannot access the file")
        with mock.patch.object(portable_fs, "replace", side_effect=locked):
            self.assertEqual(str(self.failed()), yu.IN_USE)
        self.assertEqual(self.installed(), build("2026.09.01"))
        self.no_part_left()

    def test_a_full_disk_says_so(self) -> None:
        self.github.publish(TAG, build(TAG))
        full = OSError(28, "No space left on device")
        with mock.patch.object(Path, "write_bytes", side_effect=full):
            self.assertEqual(str(self.failed()), ir.DISK_FULL)

    def test_a_computer_with_no_build(self) -> None:
        with self.assertRaises(yu.UpdateError) as cm:
            yu.update(self.home, run=fake_version, system="FreeBSD", machine="x86_64")
        self.assertEqual(str(cm.exception), yu.NO_BUILD)
        self.assertEqual(self.github.hits, {})


class TestStatus(UpdateCase):
    def test_status_names_the_copy_an_import_would_run(self) -> None:
        self.assertEqual(yu.status(run=fake_version)["managed"], False)
        self.github.publish(TAG, build(TAG))
        self.update()
        now = yu.status(run=fake_version)
        self.assertEqual(
            (now["installed"], now["managed"], now["version"]), (True, True, TAG)
        )
        self.assertEqual(now["path"], str(self.home / "yt-dlp"))
        self.assertIsInstance(now["updated_at"], int)

    def test_the_command_line(self) -> None:
        self.github.publish(TAG, build(TAG))
        out = io.StringIO()
        with (
            mock.patch.object(yu, "run_version", fake_version),
            mock.patch.object(yu, "asset_name", return_value=ASSET),
            mock.patch.object(yu.platform, "system", return_value="Darwin"),
            contextlib.redirect_stdout(out),
        ):
            self.assertEqual(yu.main(["update"]), 0)
        self.assertEqual(json.loads(out.getvalue())["version"], TAG)
        err = io.StringIO()
        self.github.stop()
        with (
            mock.patch.object(yu, "run_version", fake_version),
            contextlib.redirect_stderr(err),
        ):
            self.assertEqual(yu.main(["update", "--force"]), 1)
        self.assertIn(yu.OFFLINE, err.getvalue())
        with mock.patch.dict(os.environ, {"CASTLE_DOWNLOADER_DIR": ""}):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(yu.main(["update"]), 1)


class TestWords(unittest.TestCase):
    def test_every_sentence_says_what_happened_then_what_to_do(self) -> None:
        for name in ("OFFLINE", "UNREADABLE", "TAMPERED", "BROKEN", "NO_BUILD"):
            text = getattr(yu, name)
            with self.subTest(name=name):
                self.assertEqual(text.count(" — "), 1, text)
                self.assertTrue(text.endswith("."), text)
        for name in ("NO_HOME", "IN_USE"):
            self.assertEqual(getattr(yu, name).count(" — "), 1)

    def test_each_computer_gets_its_own_build(self) -> None:
        for system, machine, want in (
            ("Windows", "AMD64", "yt-dlp.exe"),
            ("Windows", "ARM64", "yt-dlp.exe"),
            ("Darwin", "arm64", "yt-dlp_macos"),
            ("Darwin", "x86_64", "yt-dlp_macos"),
            ("Linux", "x86_64", "yt-dlp_linux"),
            ("Linux", "aarch64", "yt-dlp_linux_aarch64"),
            ("Linux", "riscv64", None),
        ):
            with self.subTest(system=system, machine=machine):
                self.assertEqual(yu.asset_name(system, machine), want)


if __name__ == "__main__":
    unittest.main()
