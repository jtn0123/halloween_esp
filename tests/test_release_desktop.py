"""tools/release_assets.py, desktop half — the Tauri bundles and latest.json.

The installed app's updater fetches latest.json from the newest Release and
follows the URLs in it, so those names are as much a contract as the
firmware's (docs/RELEASING.md). Spelled out literally here, like
test_release_assets.py, so a rename is made on purpose.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import release_assets as ra

TAG = "v0.1.0"
REPO = "jtn0123/halloween_esp"
MAC = "aarch64-apple-darwin"
WIN = "x86_64-pc-windows-msvc"


def fake_bundle(root: Path, target: str) -> Path:
    """What `tauri build --target T` leaves under target/T/release/bundle."""
    b = root / target / "release" / "bundle"
    if target == WIN:
        (b / "nsis").mkdir(parents=True)
        (b / "nsis" / "Castle Tools_0.1.0_x64-setup.exe").write_bytes(b"nsis")
        (b / "nsis" / "Castle Tools_0.1.0_x64-setup.exe.sig").write_text("SIG-WIN\n")
    else:
        (b / "dmg").mkdir(parents=True)
        (b / "macos").mkdir()
        (b / "dmg" / "Castle Tools_0.1.0_aarch64.dmg").write_bytes(b"dmg")
        (b / "macos" / "Castle Tools.app.tar.gz").write_bytes(b"tgz")
        (b / "macos" / "Castle Tools.app.tar.gz.sig").write_text("SIG-MAC\n")
    return b


class DesktopTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.out = self.tmp / "dist"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _quiet(self, argv: list[str]) -> int:
        with contextlib.redirect_stdout(io.StringIO()):
            return ra.main(argv)

    def _stage_everything(self) -> None:
        build = self.tmp / "fw"
        build.mkdir()
        (build / "firmware.ota.bin").write_bytes(b"app")
        (build / "firmware.factory.bin").write_bytes(b"full")
        ra.stage_firmware(TAG, build / "firmware.ota.bin", self.out)
        bins = self.tmp / "bins"
        bins.mkdir()
        for name in ra.CORE_BINS:
            (bins / name).write_bytes(b"x")
            (bins / f"{name}.exe").write_bytes(b"x")
        for t in ra.CORE_TARGETS:
            ra.zip_core(TAG, t, bins, self.out)
        for t in (MAC, WIN):
            b = fake_bundle(self.tmp / "tauri", t)
            self._quiet(["stage-desktop", TAG, t, str(b), str(self.out)])

    def test_the_desktop_contract_names(self) -> None:
        plain = ra.expected_assets(TAG)
        full = ra.expected_assets(TAG, desktop=True)
        self.assertEqual(full[:5], plain[:5])
        self.assertEqual(
            full[5:],
            [
                "castle-tools-aarch64-apple-darwin-v0.1.0.app.tar.gz",
                "castle-tools-aarch64-apple-darwin-v0.1.0.app.tar.gz.sig",
                "castle-tools-aarch64-apple-darwin-v0.1.0.dmg",
                "castle-tools-x86_64-pc-windows-msvc-v0.1.0-setup.exe",
                "castle-tools-x86_64-pc-windows-msvc-v0.1.0-setup.exe.sig",
                "flasher-manifest.json",
                "SHA256SUMS",
                "latest.json",
            ],
        )

    def test_staging_renames_and_keeps_the_signature(self) -> None:
        b = fake_bundle(self.tmp, MAC)
        ra.stage_desktop(TAG, MAC, b, self.out)
        tgz = self.out / "castle-tools-aarch64-apple-darwin-v0.1.0.app.tar.gz"
        self.assertEqual(tgz.read_bytes(), b"tgz")
        self.assertEqual(Path(f"{tgz}.sig").read_text(), "SIG-MAC\n")

    def test_an_unsigned_build_is_refused(self) -> None:
        b = fake_bundle(self.tmp, WIN)
        (b / "nsis" / "Castle Tools_0.1.0_x64-setup.exe.sig").unlink()
        with self.assertRaisesRegex(SystemExit, "TAURI_SIGNING_PRIVATE_KEY"):
            ra.stage_desktop(TAG, WIN, b, self.out)

    def test_an_ambiguous_bundle_and_an_unknown_target_are_refused(self) -> None:
        b = fake_bundle(self.tmp, MAC)
        (b / "dmg" / "older.dmg").write_bytes(b"old")
        with self.assertRaisesRegex(SystemExit, "expected one dmg"):
            ra.stage_desktop(TAG, MAC, b, self.out)
        with self.assertRaisesRegex(SystemExit, "not a desktop target"):
            ra.stage_desktop(TAG, "x86_64-unknown-linux-gnu", b, self.out)

    def test_finish_with_desktop_writes_latest_json_against_the_release(self) -> None:
        self._stage_everything()
        self.assertEqual(
            self._quiet(["finish", TAG, str(self.out), "--desktop", REPO]), 0
        )
        doc = json.loads((self.out / "latest.json").read_text())
        self.assertEqual(doc["version"], "0.1.0")
        base = "https://github.com/jtn0123/halloween_esp/releases/download/v0.1.0/"
        self.assertEqual(
            doc["platforms"],
            {
                "darwin-aarch64": {
                    "signature": "SIG-MAC",
                    "url": base + "castle-tools-aarch64-apple-darwin-v0.1.0.app.tar.gz",
                },
                "windows-x86_64": {
                    "signature": "SIG-WIN",
                    "url": base
                    + "castle-tools-x86_64-pc-windows-msvc-v0.1.0-setup.exe",
                },
            },
        )
        self.assertIn("latest.json", (self.out / "SHA256SUMS").read_text())

    def test_finish_without_desktop_refuses_stray_bundles(self) -> None:
        self._stage_everything()
        with self.assertRaisesRegex(SystemExit, "extra .*castle-tools"):
            ra.finish(TAG, self.out)

    def test_finish_with_desktop_refuses_a_missing_platform(self) -> None:
        self._stage_everything()
        for p in self.out.glob("castle-tools-x86_64-pc-windows-msvc-*"):
            p.unlink()
        with self.assertRaisesRegex(SystemExit, "missing updater signature"):
            ra.finish(TAG, self.out, REPO)


if __name__ == "__main__":
    unittest.main()
