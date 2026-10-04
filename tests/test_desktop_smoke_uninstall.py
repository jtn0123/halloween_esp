"""tools/desktop_smoke.py's Windows uninstall phase, against a fake Windows.

The real phase runs in release.yml's desktop job, on the real installer and
desktop/src-tauri/windows/hooks.nsh. What is tested here is the smoke's
side of it: the two command lines it uses, both written the way the NSIS
template writes them (tests/test_desktop_uninstall.py has the template's
audit), that it waits for an owner's uninstall that returns at once, and
that each wrong outcome is a failure — a hook that cannot tell an upgrade
from an owner, one that never removes the runtime, and one that takes the
owner's show.
"""

from __future__ import annotations

import contextlib
import io
import platform
import shutil
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import desktop_env as de
import desktop_smoke as smoke


class FakeWindows:
    """A per-user install as the template and hooks.nsh leave one. `wrong`
    names what this one's uninstaller does that the real one must not."""

    def __init__(self, tmp: Path, wrong: str = "") -> None:
        self.wrong = wrong
        self.env = {"LOCALAPPDATA": str(tmp / "local"), "APPDATA": str(tmp / "roaming")}
        self.folder = tmp / "local" / "Castle Tools"
        self.exe = self.folder / "castle-tools.exe"
        self.show, self.runtime = smoke.windows_places(self.env)
        self.bundle = tmp / "bundle"
        (self.bundle / "nsis").mkdir(parents=True)
        self.setup = self.bundle / "nsis" / "Castle Tools_1.2.3_x64-setup.exe"
        self.setup.write_bytes(b"")
        self.install()
        self.show.parent.mkdir(parents=True)
        self.show.write_text("scenes: []\n", encoding="utf-8")
        (self.runtime / "python").mkdir(parents=True)
        # The installer's own link from the runtime to the owner's data — a
        # junction on Windows, a symlink here.
        self.data = de.Dirs(self.runtime, self.show.parent, platform.system())
        if wrong != "no link":
            de.link_radio_data(self.data)
        self.ran: list[object] = []
        self.pending: list[Callable[[], None]] = []
        self.slept = 0

    def install(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        for name in ("castle-tools.exe", "uninstall.exe"):
            (self.folder / name).write_bytes(b"")

    def run(self, cmd: object, **kw: object) -> None:
        assert kw == {"check": True, "timeout": 600}, kw
        self.ran.append(cmd)
        if isinstance(cmd, str):
            self.in_place()
        elif cmd == [str(self.setup), "/S"]:
            self.install()
        else:
            # It copies itself to %TEMP% and returns; the copy works on.
            self.pending.append(self.owners)

    def in_place(self) -> None:
        """uninstall.exe with `_?=`: it cannot delete itself, so the folder
        and uninstall.exe stay; the hook must leave the runtime."""
        if self.wrong != "keeps the app":
            self.exe.unlink()
        if self.wrong == "hook ignores the upgrade":
            shutil.rmtree(self.runtime)
        if self.wrong == "upgrade takes the show":
            self.show.unlink()

    def owners(self) -> None:
        shutil.rmtree(self.folder)
        if self.wrong != "owner keeps the runtime":
            # hooks.nsh: the link alone first, then the runtime.
            de.remove_link(self.data.app / de.RADIO_DATA)
            shutil.rmtree(self.runtime)
        if self.wrong == "owner takes the show":
            self.show.unlink()

    def sleep(self, _seconds: float) -> None:
        self.slept += 1
        while self.pending:
            self.pending.pop()()

    def smoke(self, wait: float = 60.0) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            smoke.uninstall_windows(
                self.exe, self.env, self.bundle, self.run, wait, self.sleep
            )


class TheUninstallPhase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def test_an_upgrade_keeps_the_runtime_and_an_owner_removes_it(self) -> None:
        win = FakeWindows(self.tmp)
        uninstall = win.folder / "uninstall.exe"
        win.smoke()
        self.assertEqual(
            win.ran,
            [
                # The template's reinstall page: "$R1 _?=$4", UninstallString
                # quoted, the directory last and unquoted (spaces and all).
                f'"{uninstall}" /S _?={win.folder}',
                [str(win.setup), "/S"],
                [str(uninstall), "/S"],
            ],
        )
        self.assertGreater(win.slept, 0, "it waited for the uninstall that returned")
        self.assertFalse(win.runtime.exists())
        self.assertTrue(win.show.is_file())

    def test_each_wrong_uninstall_fails(self) -> None:
        for wrong, why in (
            ("keeps the app", "in-place uninstall left the app"),
            ("hook ignores the upgrade", "removed the runtime the new version"),
            ("upgrade takes the show", "in-place uninstall took the owner's show"),
            ("owner keeps the runtime", "after an owner's uninstall, the runtime"),
            ("owner takes the show", "an owner's uninstall took the owner's show"),
        ):
            with self.subTest(wrong), tempfile.TemporaryDirectory() as tmp:
                win = FakeWindows(Path(tmp), wrong)
                with self.assertRaisesRegex(smoke.SmokeError, why):
                    # The one that never finishes is given no time to.
                    win.smoke(wait=0.0 if wrong == "owner keeps the runtime" else 60.0)

    def test_a_launch_that_set_nothing_up_is_not_a_pass(self) -> None:
        """With no runtime to begin with, "the runtime went" proves nothing."""
        win = FakeWindows(self.tmp)
        shutil.rmtree(win.runtime)
        with self.assertRaisesRegex(
            smoke.SmokeError, "before any uninstall, the runtime"
        ):
            win.smoke()
        self.assertEqual(win.ran, [])

    def test_a_runtime_without_the_link_proves_nothing(self) -> None:
        win = FakeWindows(self.tmp, "no link")
        with self.assertRaisesRegex(smoke.SmokeError, "no link to the owner's data"):
            win.smoke()
        self.assertEqual(win.ran, [])

    def test_the_link_is_the_one_the_installer_makes(self) -> None:
        dirs = de.Dirs(Path("R"), Path("D"), "Windows")
        self.assertEqual(Path("R") / smoke.RADIO_LINK, dirs.app / de.RADIO_DATA)

    def test_the_places_are_the_apps(self) -> None:
        env = {"APPDATA": "R", "LOCALAPPDATA": "L"}
        show, runtime = smoke.windows_places(env)
        self.assertEqual(show, Path("R", smoke.IDENTIFIER, "radio", "scenes.yaml"))
        self.assertEqual(runtime, Path("L", smoke.IDENTIFIER, "runtime"))
        lib = (ROOT / "desktop" / "src-tauri" / "src" / "lib.rs").read_text(
            encoding="utf-8"
        )
        self.assertIn('runtime_dir: paths.app_local_data_dir()?.join("runtime"),', lib)

    def test_main_uninstalls_after_the_launches_on_windows_only(self) -> None:
        for system, uninstalls in (("Windows", 1), ("Darwin", 0)):
            order = mock.Mock()
            with (
                self.subTest(system),
                mock.patch.object(smoke.platform, "system", return_value=system),
                mock.patch.object(smoke, "mac_app", return_value=Path("/a")),
                mock.patch.object(smoke, "windows_app", return_value=Path("/a")),
                mock.patch.object(smoke, "log_path", return_value=self.tmp / "log"),
                mock.patch.object(smoke, "judge", order.judge),
                mock.patch.object(smoke, "read_the_pinned_model", order.model),
                mock.patch.object(smoke, "uninstall_windows", order.uninstall),
                mock.patch("sys.stdout"),
            ):
                self.assertEqual(smoke.main([str(self.tmp)]), 0)
                calls = [name for name, _args, _kwargs in order.mock_calls]
                self.assertEqual(calls, ["judge", "model"] + ["uninstall"] * uninstalls)


if __name__ == "__main__":
    unittest.main()
