"""Registration preserves a working launcher if native compilation fails,
and is a macOS thing only: elsewhere it says so and runs nothing."""

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import register_castle_launcher as launcher


class CastleLauncherTests(unittest.TestCase):
    def test_failed_compile_preserves_existing_app(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Open Castle Studio.command").touch()
            applications = root / "Applications"
            existing = applications / "Castle Tools.app" / "Contents" / "MacOS"
            existing.mkdir(parents=True)
            binary = existing / "CastleTools"
            binary.write_bytes(b"working launcher")
            with (
                mock.patch.object(launcher.sys, "platform", "darwin"),
                mock.patch.object(
                    launcher.subprocess,
                    "run",
                    side_effect=subprocess.CalledProcessError(1, "swiftc"),
                ),
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    launcher.register(root, applications)
            self.assertEqual(binary.read_bytes(), b"working launcher")

    def test_registration_keeps_checkout_path_as_data(self) -> None:
        root = Path('/tmp/Castle "test" $(not-a-command)')
        info = launcher.launcher_info(root)
        self.assertEqual(info["CastleProjectPath"], str(root.resolve()))
        self.assertEqual(
            info["CFBundleURLTypes"][0]["CFBundleURLSchemes"], ["castle-tools"]
        )
        self.assertTrue(info["LSUIElement"])


class PlatformTests(unittest.TestCase):
    """docs/PRODUCTION-TODO.md §4.2: the Mac-only branch behind a platform
    check — macOS builds the handler; Windows and Linux are told plainly that
    there is nothing to register here, with no Xcode advice and no command run."""

    def test_macos_compiles_signs_and_registers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "Open Castle Studio.command").touch()
            with (
                mock.patch.object(launcher.sys, "platform", "darwin"),
                mock.patch.object(launcher.subprocess, "run") as run,
            ):
                app = launcher.register(root, root / "Applications")
            self.assertEqual(app, root / "Applications" / "Castle Tools.app")
            self.assertTrue((app / "Contents" / "Info.plist").is_file())
            tools = [Path(c.args[0][0]).name for c in run.call_args_list]
            self.assertEqual(tools, ["xcrun", "codesign", "lsregister"])

    def test_windows_and_linux_are_told_it_is_not_on_this_platform(self) -> None:
        for platform in ("win32", "linux"):
            with (
                self.subTest(platform),
                tempfile.TemporaryDirectory() as temp,
                mock.patch.object(launcher.sys, "platform", platform),
                mock.patch.object(launcher.subprocess, "run") as run,
            ):
                root = Path(temp)
                (root / "Open Castle Studio.command").touch()
                with self.assertRaises(launcher.NotOnThisPlatform):
                    launcher.register(root, root / "Applications")
                err = io.StringIO()
                with (
                    contextlib.redirect_stderr(err),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    code = launcher.main()
                self.assertEqual(code, 2)
                self.assertIn("not on this platform", err.getvalue())
                self.assertIn(platform, err.getvalue())
                self.assertNotIn("Command Line Tools", err.getvalue())
                run.assert_not_called()
                self.assertFalse((root / "Applications").exists())


class MainTests(unittest.TestCase):
    """What a Mac is told for each way registration ends: ready, Apple's
    tools missing (the Xcode hint), or the checkout itself wrong (no hint —
    installing Xcode would not bring the launcher back)."""

    def run_main(self, outcome: Path | BaseException) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        register = mock.Mock(
            side_effect=outcome if isinstance(outcome, BaseException) else None,
            return_value=outcome,
        )
        with (
            mock.patch.object(launcher, "register", register),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            code = launcher.main()
        return code, out.getvalue(), err.getvalue()

    def test_ready_names_the_app_and_the_next_click(self) -> None:
        app = Path("/Applications/Castle Tools.app")
        code, out, err = self.run_main(app)
        self.assertEqual((code, err), (0, ""))
        self.assertIn(f"Website startup is ready: {app}\n", out)
        self.assertIn("Connect Mac tools", out)

    def test_apples_tools_missing_gets_the_xcode_hint(self) -> None:
        for error in (
            subprocess.CalledProcessError(1, "swiftc"),
            FileNotFoundError("xcrun"),
        ):
            with self.subTest(type(error).__name__):
                code, out, err = self.run_main(error)
                self.assertEqual((code, out), (1, ""))
                self.assertIn("Could not register Castle Tools", err)
                self.assertIn("Command Line Tools", err)

    def test_a_broken_checkout_is_not_blamed_on_xcode(self) -> None:
        code, out, err = self.run_main(RuntimeError("launcher is missing"))
        self.assertEqual((code, out), (1, ""))
        self.assertIn("Could not register Castle Tools: launcher is missing", err)
        self.assertNotIn("Command Line Tools", err)
