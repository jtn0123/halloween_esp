"""tools/desktop_install.py as the desktop app runs it on its first launch.

The app runs the installer like this (desktop/src-tauri/src/setup_cmd.rs):

    --core-from <bundle>/bin   castle-core is the copy the app carries
    --ffmpeg download          the pinned build, whatever is on PATH
    --no-launcher              the app is the launcher
    --progress                 the @castle-step / @castle-failed lines

Each of those is tested here the way tests/test_desktop_install.py tests
the rest: temp directories, canned `which` and `fetch`, nothing run.
tests/test_desktop_bundle.py holds the flags setup_cmd.rs passes to this
parser.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import desktop_env as de
import desktop_install as di
import desktop_progress as progress
import desktop_release as rel
import exe_paths
import ytdlp_update as yu
from test_desktop_release import api_body, fake_fetch, sha

APP_FLAGS = ("--ffmpeg", "download", "--no-launcher", "--progress")


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.dirs = de.Dirs(self.tmp / "rt", self.tmp / "data", "Darwin")
        self.carried = self.tmp / "bundle" / "bin"
        self.carried.mkdir(parents=True)
        for name in rel.CORE_BINS:
            (self.carried / name).write_bytes(f"#!{name}\n".encode())

    def args(self, *argv: str) -> argparse.Namespace:
        return di.build_parser().parse_args(
            ["--uv", "uv", "--core-from", str(self.carried), *APP_FLAGS, *argv]
        )

    def installer(
        self, *argv: str, which: dict[str, str] | None = None
    ) -> tuple[di.Installer, list[str]]:
        said: list[str] = []
        inst = di.Installer(
            self.args(*argv),
            self.dirs,
            fetch=fake_fetch({}),
            which=(which or {}).get,
            machine="arm64",
            say=said.append,
        )
        return inst, said


class FirstLaunch(Case):
    def test_every_step_is_announced_in_order_and_nothing_is_asked_of_github(
        self,
    ) -> None:
        inst, said = self.installer(
            "--dry-run", "--source", str(ROOT), which={"cargo": "/c", "git": "/g"}
        )
        self.assertEqual(inst.install(), 0)
        marks = [s for s in said if s.startswith(progress.STEP_MARK)]
        self.assertEqual(
            marks, [progress.step_line(n) for n in range(1, len(progress.STEPS) + 1)]
        )
        plan = "\n".join(said)
        self.assertIn(f"would copy castle-core from {self.carried}", plan)
        self.assertIn("launcher: none (--no-launcher)", plan)
        self.assertIn("download the pinned static ffmpeg", plan)
        for never in ("cargo build", "castle-core-aarch64", "Castle Tools.command"):
            with self.subTest(never=never):
                self.assertNotIn(never, plan)

    def test_the_carried_castle_core_is_placed_where_core_bins_looks(self) -> None:
        inst, _ = self.installer()
        self.assertEqual(inst.core_bins(ROOT), "bundled")
        placed = self.dirs.app / "core" / "target" / "release"
        for name in rel.CORE_BINS:
            with self.subTest(program=name):
                self.assertEqual((placed / name).read_bytes(), f"#!{name}\n".encode())
                if os.name != "nt":  # Windows has no executable bit to set
                    self.assertTrue(os.access(placed / name, os.X_OK))
        (self.carried / "scene_render").unlink()
        with self.assertRaisesRegex(rel.ReleaseError, "no scene_render"):
            inst.core_bins(ROOT)

    def test_the_record_says_bundled_which_install_tree_rs_trusts(self) -> None:
        inst, _ = self.installer()
        inst.write_record("bundled")
        record = de.read_json(self.dirs.install_file)
        self.assertEqual(record["core"], "bundled")
        self.assertEqual(record["python"], str(self.dirs.python))
        rust = ROOT / "desktop" / "src-tauri" / "src" / "install_tree.rs"
        self.assertIn('how != "bundled"', rust.read_text(encoding="utf-8"))


class Ffmpeg(Case):
    def test_download_ignores_path_and_keeps_its_own_copy(self) -> None:
        on_path = {"ffmpeg": "/usr/bin/ffmpeg", "ffprobe": "/usr/bin/ffprobe"}
        inst, said = self.installer("--dry-run", which=on_path)
        inst.ffmpeg()
        self.assertTrue(any("pinned static ffmpeg" in s for s in said), said)
        self.dirs.bin.mkdir(parents=True)
        for name in ("ffmpeg", "ffprobe"):
            (self.dirs.bin / name).write_bytes(b"")
        kept, said = self.installer(which=on_path)
        kept.ffmpeg()
        self.assertEqual(kept.found["ffmpeg"], str(self.dirs.bin / "ffmpeg"))
        self.assertEqual(said, [f"ffmpeg: {self.dirs.bin / 'ffmpeg'}"])

    def test_a_repair_fetches_it_again(self) -> None:
        self.dirs.bin.mkdir(parents=True)
        for name in ("ffmpeg", "ffprobe"):
            (self.dirs.bin / name).write_bytes(b"")
        inst, said = self.installer("--dry-run", "--repair")
        inst.ffmpeg()
        self.assertTrue(any("pinned static ffmpeg" in s for s in said), said)


class Downloader(Case):
    """yt-dlp is optional (desktop_steps.no_ytdlp): a first launch that could
    not fetch it finishes, and Castle Radio's Update the downloader button —
    ytdlp_update.update into CASTLE_DOWNLOADER_DIR — fetches it later."""

    HEAVY = ("stage_app", "python_env", "ffmpeg", "model", "data", "status")

    def test_a_failed_fetch_finishes_the_setup_without_it(self) -> None:
        inst, said = self.installer()  # every URL is "no route" (OSError)
        with contextlib.ExitStack() as stack:
            for name in self.HEAVY:  # uv, torch and the network: not here
                stack.enter_context(mock.patch.object(inst, name))
            self.assertEqual(inst.install(), 0)
        marks = [s for s in said if s.startswith(progress.STEP_MARK)]
        self.assertEqual(len(marks), len(progress.STEPS))
        self.assertFalse(any(s.startswith(progress.FAILED_MARK) for s in said))
        note = next(s for s in said if s.startswith("yt-dlp: not downloaded"))
        self.assertIn(yu.OFFLINE, note)  # the owner's sentence, not a traceback
        self.assertIn("no route to", note)  # and the detail, for whoever helps
        self.assertIn("Update the downloader in Castle Radio", note)
        self.assertEqual(said[-1], "Castle Tools are installed.")
        record = de.read_json(self.dirs.install_file)
        self.assertEqual(record["core"], "bundled")
        self.assertNotIn("ytdlp", record)
        self.assertNotIn("CASTLE_YTDLP", de.launch_env(self.dirs, record, {}, {}))

    def test_a_failed_refetch_keeps_the_copy_already_there(self) -> None:
        mine = self.dirs.bin / "yt-dlp"
        mine.parent.mkdir(parents=True)
        mine.write_bytes(b"#!old")
        inst, said = self.installer("--repair")
        inst.ytdlp()
        self.assertEqual(inst.found["ytdlp"], str(mine))
        self.assertEqual(mine.read_bytes(), b"#!old")
        self.assertTrue(said[-1].endswith("kept the copy already there"), said)

    def test_the_update_button_then_installs_it_where_the_app_looks(self) -> None:
        inst, _ = self.installer()
        inst.ytdlp()  # offline: nothing fetched, nothing recorded
        self.assertNotIn("ytdlp", inst.found)
        nothing = self.tmp / "empty-path"
        nothing.mkdir()
        record = de.install_record(self.dirs, core="bundled")
        env = de.launch_env(self.dirs, record, {}, {"PATH": str(nothing)})
        # This runner's own file name for it, so status() finds what update() wrote.
        system = "Windows" if os.name == "nt" else "Darwin"
        asset = yu.asset_name(system, "arm64")
        assert asset is not None
        new = b"#!yt-dlp"
        fetch = fake_fetch(
            {
                yu.API_LATEST: api_body(
                    "2026.10.01", {asset: "dl/bin", yu.SUMS: "dl/sums"}
                ),
                "dl/bin": new,
                "dl/sums": f"{sha(new)}  {asset}\n".encode(),
            }
        )

        def version(_program: str) -> str:
            return "2026.10.01"

        here = str(self.tmp / "venv" / "bin" / "python")  # no yt-dlp beside it
        with (
            mock.patch.dict(os.environ, env, clear=True),
            mock.patch.object(sys, "executable", here),
        ):
            before = yu.status(run=version)
            home = exe_paths.downloader_dir()  # what downloader_routes passes
            assert home is not None
            yu.update(home, fetch, run=version, system=system, machine="arm64")
            after = yu.status(run=version)
            mine = str(self.dirs.bin / exe_paths.exe("yt-dlp"))
        # The card's "Links need the downloader", then the button's result.
        self.assertFalse(before["installed"])
        self.assertEqual(before["home"], str(self.dirs.bin))
        self.assertEqual((after["installed"], after["managed"]), (True, True))
        self.assertEqual((after["path"], after["version"]), (mine, "2026.10.01"))


class Failures(Case):
    def test_a_failure_is_one_line_the_splash_can_show(self) -> None:
        failed = subprocess.CalledProcessError(2, ["/rt/uv", "pip", "sync"])
        self.assertEqual(progress.failure(failed), "uv stopped with status 2")
        as_text = subprocess.CalledProcessError(1, "/x/python -c pass")
        self.assertEqual(progress.failure(as_text), "python stopped with status 1")
        self.assertEqual(progress.failure(OSError("no space\n  left")), "no space left")
        self.assertEqual(
            progress.failed_line(rel.ReleaseError("offline")),
            f"{progress.FAILED_MARK} offline",
        )

    def test_main_says_why_on_the_progress_channel_only_when_asked(self) -> None:
        base = ["--prefix", str(self.tmp / "rt"), "--data-dir", str(self.tmp / "d")]
        for argv, marked in ((["--progress"], True), ([], False)):
            out, err = io.StringIO(), io.StringIO()
            with (
                self.subTest(argv=argv),
                mock.patch.object(
                    di.Installer, "install", side_effect=rel.ReleaseError("offline")
                ),
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
            ):
                self.assertEqual(di.main([*base, *argv]), 1)
                self.assertEqual(
                    f"{progress.FAILED_MARK} offline" in out.getvalue(), marked
                )
                self.assertIn("install failed — offline", err.getvalue())


if __name__ == "__main__":
    unittest.main()
