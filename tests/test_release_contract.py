"""Never break an installed buyer: what an app ALREADY on someone's computer
looks for in a GitHub Release is exactly what tools/release_assets.py makes.

An installed Castle Tools cannot be told that a release renamed an asset —
it just stops finding its update. So each reader keeps its own spelling of
the names (castle_update.py, desktop_release.py, release_channel.py,
desktop/src-tauri/src/release.rs and channel.rs, tauri.conf.json), and this
file builds a release the way the workflow does — stage_firmware, then
finish — and holds every one of those spellings to it. The castle update is
then run against that very release, end to end. docs/RELEASING.md "What an
installed app reads" is the prose half of this file.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import helpers  # noqa: F401  (hermetic env)

# isort: split
import castle_update as cu
import desktop_release as rel
import fw_formats
import release_assets as ra
import release_channel as channel
import release_emu

TAG = "v1.2.3"
RELEASE_RS = ROOT / "desktop" / "src-tauri" / "src" / "release.rs"
TAURI_CONF = ROOT / "desktop" / "src-tauri" / "tauri.conf.json"
IMAGE = b"\xe9" + bytes(range(256)) * 300


class StagedRelease(unittest.TestCase):
    """One release directory, staged and finished as release.yml does it."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        build = Path(tmp.name) / "build"
        build.mkdir()
        (build / "firmware.ota.bin").write_bytes(IMAGE)
        (build / "firmware.factory.bin").write_bytes(b"\xe9factory" + IMAGE)
        self.out = Path(tmp.name) / "dist"
        ra.stage_firmware(TAG, build / "firmware.ota.bin", self.out)
        for target in ra.CORE_TARGETS:
            (self.out / ra.core_zip_name(target, TAG)).write_bytes(b"zip")
        self.names = ra.finish(TAG, self.out)

    def test_the_castle_update_finds_its_descriptor_and_image(self) -> None:
        board = ra.BOARD
        about = cu.ABOUT.format(board=board, tag=TAG)
        ota = cu.OTA.format(board=board, tag=TAG)
        self.assertIn(about, self.names)
        self.assertIn(ota, self.names)
        doc = json.loads((self.out / about).read_text(encoding="utf-8"))
        self.assertEqual(doc["schema"], cu.ABOUT_SCHEMA)
        self.assertEqual(
            (doc["board"], doc["tag"], doc["ota"], doc["ota_bytes"]),
            (board, TAG, ota, len(IMAGE)),
        )
        self.assertEqual(doc["fw_variant"], ra.FW_VARIANT)
        self.assertEqual(doc["version"], fw_formats.this_firmware())

    def test_the_installer_finds_its_zips_and_sums(self) -> None:
        self.assertEqual(rel.SUMS, ra.SUMS)
        self.assertEqual(self.names[-1], ra.SUMS, "the sums are written last")
        for target in ra.CORE_TARGETS:
            self.assertIn(rel.core_asset(target, TAG), self.names)
        sums = rel.parse_sums((self.out / ra.SUMS).read_text(encoding="utf-8"))
        self.assertEqual(sorted(sums), sorted(n for n in self.names if n != ra.SUMS))

    def test_the_apps_compiled_in_names_are_the_releases(self) -> None:
        text = RELEASE_RS.read_text(encoding="utf-8")
        board = re.search(r'pub const BOARD: &str = "([^"]+)";', text)
        self.assertIsNotNone(board)
        assert board is not None
        self.assertEqual(board[1], ra.BOARD)
        targets = re.search(
            r"pub const CORE_TARGETS: \[&str; \d+\] = \[(.*?)\];", text, re.DOTALL
        )
        assert targets is not None
        self.assertEqual(tuple(re.findall(r'"([^"]+)"', targets[1])), ra.CORE_TARGETS)
        literals = set(re.findall(r'"(castle-[a-z0-9.-]+-v1\.2\.3\.[a-z.]+)"', text))
        self.assertGreaterEqual(len(literals), 3, "release.rs names_match_the_contract")
        self.assertLessEqual(literals, set(self.names))

    def test_the_updaters_manifest_is_the_desktop_releases_last_asset(self) -> None:
        full = ra.expected_assets(TAG, desktop=True)
        self.assertEqual(full[-1], ra.LATEST)
        self.assertEqual(
            channel.STABLE_LATEST_JSON,
            f"https://github.com/{rel.REPO}/releases/latest/download/{ra.LATEST}",
        )
        self.assertEqual(
            channel.LATEST_JSON.format(tag=TAG),
            f"https://github.com/{rel.REPO}/releases/download/{TAG}/{ra.LATEST}",
        )
        conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
        self.assertEqual(
            conf["plugins"]["updater"]["endpoints"], [channel.STABLE_LATEST_JSON]
        )

    def test_the_release_build_is_the_one_a_buyers_castle_reports(self) -> None:
        fw = ROOT / "firmware"
        common = (fw / "castle_sd_common.yaml").read_text(encoding="utf-8")
        buyer = (fw / "castle_buyer.yaml").read_text(encoding="utf-8")
        self.assertRegex(common, rf"\n  board_id: {re.escape(ra.BOARD)}\n")
        self.assertRegex(buyer, rf"\n  fw_variant: {re.escape(ra.FW_VARIANT)}\n")
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("make build-buyer", workflow)

    def test_a_castle_updates_from_exactly_this_release(self) -> None:
        github = release_emu.ReleaseEmu().start()
        self.addCleanup(github.stop)
        github.publish(TAG, {n: (self.out / n).read_bytes() for n in self.names})
        st = {"version": "5.0", "board": ra.BOARD, "fw_variant": ra.FW_VARIANT}
        with tempfile.TemporaryDirectory() as tmp:
            off = cu.offer(github.fetch, ra.BOARD, False, Path(tmp))
            plan = cu.judge(st, off)
            data = cu.download_image(off, ra.BOARD, Path(tmp), github.fetch)
        self.assertTrue(plan.update)
        self.assertEqual((plan.tag, plan.available), (TAG, fw_formats.this_firmware()))
        self.assertEqual(data, IMAGE)


if __name__ == "__main__":
    unittest.main()
