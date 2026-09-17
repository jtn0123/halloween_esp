"""Registration preserves a working launcher if native compilation fails."""

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
