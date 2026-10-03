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

import exe_paths
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


def installed(home: Path) -> bytes:
    return (home / "yt-dlp").read_bytes()


def parts_left(home: Path) -> list[str]:
    """Half-fetched builds an update left beside the copy."""
    return [p.name for p in home.glob("*.download*")] if home.is_dir() else []


class FakeGitHub:
    """The three answers an update reads, served from a dict; `hits` counts
    each path, so a test can tell an update that downloaded from one that
    did not."""

    def __init__(self) -> None:
        self.pages: dict[str, bytes] = {}
        self.hits: dict[str, int] = {}
        #: A path's refusal (the API's 403), and the website's redirects.
        self.status: dict[str, int] = {}
        self.moved: dict[str, str] = {}
        pages, hits, status, moved = self.pages, self.hits, self.status, self.moved

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                key = self.path if self.command == "GET" else f"HEAD {self.path}"
                hits[key] = hits.get(key, 0) + 1
                if self.path in moved:
                    self.send_response(302)
                    self.send_header("Location", moved[self.path])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = pages.get(self.path)
                code = status.get(self.path, 200 if body is not None else 404)
                body = body if code == 200 else b"rate limit exceeded"
                self.send_response(code)
                self.send_header("Content-Length", str(len(body or b"")))
                self.end_headers()
                if self.command == "GET":
                    self.wfile.write(body or b"")

            do_HEAD = do_GET

            def log_message(self, *_: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        serve = lambda: self.server.serve_forever(poll_interval=0.02)  # noqa: E731
        threading.Thread(target=serve, daemon=True).start()

    def publish(
        self,
        tag: str,
        body: bytes,
        sums: bytes | None = None,
        with_sums: bool = True,
        asset: str = ASSET,
    ) -> None:
        digest = hashlib.sha256(body).hexdigest()
        assets = [{"name": asset, "browser_download_url": f"{self.base}/dl/{asset}"}]
        if with_sums:
            assets.append(
                {"name": yu.SUMS, "browser_download_url": f"{self.base}/dl/sums"}
            )
        release = {"tag_name": tag, "draft": False, "prerelease": False}
        self.pages["/latest"] = json.dumps({**release, "assets": assets}).encode()
        self.pages[f"/dl/{asset}"] = body
        self.pages["/dl/sums"] = sums or f"{digest}  {asset}\n".encode()

    def on_the_website(self, tag: str, body: bytes, asset: str = ASSET) -> str:
        """The same release as github.com serves it: /releases/latest
        redirects to its tag, the files sit under download/<tag>/. Returns
        the website's base URL."""
        web = f"{self.base}/web"
        self.moved["/web/latest"] = f"{web}/tag/{tag}"
        self.pages[f"/web/tag/{tag}"] = b"<html>release page</html>"
        self.pages[f"/web/download/{tag}/{asset}"] = body
        digest = hashlib.sha256(body).hexdigest()
        self.pages[f"/web/download/{tag}/{yu.SUMS}"] = f"{digest}  {asset}\n".encode()
        return web

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


class TestUpdate(UpdateCase):
    def test_a_first_update_installs_the_verified_build(self) -> None:
        self.github.publish(TAG, build(TAG))
        got = self.update()
        self.assertEqual(
            got, {"changed": True, "version": TAG, "path": str(self.home / "yt-dlp")}
        )
        self.assertEqual(installed(self.home), build(TAG))
        record = yu.read_record(self.home)
        self.assertEqual((record["tag"], record["asset"]), (TAG, ASSET))
        self.assertEqual(record["sha256"], hashlib.sha256(build(TAG)).hexdigest())
        if os.name != "nt":
            self.assertTrue(os.access(self.home / "yt-dlp", os.X_OK))
        self.assertEqual(parts_left(self.home), [])

    def test_an_up_to_date_copy_is_left_alone_unless_forced(self) -> None:
        self.github.publish(TAG, build(TAG))
        self.update()
        self.assertIs(self.update()["changed"], False)
        self.assertEqual(self.github.hits.get(f"/dl/{ASSET}"), 1)
        self.assertIs(self.update(force=True)["changed"], True)
        self.assertEqual(self.github.hits.get(f"/dl/{ASSET}"), 2)

    def test_a_newer_release_replaces_the_old_copy(self) -> None:
        self.github.publish("2026.09.01", build("2026.09.01"))
        self.update()
        self.github.publish(TAG, build(TAG))
        self.assertEqual(self.update()["version"], TAG)
        self.assertEqual(installed(self.home), build(TAG))

    def test_bytes_that_do_not_match_the_sums_are_never_installed(self) -> None:
        self.github.publish("2026.09.01", build("2026.09.01"))
        self.update()
        wrong = f"{'0' * 64}  {ASSET}\n".encode()
        self.github.publish(TAG, build(TAG), sums=wrong)
        err = self.failed()
        self.assertEqual(str(err), yu.TAMPERED)
        self.assertIn("checksum mismatch", err.detail)
        self.assertEqual(installed(self.home), build("2026.09.01"))
        self.assertEqual(parts_left(self.home), [])

    def test_a_build_that_will_not_start_is_not_installed(self) -> None:
        self.github.publish(TAG, b"not a program at all")
        self.assertEqual(str(self.failed()), yu.BROKEN)
        self.assertFalse((self.home / "yt-dlp").exists())
        self.assertEqual(parts_left(self.home), [])

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
        self.assertEqual(installed(self.home), build(TAG))

    def test_a_busy_copy_is_kept_and_named_as_busy(self) -> None:
        self.github.publish("2026.09.01", build("2026.09.01"))
        self.update()
        self.github.publish(TAG, build(TAG))
        locked = PermissionError(13, "The process cannot access the file")
        with mock.patch.object(portable_fs, "replace", side_effect=locked):
            self.assertEqual(str(self.failed()), yu.IN_USE)
        self.assertEqual(installed(self.home), build("2026.09.01"))
        self.assertEqual(parts_left(self.home), [])

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


#: This computer as ytdlp_update.update sees it, so the copy it installs
#: has the name exe_paths looks for (`yt-dlp.exe` on Windows).
HOST = (
    ("Windows", "AMD64", "yt-dlp.exe")
    if exe_paths.WINDOWS
    else ("Darwin", "arm64", ASSET)
)


def same_file(a: object, b: Path) -> bool:
    """Windows' shutil.which spells the extension as PATHEXT does (.EXE)."""
    return isinstance(a, str) and os.path.normcase(a) == os.path.normcase(str(b))


class TestTurnedAway(UpdateCase):
    """The Releases API allows 60 calls an hour to an address, and everyone
    behind one router shares it — an office, a school, a CI runner pool
    (tests/install_smoke.py met it on macos-14). The website is outside
    that limit and names the same release."""

    def setUp(self) -> None:
        super().setUp()
        self.github.publish(TAG, build(TAG))
        self.github.status["/latest"] = 403
        web = self.github.on_the_website(TAG, build(TAG))
        self.enterContext(mock.patch.dict(os.environ, {"CASTLE_DOWNLOADER_WEB": web}))

    def test_the_website_names_the_release_and_it_is_verified(self) -> None:
        got = self.update()
        self.assertEqual((got["changed"], got["version"]), (True, TAG))
        self.assertEqual(installed(self.home), build(TAG))
        self.assertEqual(self.github.hits[f"/web/download/{TAG}/{ASSET}"], 1)
        self.assertEqual(self.github.hits[f"HEAD /web/tag/{TAG}"], 1)
        self.assertNotIn(f"/web/tag/{TAG}", self.github.hits)  # never the page
        self.assertEqual(yu.read_record(self.home)["tag"], TAG)
        self.assertIs(self.update()["changed"], False)

    def test_the_websites_bytes_still_answer_to_its_sums(self) -> None:
        self.github.pages[f"/web/download/{TAG}/{ASSET}"] = build("tampered")
        self.assertEqual(str(self.failed()), yu.TAMPERED)
        self.assertFalse((self.home / "yt-dlp").exists())

    def test_turned_away_by_both_says_so_not_offline(self) -> None:
        self.github.moved.clear()
        self.github.status["/web/latest"] = 429
        err = self.failed()
        self.assertEqual(str(err), yu.TURNED_AWAY_SAID)
        self.assertIn("403", err.detail)
        self.assertIn("429", err.detail)

    def test_a_landing_that_is_not_a_release_is_unreadable(self) -> None:
        self.github.moved["/web/latest"] = f"{self.github.base}/web/tag/"
        self.github.pages["/web/tag/"] = b"<html>all releases</html>"
        self.assertEqual(str(self.failed()), yu.UNREADABLE)
        self.github.moved["/web/latest"] = f"{self.github.base}/web/gone"
        self.assertEqual(str(self.failed()), yu.UNREADABLE)

    def test_other_refusals_and_a_mirror_with_no_website_do_not_go_round(
        self,
    ) -> None:
        self.github.status["/latest"] = 500
        self.assertEqual(str(self.failed()), yu.OFFLINE)
        self.github.status["/latest"] = 403
        with mock.patch.dict(os.environ, {"CASTLE_DOWNLOADER_WEB": ""}):
            self.assertIsNone(yu.web_url())
            self.assertEqual(str(self.failed()), yu.OFFLINE)
        self.assertNotIn("HEAD /web/latest", self.github.hits)
        with mock.patch.dict(
            os.environ, {"CASTLE_DOWNLOADER_WEB": "", "CASTLE_DOWNLOADER_RELEASES": ""}
        ):
            self.assertEqual(yu.web_url(), yu.WEB_RELEASES)

    def test_the_installer_ends_on_the_sentence_not_a_traceback(self) -> None:
        # desktop_install.main reports a ReleaseError as "install failed — …".
        self.assertTrue(issubclass(yu.UpdateError, yu.rel.ReleaseError))


class TestStatus(UpdateCase):
    """Every place exe_paths.ytdlp() looks is this test's own — the managed
    folder, CASTLE_YTDLP, the interpreter's folder and PATH — so a yt-dlp
    the machine happens to have (a CI runner's, a developer's) never
    answers for the copy under test."""

    def setUp(self) -> None:
        super().setUp()
        self.path = self.tmp / "path"
        self.path.mkdir()
        # Windows' which also searches the working directory unless told not to.
        env = {"PATH": str(self.path), "NoDefaultCurrentDirectoryInExePath": "1"}
        self.enterContext(mock.patch.dict(os.environ, env))
        python = self.tmp / "python" / exe_paths.exe("python")
        self.enterContext(mock.patch.object(exe_paths.sys, "executable", str(python)))

    def test_status_names_the_copy_an_import_would_run(self) -> None:
        none = yu.status(run=fake_version)
        self.assertEqual(
            (none["installed"], none["path"], none["version"]), (False, None, None)
        )
        # A copy on PATH runs, but it is not the one Update replaces.
        system_copy = self.path / exe_paths.exe("yt-dlp")
        system_copy.write_bytes(build("2026.09.01"))
        system_copy.chmod(0o755)
        mine = yu.status(run=fake_version)
        self.assertIs(mine["installed"], True)
        self.assertIs(mine["managed"], False)
        self.assertTrue(same_file(mine["path"], system_copy), mine["path"])
        self.assertEqual(mine["version"], "2026.09.01")
        self.assertIsNone(mine["updated_at"])
        # Once fetched, the managed copy is what an import runs.
        system, machine, asset = HOST
        self.github.publish(TAG, build(TAG), asset=asset)
        yu.update(self.home, run=fake_version, system=system, machine=machine)
        now = yu.status(run=fake_version)
        self.assertEqual(
            (now["installed"], now["managed"], now["version"]), (True, True, TAG)
        )
        self.assertEqual(now["path"], str(self.home / exe_paths.exe("yt-dlp")))
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
        for name in ("OFFLINE", "UNREADABLE", "TURNED_AWAY_SAID", "TAMPERED", "BROKEN",
                     "NO_BUILD"):  # fmt: skip
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
