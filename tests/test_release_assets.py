"""tools/release_assets.py — the release's asset names are a contract.

The desktop app's updater, its firmware check and the web flasher are all
written against these exact names (docs/RELEASING.md). These tests spell the
names out literally rather than calling the helpers back, so a rename has to
be made here too, on purpose.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import release_assets as ra

TAG = "v0.1.0"


class TagTests(unittest.TestCase):
    def test_plain_tag_is_a_release(self) -> None:
        self.assertFalse(ra.check_tag("v0.1.0"))
        self.assertFalse(ra.check_tag("v12.30.4"))

    def test_suffix_makes_a_prerelease(self) -> None:
        self.assertTrue(ra.check_tag("v0.2.0-rc.1"))
        self.assertTrue(ra.check_tag("v1.0.0-beta"))

    def test_non_tags_are_refused(self) -> None:
        for bad in ("0.1.0", "v0.1", "v5.73", "v0.1.0-", "v0.1.0/x", "v0.1.0 "):
            with self.subTest(tag=bad), self.assertRaises(SystemExit):
                ra.check_tag(bad)

    def test_cli_prints_the_prerelease_flag(self) -> None:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(ra.main(["check-tag", "v0.1.0-rc.2"]), 0)
        self.assertEqual(out.getvalue(), "true\n")


class NameTests(unittest.TestCase):
    def test_the_contract_names(self) -> None:
        self.assertEqual(
            ra.expected_assets(TAG),
            [
                "castle-fw-feather-s3-4m2p-v0.1.0.factory.bin",
                "castle-fw-feather-s3-4m2p-v0.1.0.ota.bin",
                "castle-core-x86_64-pc-windows-msvc-v0.1.0.zip",
                "castle-core-aarch64-apple-darwin-v0.1.0.zip",
                "castle-core-x86_64-apple-darwin-v0.1.0.zip",
                "flasher-manifest.json",
                "SHA256SUMS",
            ],
        )

    def test_manifest_is_esp_web_tools_shaped(self) -> None:
        m = ra.flasher_manifest(TAG)
        self.assertIs(m["new_install_prompt_erase"], True)
        builds = m["builds"]
        assert isinstance(builds, list)
        (build,) = builds
        self.assertEqual(build["chipFamily"], "ESP32-S3")
        self.assertIs(build["improv"], True)
        self.assertEqual(
            build["parts"],
            [{"path": "castle-fw-feather-s3-4m2p-v0.1.0.factory.bin", "offset": 0}],
        )


class StagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.out = self.tmp / "dist"
        build = self.tmp / "build"
        build.mkdir()
        (build / "firmware.ota.bin").write_bytes(b"app")
        (build / "firmware.factory.bin").write_bytes(b"boot+table+app")
        self.ota = build / "firmware.ota.bin"
        self.bins = self.tmp / "bins"
        self.bins.mkdir()
        for name in ("analyze_track", "scene_render", "studio"):
            (self.bins / name).write_bytes(name.encode())
            (self.bins / f"{name}.exe").write_bytes(name.encode() + b".exe")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _quiet(self, argv: list[str]) -> int:
        with contextlib.redirect_stdout(io.StringIO()):
            return ra.main(argv)

    def _stage_all(self) -> None:
        self._quiet(["stage-firmware", TAG, str(self.ota), str(self.out)])
        for target in ra.CORE_TARGETS:
            self._quiet(["zip-core", TAG, target, str(self.bins), str(self.out)])

    def test_firmware_lands_under_its_release_names(self) -> None:
        ra.stage_firmware(TAG, self.ota, self.out)
        factory = self.out / "castle-fw-feather-s3-4m2p-v0.1.0.factory.bin"
        self.assertEqual(factory.read_bytes(), b"boot+table+app")
        ota = self.out / "castle-fw-feather-s3-4m2p-v0.1.0.ota.bin"
        self.assertEqual(ota.read_bytes(), b"app")

    def test_a_build_without_a_factory_image_is_not_a_release(self) -> None:
        (self.ota.parent / "firmware.factory.bin").unlink()
        with self.assertRaisesRegex(SystemExit, "firmware.factory.bin"):
            ra.stage_firmware(TAG, self.ota, self.out)

    def test_windows_zip_carries_exe_and_mac_zip_does_not(self) -> None:
        win = ra.zip_core(TAG, "x86_64-pc-windows-msvc", self.bins, self.out)
        mac = ra.zip_core(TAG, "aarch64-apple-darwin", self.bins, self.out)
        with zipfile.ZipFile(win) as zf:
            self.assertEqual(
                sorted(zf.namelist()),
                ["analyze_track.exe", "scene_render.exe", "studio.exe"],
            )
        with zipfile.ZipFile(mac) as zf:
            self.assertEqual(
                sorted(zf.namelist()), ["analyze_track", "scene_render", "studio"]
            )
            mode = zf.getinfo("studio").external_attr >> 16
            self.assertEqual(mode & 0o111, 0o111)

    def test_unknown_target_and_missing_binary_are_refused(self) -> None:
        with self.assertRaisesRegex(SystemExit, "not a release target"):
            ra.zip_core(TAG, "riscv64gc-unknown-linux-gnu", self.bins, self.out)
        (self.bins / "studio").unlink()
        with self.assertRaisesRegex(SystemExit, "missing castle-core binary"):
            ra.zip_core(TAG, "aarch64-apple-darwin", self.bins, self.out)

    def test_finish_writes_manifest_and_sums_over_everything_else(self) -> None:
        self._stage_all()
        self.assertEqual(self._quiet(["finish", TAG, str(self.out)]), 0)
        manifest = json.loads(
            (self.out / "flasher-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["version"], TAG)
        sums = (self.out / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
        named = {line.split("  ", 1)[1]: line.split("  ", 1)[0] for line in sums}
        self.assertEqual(sorted(named), sorted(ra.expected_assets(TAG)[:-1]))
        for name, digest in named.items():
            data = (self.out / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest, name)

    def test_finish_refuses_a_partial_or_padded_release(self) -> None:
        ra.stage_firmware(TAG, self.ota, self.out)
        with self.assertRaisesRegex(SystemExit, "missing .*castle-core"):
            ra.finish(TAG, self.out)
        self._stage_all()
        (self.out / "notes.txt").write_text("stray", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, r"extra \['notes.txt'\]"):
            ra.finish(TAG, self.out)

    def test_bad_usage_prints_the_doc(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(ra.main(["finish", TAG]), 2)
        self.assertIn("release contract", err.getvalue())


if __name__ == "__main__":
    unittest.main()
