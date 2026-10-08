"""tools/desktop_env.py: where an installed Castle Tools lives, and its env.

Every case names its own platform, environment and home folder, so nothing
here reads the real profile or writes outside a temp dir — the suite runs
the same on a Mac, on Windows CI and on Linux.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import desktop_env as de

HOME = Path("/home/someone")


class TestDefaultDirs(unittest.TestCase):
    def test_windows_uses_localappdata_for_both_roots(self) -> None:
        d = de.default_dirs(
            "Windows", {"LOCALAPPDATA": r"C:\Users\b\AppData\Local"}, HOME
        )
        local = Path(r"C:\Users\b\AppData\Local")
        self.assertEqual(d.install, local / "Programs" / "CastleTools")
        self.assertEqual(d.data, local / "CastleTools")
        self.assertEqual(d.python, d.install / "env" / "Scripts" / "python.exe")
        self.assertEqual(d.exe("ffmpeg"), "ffmpeg.exe")

    def test_windows_without_localappdata_falls_back_under_home(self) -> None:
        d = de.default_dirs("Windows", {}, HOME)
        self.assertEqual(d.data, HOME / "AppData" / "Local" / "CastleTools")

    def test_macos_keeps_data_in_application_support(self) -> None:
        d = de.default_dirs("Darwin", {}, HOME)
        self.assertEqual(d.install, HOME / "Applications" / "CastleTools")
        self.assertEqual(
            d.data, HOME / "Library" / "Application Support" / "CastleTools"
        )
        self.assertEqual(d.python, d.install / "env" / "bin" / "python")
        self.assertEqual(d.exe("ffmpeg"), "ffmpeg")

    def test_linux_honours_xdg_data_home(self) -> None:
        d = de.default_dirs("Linux", {"XDG_DATA_HOME": "/x/share"}, HOME)
        self.assertEqual(d.data, Path("/x/share/CastleTools"))
        self.assertEqual(d.install, HOME / ".local" / "opt" / "CastleTools")

    def test_overrides_win_on_every_platform(self) -> None:
        env = {"CASTLE_TOOLS_HOME": "/tmp/i", "CASTLE_TOOLS_DATA": "/tmp/d"}
        for system in ("Windows", "Darwin", "Linux"):
            with self.subTest(system=system):
                d = de.default_dirs(system, env, HOME)
                self.assertEqual((d.install, d.data), (Path("/tmp/i"), Path("/tmp/d")))

    def test_platform_is_read_when_not_given(self) -> None:
        with mock.patch.object(de.platform, "system", return_value="Darwin"):
            d = de.default_dirs(environ={}, home=HOME)
        self.assertEqual(d.system, "Darwin")
        self.assertEqual(d.install, HOME / "Applications" / "CastleTools")

    def test_data_never_inside_install(self) -> None:
        for system in ("Windows", "Darwin", "Linux"):
            with self.subTest(system=system):
                d = de.default_dirs(system, {}, HOME)
                self.assertNotIn(d.install, d.data.parents)
                self.assertNotIn(d.data, d.install.parents)


class TestInstalledDirs(unittest.TestCase):
    def test_install_json_names_the_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "inst"
            de.write_json(install / de.INSTALL_FILE, {"data": str(Path(tmp) / "data")})
            d = de.installed_dirs(install / "app", system="Linux")
            self.assertEqual(d.install, install)
            self.assertEqual(d.data, Path(tmp) / "data")

    def test_a_checkout_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {"CASTLE_TOOLS_HOME": tmp, "CASTLE_TOOLS_DATA": tmp}
            with mock.patch.dict(os.environ, env):
                d = de.installed_dirs(Path(tmp) / "nothing" / "app", system="Linux")
            self.assertEqual(d.install, Path(tmp))


class TestRecordAndEnv(unittest.TestCase):
    def setUp(self) -> None:
        self.dirs = de.Dirs(Path("/i"), Path("/d"), "Darwin")

    def test_record_keeps_roots_and_drops_empty_values(self) -> None:
        rec = de.install_record(self.dirs, ffmpeg="/x/ffmpeg", ytdlp="")
        self.assertEqual(rec["schema"], de.SCHEMA)
        self.assertEqual(rec["data"], str(Path("/d")))
        self.assertEqual(rec["ffmpeg"], "/x/ffmpeg")
        self.assertNotIn("ytdlp", rec)

    def test_env_points_every_knob_at_the_data_root(self) -> None:
        rec = {
            "python": "/i/env/bin/python",
            "ffmpeg": "/opt/ff/ffmpeg",
            "ytdlp": "/i/bin/yt-dlp",
        }
        env = de.launch_env(self.dirs, rec, {}, {"PATH": "/usr/bin", "OTHER": "kept"})
        self.assertEqual(env["CASTLE_TRACKS"], str(Path("/d/tracks")))
        self.assertEqual(env["CASTLE_SCENES"], str(Path("/d/scenes.yaml")))
        self.assertEqual(env["CASTLE_BUILD"], str(Path("/d/build")))
        self.assertEqual(env["CASTLE_PY"], "/i/env/bin/python")
        self.assertEqual(env["CASTLE_FFMPEG"], "/opt/ff/ffmpeg")
        self.assertEqual(env["CASTLE_YTDLP"], "/i/bin/yt-dlp")
        # One managed yt-dlp: the installer's, which Update the downloader
        # replaces in place (tools/ytdlp_update.py).
        self.assertEqual(env["CASTLE_DOWNLOADER_DIR"], str(Path("/i/bin")))
        self.assertEqual(env["PYTHONUTF8"], "1")
        # Where the model step puts the htdemucs weights and Demucs finds
        # them — which CI's cached weights (tools/model_pin.py) bypass, so
        # it is held here rather than by the smokes.
        self.assertEqual(env["HF_HOME"], str(Path("/i/models/huggingface")))
        self.assertEqual(env["TORCH_HOME"], str(Path("/i/models/torch")))
        self.assertEqual(env["OTHER"], "kept")
        path = env["PATH"].split(os.pathsep)
        self.assertEqual(path[0], str(Path("/i/bin")))
        self.assertIn(str(Path("/opt/ff")), path)
        self.assertEqual(path[-1], "/usr/bin")
        self.assertEqual(len(path), len(set(path)), "PATH entries are deduplicated")

    def test_no_castle_named_means_the_users_store_decides(self) -> None:
        """Unpinned, the studio and Castle Radio both read the first castle
        of CASTLE_DEVICES — so nothing inherited may shadow it, and an empty
        CASTLE_HOST (which would mean "explicitly none") is not set either."""
        base = {"CASTLE_HOST": "10.0.0.9", "CASTLE_RADIO_HOST": "10.0.0.8"}
        env = de.launch_env(self.dirs, {}, {}, base)
        self.assertNotIn("CASTLE_HOST", env)
        self.assertNotIn("CASTLE_RADIO_HOST", env)
        self.assertEqual(env["CASTLE_DEVICES"], str(Path("/d/devices.toml")))

    def test_settings_castle_feeds_both_servers(self) -> None:
        env = de.launch_env(self.dirs, {}, {"castle_host": " castle.local "}, {})
        self.assertEqual(env["CASTLE_HOST"], "castle.local")
        self.assertEqual(env["CASTLE_RADIO_HOST"], "castle.local")

    def test_the_key_store_is_the_users_and_a_set_key_pins_it(self) -> None:
        env = de.launch_env(self.dirs, {}, {}, {})
        self.assertEqual(env["CASTLE_DEVICES"], str(Path("/d/devices.toml")))
        self.assertNotIn("CASTLE_KEY", env)
        env = de.launch_env(self.dirs, {}, {"castle_key": ' pa"ss#1 '}, {})
        self.assertEqual(env["CASTLE_KEY"], 'pa"ss#1')
        for bad in ("two words", "", 7, "k" * 65):
            with self.subTest(bad=bad):
                env = de.launch_env(self.dirs, {}, {"castle_key": bad}, {})
                self.assertNotIn("CASTLE_KEY", env)

    def test_printing_the_env_withholds_the_key(self) -> None:
        lines = de.shown({"CASTLE_KEY": "s3cret", "PATH": "/bin"})
        self.assertEqual(lines, ["CASTLE_KEY=<set>", "PATH=/bin"])


class TestDataDir(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_seed_writes_once_and_never_overwrites(self) -> None:
        shipped = self.tmp / "shipped.yaml"
        shipped.write_text("scenes: [shipped]\n", encoding="utf-8")
        target = self.tmp / "data" / "scenes.yaml"
        self.assertTrue(de.seed_scenes(shipped, target))
        target.write_text("scenes: [mine]\n", encoding="utf-8")
        shipped.write_text("scenes: [newer release]\n", encoding="utf-8")
        self.assertFalse(de.seed_scenes(shipped, target))
        self.assertEqual(target.read_text(encoding="utf-8"), "scenes: [mine]\n")

    def test_write_json_round_trips_and_leaves_no_temp(self) -> None:
        p = self.tmp / "a" / "s.json"
        de.write_json(p, {"castle_host": "x"})
        self.assertEqual(de.read_json(p), {"castle_host": "x"})
        self.assertEqual([q.name for q in p.parent.iterdir()], ["s.json"])
        p.write_text("[1, 2]", encoding="utf-8")
        self.assertEqual(de.read_json(p), {}, "a non-object is no settings at all")

    def test_prepare_seeds_links_and_is_idempotent(self) -> None:
        # This machine's own system: a junction on Windows, a symlink elsewhere.
        dirs = de.Dirs(self.tmp / "inst", self.tmp / "data", de.platform.system())
        shipped = dirs.app / de.SHIPPED_SCENES
        shipped.parent.mkdir(parents=True)
        shipped.write_text("scenes: []\n", encoding="utf-8")
        first = de.prepare(dirs)
        self.assertTrue(any("seeded" in n for n in first))
        link = dirs.app / de.RADIO_DATA
        self.assertTrue(de.is_link(link))
        self.assertEqual(
            Path(os.path.realpath(link)), Path(os.path.realpath(dirs.data))
        )
        self.assertEqual(de.prepare(dirs), [], "a second run has nothing to say")
        self.assertEqual(de.link_radio_data(dirs), "kept")
        self.assertTrue(dirs.tracks.is_dir() and dirs.build.is_dir())

    def test_a_real_radio_data_dir_is_left_alone(self) -> None:
        dirs = de.Dirs(self.tmp / "inst", self.tmp / "data", "Linux")
        real = dirs.app / de.RADIO_DATA
        real.mkdir(parents=True)
        (real / "catalog.json").write_text("{}", encoding="utf-8")
        self.assertTrue(de.link_radio_data(dirs).startswith("skipped"))
        self.assertTrue((real / "catalog.json").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
