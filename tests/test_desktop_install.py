"""tools/desktop_install.py + desktop_lifecycle.py + desktop_launch.py.

The installer is exercised for real against temp directories with every
outward effect replaced: `which` answers from a dict, `fetch` from canned
bodies, subprocesses are recorded instead of run. What is tested is the
DECISIONS — what a dry run plans, what an uninstall keeps, when an update
re-runs which tree, when a launch reuses a server — not uv or cargo.
"""

from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import desktop_env as de
import desktop_install as di
import desktop_launch as dl
import desktop_lifecycle as life
import desktop_release as rel
import ytdlp_update as yu
from test_desktop_release import api_body, fake_fetch, sha


def native(dirs: de.Dirs) -> de.Dirs:
    """The same folders on this machine's own system: cases that make a real
    link use it, so on Windows they exercise the junction and not a
    privileged symlink."""
    return de.Dirs(dirs.install, dirs.data, de.platform.system())


class TempCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.dirs = de.Dirs(self.tmp / "inst", self.tmp / "data", "Darwin")

    def args(self, *argv: str) -> argparse.Namespace:
        return di.build_parser().parse_args(["--uv", "uv", *argv])


class TestPlan(TempCase):
    def test_dry_run_plans_every_step_and_changes_nothing(self) -> None:
        said: list[str] = []
        which = {"ffmpeg": "/bin/ffmpeg", "ffprobe": "/bin/ffprobe", "cargo": "/c"}.get
        inst = di.Installer(
            self.args("--dry-run", "--source", str(ROOT)),
            self.dirs,
            fetch=fake_fetch({}),
            which=which,
            machine="arm64",
            say=said.append,
        )
        self.assertEqual(inst.install(), 0)
        plan = "\n".join(said)
        for expected in (
            "would copy",
            "uv venv --managed-python --python 3.13",
            "uv pip sync",
            "--require-hashes",
            "castle-core-aarch64-apple-darwin-<tag>.zip",
            "ffmpeg: /bin/ffmpeg",
            "download the standalone yt-dlp",
            "get_model('htdemucs')",
            "prepare the data dir",
            "Castle Tools.command",
            "install.json",
        ):
            with self.subTest(step=expected):
                self.assertIn(expected, plan)
        self.assertFalse(self.dirs.install.exists())
        self.assertFalse(self.dirs.data.exists())

    def test_without_a_release_build_or_cargo_it_stops_with_a_reason(self) -> None:
        dirs = de.Dirs(self.tmp / "i", self.tmp / "d", "Linux")
        inst = di.Installer(
            self.args("--dry-run"), dirs, which=lambda _n: None, say=lambda _m: None
        )
        with self.assertRaisesRegex(rel.ReleaseError, "rustup"):
            inst.core_bins(ROOT)

    def test_no_ffmpeg_anywhere_downloads_the_pin(self) -> None:
        said: list[str] = []
        inst = di.Installer(
            self.args("--dry-run"), self.dirs, which=lambda _n: None, say=said.append
        )
        inst.ffmpeg()
        self.assertTrue(any("pinned static ffmpeg" in s for s in said))

    def test_the_package_managers_ffmpeg_is_installed_once(self) -> None:
        # winget's Links folder joins PATH only for processes started after
        # it, so every later run used to winget-install ffmpeg again
        # (tests/install_smoke.py saw it on windows-latest).
        links = self.tmp / "links"
        dirs = de.Dirs(self.tmp / "inst", self.tmp / "data", "Windows")
        said: list[str] = []
        inst = di.Installer(
            self.args("--dry-run"), dirs, which={"winget": "w"}.get, say=said.append
        )
        with mock.patch.object(di.tp, "after_package_manager", return_value=[links]):
            self.assertIsNone(inst.existing_ffmpeg())
            self.assertIn("winget install", said[-1])
            links.mkdir()
            for name in ("ffmpeg.exe", "ffprobe.exe"):
                (links / name).write_bytes(b"MZ")
            said.clear()
            inst.ffmpeg()
        self.assertEqual(inst.found["ffmpeg"], str(links / "ffmpeg.exe"))
        self.assertEqual(said, [f"ffmpeg: {links / 'ffmpeg.exe'}"])

    def test_the_downloader_is_kept_until_an_update_asks_for_the_latest(self) -> None:
        mine = self.dirs.bin / "yt-dlp"
        mine.parent.mkdir(parents=True)
        mine.write_bytes(b"#!old")
        said: list[str] = []
        kept = di.Installer(
            self.args(), self.dirs, which=lambda _n: None, say=said.append
        )
        kept.ytdlp()
        self.assertEqual((kept.found["ytdlp"], said), (str(mine), []))
        # --update runs the same code as Castle Radio's Update the downloader:
        # the latest release, checked against its SHA2-256SUMS.
        new = b"#!new"
        links = [("yt-dlp_macos", "dl/bin"), (yu.SUMS, "dl/sums")]
        assets = [{"name": n, "browser_download_url": u} for n, u in links]
        latest = {"tag_name": "2026.10.01", "assets": assets}
        fetch = fake_fetch(
            {
                yu.API_LATEST: json.dumps(latest).encode(),
                "dl/bin": new,
                "dl/sums": f"{sha(new)}  yt-dlp_macos\n".encode(),
            }
        )
        args = self.args("--update")
        inst = di.Installer(
            args, self.dirs, fetch=fetch, machine="arm64", say=said.append
        )
        version = {b"#!old": "2026.09.01", new: "2026.10.01"}
        with mock.patch.object(
            yu, "run_version", lambda p: version[Path(p).read_bytes()]
        ):
            inst.ytdlp()
        self.assertEqual((mine.read_bytes(), inst.found["ytdlp"]), (new, str(mine)))
        self.assertEqual(yu.read_record(self.dirs.bin)["tag"], "2026.10.01")

    def test_package_manager_comes_before_the_pin(self) -> None:
        said: list[str] = []
        inst = di.Installer(
            self.args("--dry-run"), self.dirs, which={"brew": "/b"}.get, say=said.append
        )
        # Not this machine's /opt/homebrew/bin, which may hold an ffmpeg.
        with mock.patch.object(di.tp, "after_package_manager", return_value=[]):
            inst.ffmpeg()
        self.assertTrue(any("brew install ffmpeg" in s for s in said))


class TestStaging(TempCase):
    def setUp(self) -> None:
        super().setUp()
        self.dirs = native(self.dirs)

    def test_a_tree_without_git_is_copied_minus_build_output(self) -> None:
        src = self.tmp / "src"
        for rel_path in (
            "tools/a.py",
            "core/target/release/x",
            ".venv/bin/p",
            "scenes/scenes.yaml",
        ):
            (src / rel_path).parent.mkdir(parents=True, exist_ok=True)
            (src / rel_path).write_text("x", encoding="utf-8")
        inst = di.Installer(
            self.args(), self.dirs, which=lambda _n: None, say=lambda _m: None
        )
        inst.stage_app(src)
        self.assertTrue((self.dirs.app / "tools" / "a.py").is_file())
        self.assertTrue((self.dirs.app / "scenes" / "scenes.yaml").is_file())
        self.assertFalse((self.dirs.app / ".venv").exists())
        self.assertFalse((self.dirs.app / "core" / "target").exists())
        self.assertFalse(self.dirs.app.with_name("app.new").exists())

    def test_the_sellers_own_files_are_never_staged(self) -> None:
        """devices.toml and the library manifest are export-ignore, so the
        release zip lacks them; a copied tree or a clone must too."""
        src = self.tmp / "src"
        for rel_path in ("tools/a.py", "devices.toml", "tracks/tracks.json"):
            (src / rel_path).parent.mkdir(parents=True, exist_ok=True)
            (src / rel_path).write_text("x", encoding="utf-8")

        def staged(git: str | None) -> set[Path]:
            inst = di.Installer(
                self.args(), self.dirs, which=lambda _n: git, say=lambda _m: None
            )
            return set(inst.source_files(src))

        self.assertEqual(staged(None), {Path("tools/a.py")}, "a copied tree")
        git = shutil.which("git")
        if git is None:
            self.fail("git is needed to stage from a clone")
        subprocess.run([git, "init", "-q", str(src)], check=True)
        subprocess.run([git, "-C", str(src), "add", "-A"], check=True)
        self.assertEqual(staged(git), {Path("tools/a.py")}, "a clone")

    def test_restaging_keeps_the_built_core_and_never_touches_data(self) -> None:
        src = self.tmp / "src"
        (src / "tools").mkdir(parents=True)
        (src / "tools" / "a.py").write_text("v1", encoding="utf-8")
        inst = di.Installer(
            self.args(), self.dirs, which=lambda _n: None, say=lambda _m: None
        )
        inst.stage_app(src)
        built = self.dirs.app / "core" / "target" / "release" / "studio"
        built.parent.mkdir(parents=True)
        built.write_text("bin", encoding="utf-8")
        de.link_radio_data(self.dirs)
        (self.dirs.data / "precious.mp3").write_text("song", encoding="utf-8")
        (src / "tools" / "a.py").write_text("v2", encoding="utf-8")
        inst.stage_app(src)
        self.assertEqual(
            (self.dirs.app / "tools" / "a.py").read_text(encoding="utf-8"), "v2"
        )
        self.assertTrue(built.is_file())
        self.assertTrue((self.dirs.data / "precious.mp3").is_file())

    def test_version_file_names_the_tag(self) -> None:
        src = self.tmp / "src"
        (src / "installer").mkdir(parents=True)
        inst = di.Installer(
            self.args(), self.dirs, which=lambda _n: None, say=lambda _m: None
        )
        (src / di.VERSION_FILE).write_text(
            "$Format:%(describe:tags=true)$\n", encoding="utf-8"
        )
        self.assertIsNone(inst.release_tag(src), "an unexpanded template is no tag")
        (src / di.VERSION_FILE).write_text("v0.3.1\n", encoding="utf-8")
        self.assertEqual(inst.release_tag(src), "v0.3.1")
        inst.args.tag = "v9.9.9"
        self.assertEqual(inst.release_tag(src), "v9.9.9")

    def test_an_existing_install_remembers_its_data_dir(self) -> None:
        de.write_json(self.dirs.install_file, {"data": str(self.tmp / "elsewhere")})
        args = self.args("--prefix", str(self.dirs.install))
        self.assertEqual(di.resolve_dirs(args, {}).data, self.tmp / "elsewhere")


class TestUninstall(TempCase):
    def setUp(self) -> None:
        super().setUp()
        self.dirs = native(self.dirs)

    def installed(self) -> None:
        de.write_json(self.dirs.install_file, {"data": str(self.dirs.data)})
        (self.dirs.app / "demo" / "castle-radio").mkdir(parents=True)
        de.link_radio_data(self.dirs)
        (self.dirs.data / "song.mp3").write_text("mine", encoding="utf-8")

    def test_uninstall_keeps_the_data(self) -> None:
        self.installed()
        code = life.uninstall(
            self.dirs, False, False, {}, self.tmp / "home", say=lambda _m: None
        )
        self.assertEqual(code, 0)
        self.assertFalse(self.dirs.install.exists())
        self.assertTrue((self.dirs.data / "song.mp3").is_file())

    def test_purge_removes_the_data_and_dry_run_removes_nothing(self) -> None:
        self.installed()
        life.uninstall(
            self.dirs, True, True, {}, self.tmp / "home", say=lambda _m: None
        )
        self.assertTrue(self.dirs.install.exists() and self.dirs.data.exists())
        life.uninstall(
            self.dirs, True, False, {}, self.tmp / "home", say=lambda _m: None
        )
        self.assertFalse(self.dirs.install.exists() or self.dirs.data.exists())

    def test_refuses_home_root_and_strangers(self) -> None:
        home = self.tmp / "home"
        bad = de.Dirs(home, self.tmp / "d", "Linux")
        self.assertEqual(
            life.uninstall(bad, False, False, {}, home, say=lambda _m: None), 1
        )
        stranger = de.Dirs(self.tmp / "someone-else", self.tmp / "d", "Linux")
        stranger.install.mkdir()
        self.assertEqual(
            life.uninstall(stranger, False, False, {}, home, say=lambda _m: None), 1
        )
        self.assertTrue(stranger.install.exists())
        self.assertIsNotNone(life.removable(Path(Path.cwd().anchor), home))

    def test_start_menu_shortcut_is_per_user(self) -> None:
        p = life.start_menu_shortcut({"APPDATA": r"C:\U\AppData\Roaming"}, Path("/h"))
        self.assertEqual(p.name, "Castle Tools.lnk")
        self.assertIn("Start Menu", str(p))


class TestUpdate(TempCase):
    def setUp(self) -> None:
        super().setUp()
        de.write_json(
            self.dirs.install_file, {"data": str(self.dirs.data), "tag": "v0.1.0"}
        )
        self.runs: list[list[str]] = []

    def test_up_to_date_reruns_the_installed_tree_with_the_answer(self) -> None:
        fetch = fake_fetch({rel.API_LATEST: api_body("v0.1.0", {})})

        def record(cmd: list[str]) -> int:
            self.runs.append(cmd)
            return 0

        life.update(
            self.args("--update"), self.dirs, fetch, record, say=lambda _m: None
        )
        (cmd,) = self.runs
        self.assertEqual(Path(cmd[1]).parent.parent, self.dirs.app)
        blob = cmd[cmd.index("--release-json") + 1]
        self.assertEqual(
            json.loads(Path(blob).read_text(encoding="utf-8"))["tag"], "v0.1.0"
        )

    def test_newer_release_runs_the_new_trees_installer(self) -> None:
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("halloween_esp-0.2.0/tools/desktop_install.py", "#")
        latest = rel.release_from_api(api_body("v0.2.0", {}))
        fetch = fake_fetch(
            {rel.API_LATEST: api_body("v0.2.0", {}), latest.source_zip: buf.getvalue()}
        )

        def record(cmd: list[str]) -> int:
            self.runs.append(cmd)
            return 0

        args = self.args("--update", "--skip-model")
        self.assertEqual(
            life.update(args, self.dirs, fetch, record, say=lambda _m: None), 0
        )
        (cmd,) = self.runs
        self.assertEqual(Path(cmd[1]).parent.parent.name, "halloween_esp-0.2.0")
        self.assertIn("--skip-model", cmd)
        self.assertEqual(cmd[cmd.index("--prefix") + 1], str(self.dirs.install))

    def test_nothing_installed_is_not_an_update(self) -> None:
        self.dirs.install_file.unlink()
        code = life.update(
            self.args("--update"),
            self.dirs,
            fake_fetch({}),
            lambda _c: 0,
            say=lambda _m: None,
        )
        self.assertEqual(code, 1)


class TestLauncher(TempCase):
    def test_the_port_reaches_the_server_only_as_a_checked_int(self) -> None:
        self.assertEqual(dl.listen_port(8871), 8871)
        for bad in (0, 80, 1023, 65536):
            with self.subTest(port=bad), self.assertRaises(ValueError):
                dl.listen_port(bad)

    def test_an_out_of_range_port_is_refused_before_anything_starts(self) -> None:
        with (
            mock.patch.object(dl, "start") as start,
            mock.patch("sys.stdout", new_callable=io.StringIO) as out,
        ):
            self.assertEqual(dl.main(["--port", "22"]), 2)
        start.assert_not_called()
        self.assertIn("1024-65535", out.getvalue())

    def test_radio_state_reads_the_identity_route(self) -> None:
        ours = {"service": "castle-radio", "protocol": 1}
        self.assertEqual(dl.radio_state(1, lambda _u: ours), "ours")
        self.assertEqual(dl.radio_state(1, lambda _u: None), "free")
        self.assertEqual(dl.radio_state(1, lambda _u: "other"), "taken")
        self.assertEqual(dl.radio_state(1, lambda _u: {**ours, "protocol": 2}), "taken")

    def test_update_check_is_daily_quiet_and_opt_out(self) -> None:
        now = datetime(2026, 10, 1, tzinfo=UTC)
        fetch = fake_fetch({rel.API_LATEST: api_body("v0.2.0", {})})
        msg = dl.check_for_update(self.dirs, {"tag": "v0.1.0"}, {}, fetch, now)
        self.assertIn("v0.2.0", msg or "")
        self.assertIsNone(
            dl.check_for_update(
                self.dirs,
                {"tag": "v0.1.0"},
                {},
                fake_fetch({}),
                now + timedelta(hours=2),
            ),
            "a second launch the same day asks nobody",
        )
        later = now + timedelta(days=2)
        self.assertIsNone(
            dl.check_for_update(self.dirs, {"tag": "v0.1.0"}, {}, fake_fetch({}), later)
        )
        off = {"check_updates": False}
        with mock.patch.object(dl.rel, "find_release") as never:
            dl.check_for_update(self.dirs, {}, off, fetch, later + timedelta(days=5))
            never.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
