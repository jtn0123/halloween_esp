"""THIRD-PARTY-NOTICES: current, complete, and inside everything that ships.

Three questions, one class each. Are the committed notices what the tables
make, and do the tables still describe the lockfiles and pins they were
written from (a crate added without a refresh fails here)? Does anything
copyleft or non-commercial ship that the tables have not argued for? And
does every artifact the release publishes actually carry its file — the
desktop bundle, the castle-core zips, the firmware release asset and the
web flasher page, and the source zip the installer unpacks?
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import desktop_bundle as db
import notices_desktop as nd
import notices_firmware as nf
import notices_render as nr
import release_assets as ra
import third_party_notices as tp
from notices_external import EXTERNAL
from notices_model import category, chosen, shown

WORKFLOWS = ROOT / ".github" / "workflows"


def read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


class CurrentTests(unittest.TestCase):
    def test_check_finds_nothing(self) -> None:
        """Texts pinned, inventory == lockfile, policy clean, files current:
        `tools/third_party_notices.py check` — what to run when this fails."""
        self.assertEqual(tp.problems(), [])

    def test_the_inventory_matches_the_lockfile_both_ways(self) -> None:
        doc = nd.load()
        dropped = {
            **doc,
            "packages": [p for p in doc["packages"] if p["name"] != "serde"],
        }
        errors = nd.staleness(dropped)
        self.assertTrue(errors)
        self.assertTrue(
            all("but not in the notices inventory" in e for e in errors), errors
        )
        ghost = {
            **doc,
            "packages": [*doc["packages"], {"name": "ghost", "version": "1.0.0"}],
        }
        self.assertEqual(
            nd.staleness(ghost),
            [
                (
                    "ghost 1.0.0 is in the notices inventory but no longer in Cargo.lock — "
                    "run: tools/third_party_notices.py refresh"
                )
            ],
        )

    def test_pins_the_notices_describe_are_the_pins_in_the_tree(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        (tmp / "Cargo.lock").write_text(
            '[[package]]\nname = "castle-core"\nversion = "0.1.0"\n\n'
            '[[package]]\nname = "rand"\nversion = "0.9.0"\n',
            encoding="utf-8",
            newline="\n",
        )
        (tmp / "rust-toolchain.toml").write_text(
            '[toolchain]\nchannel = "1.99.0"\n', encoding="utf-8", newline="\n"
        )
        cli = tmp / "desktop" / "cli"
        cli.mkdir(parents=True)
        (cli / "package.json").write_text(
            '{"devDependencies": {"@tauri-apps/cli": "3.0.0"}}',
            encoding="utf-8",
            newline="\n",
        )
        doc = nd.load()
        doc = {
            **doc,
            "packages": [p for p in doc["packages"] if p["name"] != "webview2-com-sys"],
        }
        with (
            mock.patch.object(nd, "CORE_LOCK", tmp / "Cargo.lock"),
            mock.patch.object(nd, "CORE_TOOLCHAIN", tmp / "rust-toolchain.toml"),
            mock.patch.object(nd, "ROOT", tmp),
        ):
            text = "\n".join(nd.staleness(doc))
        for want in (
            "core/Cargo.lock now has dependencies",
            "no longer Rust 1.88.0",
            "Tauri CLI other than",
            "is gone from Cargo.lock: re-check the WebView2",
        ):
            self.assertIn(want, text)

    def test_the_firmware_and_flasher_pins_are_the_ones_reviewed(self) -> None:
        self.assertIn(f"esphome=={nf.ESPHOME}\n", read("requirements.txt"))
        tools = next(e for e in EXTERNAL if e.name == "esp-web-tools")
        web = re.search(r"esp-web-tools@([\d.]+)/", read("flasher/index.html"))
        self.assertIsNotNone(web)
        assert web is not None
        self.assertTrue(tools.version.startswith(f"{web.group(1)} "), tools.version)

    def test_every_linked_crate_is_named_in_the_desktop_notices(self) -> None:
        text = read(nr.ARTIFACTS["desktop"].path)
        for p in nd.load()["packages"]:
            if p["linked"]:
                self.assertIn(f". {p['name']} {p['version']}\n", text)

    def test_a_missing_or_edited_notices_file_is_stale(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        with mock.patch.object(nr, "ROOT", tmp):
            self.assertEqual(len(nr.stale()), len(nr.ARTIFACTS))
            self.assertTrue(all("is missing" in e for e in nr.stale()))
            (tmp / "THIRD-PARTY-NOTICES.txt").write_text(
                "old\n", encoding="utf-8", newline="\n"
            )
            self.assertIn(
                "THIRD-PARTY-NOTICES.txt is stale — run: tools/third_party_notices.py generate",
                nr.stale(),
            )


class CopyleftTests(unittest.TestCase):
    """GPL ships in exactly one artifact, for reasons the table prints."""

    def copyleft(self, key: str) -> set[str]:
        return {
            c.name
            for c in nr.components(key)
            if any(category(t) == "copyleft" for t in chosen(c.license))
        }

    def test_copyleft_is_exactly_the_reviewed_firmware_components(self) -> None:
        self.assertEqual(
            self.copyleft("firmware"),
            {
                "ESPHome (C++ runtime and components)",
                "esp-audio-libs",
                "GCC runtime libraries (libgcc, libstdc++)",
            },
        )
        for c in nr.components("firmware"):
            if c.name in self.copyleft("firmware"):
                self.assertIn(
                    c.override,
                    read(nr.ARTIFACTS["firmware"].path).replace("\n   ", " "),
                )

    def test_nothing_copyleft_ships_in_the_apps_or_the_source(self) -> None:
        for key in ("desktop", "castle-core", "source"):
            with self.subTest(artifact=key):
                self.assertEqual(self.copyleft(key), set())

    def test_the_esphome_override_points_at_a_real_section(self) -> None:
        self.assertIn("docs/LICENSING.md", nf.FIRMWARE["esphome"].override)
        self.assertIn("## The firmware image and GPLv3", read("docs/LICENSING.md"))


class ShippingTests(unittest.TestCase):
    def test_the_desktop_bundle_carries_its_notices(self) -> None:
        conf = json.loads(read("desktop/src-tauri/tauri.conf.json"))
        resources = conf["bundle"]["resources"]
        src = (
            ROOT
            / "desktop"
            / "src-tauri"
            / "../../licenses/THIRD-PARTY-NOTICES-desktop.txt"
        )
        self.assertEqual(
            resources,
            {
                "../../licenses/THIRD-PARTY-NOTICES-desktop.txt": "THIRD-PARTY-NOTICES.txt"
            },
        )
        self.assertEqual(src.resolve(), ROOT / nr.ARTIFACTS["desktop"].path)

    def test_nothing_else_rides_in_the_app_without_notices(self) -> None:
        """The release build bundles one folder, castle/, and only
        tools/desktop_bundle.py writes it: this tree's own files, castle-core
        and a pinned uv — each with notices (uv's in the desktop file).
        Putting ffmpeg, a Python or the htdemucs weights in the app changes
        one of these pins — and needs their notices first (docs/LICENSING.md);
        tests/test_desktop_bundle.py holds what a staged folder contains."""
        release = json.loads(read("desktop/src-tauri/tauri.release.conf.json"))
        self.assertEqual(
            release["bundle"]["resources"], {"../sidecar/castle/": "castle/"}
        )
        sidecar = [
            ln.strip()
            for ln in read(".github/workflows/release.yml").splitlines()
            if "desktop/sidecar" in ln
        ]
        self.assertEqual(
            sidecar,
            [
                (
                    'run: python tools/desktop_bundle.py stage "$TAG" "$TARGET" '
                    "core-zip desktop/sidecar"
                )
            ],
        )
        self.assertEqual(
            (db.APP, db.BIN, db.UV, db.ABOUT), ("app", "bin", "uv", "bundle.json")
        )
        self.assertEqual(set(db.UV_PINS), set(ra.DESKTOP_TARGETS))
        for pin in db.UV_PINS.values():
            self.assertTrue(
                pin.url.startswith(
                    f"https://github.com/astral-sh/uv/releases/download/{db.UV_VERSION}/"
                ),
                pin.url,
            )
        uv = [c for c in nr.components("desktop") if c.name == "uv"]
        self.assertEqual([c.version for c in uv], [db.UV_VERSION])
        self.assertEqual(uv[0].license, "MIT OR Apache-2.0")
        self.assertIn(f"uv {db.UV_VERSION}", read(nr.ARTIFACTS["desktop"].path))

    def test_the_release_assets_are_the_generated_files(self) -> None:
        self.assertEqual(ra.FIRMWARE_NOTICES, ROOT / nr.ARTIFACTS["firmware"].path)
        self.assertEqual(ra.CORE_NOTICES, ROOT / nr.ARTIFACTS["castle-core"].path)
        self.assertIn(ra.notices_name("v1.0.0"), ra.expected_assets("v1.0.0"))

    def test_the_release_checks_the_image_against_its_notices_first(self) -> None:
        release = read(".github/workflows/release.yml")
        check = release.index("third_party_notices.py check-firmware")
        self.assertLess(check, release.index("release_assets.py stage-firmware"))
        firmware = read(".github/workflows/firmware.yml")
        self.assertIn(
            "third_party_notices.py check-firmware firmware/.esphome/build/castle\n",
            firmware,
        )

    def test_the_web_flasher_serves_and_links_the_firmware_notices(self) -> None:
        pages = read(".github/workflows/pages.yml")
        self.assertIn("--pattern 'castle-fw-*.notices.txt'", pages)
        self.assertIn(
            "cp dl/castle-fw-*.notices.txt _site/THIRD-PARTY-NOTICES.txt", pages
        )
        self.assertIn('href="THIRD-PARTY-NOTICES.txt"', read("flasher/index.html"))

    def test_the_source_zip_keeps_the_notices(self) -> None:
        """GitHub's "Source code" zip is `git archive`, which drops anything
        marked export-ignore; the installer unpacks that zip."""
        paths = ["THIRD-PARTY-NOTICES.txt", *(a.path for a in nr.ARTIFACTS.values())]
        out = subprocess.run(
            ["git", "check-attr", "export-ignore", "--", *paths],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        self.assertNotIn(": set", out)


class CliTests(unittest.TestCase):
    def run_main(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = tp.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_check_passes_and_path_names_the_file(self) -> None:
        self.assertEqual(
            self.run_main("check")[:2], (0, "third-party notices: current\n")
        )
        code, out, _ = self.run_main("path", "firmware")
        self.assertEqual(
            (code, out.strip()), (0, shown(ROOT / nr.ARTIFACTS["firmware"].path))
        )

    def test_bad_arguments_print_the_usage(self) -> None:
        for argv in (
            (),
            ("bogus",),
            ("path", "nope"),
            ("check", "extra"),
            ("check-firmware",),
        ):
            with self.subTest(argv=argv):
                code, _, err = self.run_main(*argv)
                self.assertEqual(code, 2)
                self.assertIn("third_party_notices.py generate", err)

    def test_a_failed_check_lists_each_problem(self) -> None:
        with mock.patch.object(tp, "problems", return_value=["one", "two"]):
            code, _, err = self.run_main("check")
        self.assertEqual(code, tp.FAILED)
        self.assertEqual(err, "  one\n  two\nthird-party notices: 2 problem(s)\n")

    def test_generate_writes_every_file(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        with mock.patch.object(tp, "ROOT", tmp):
            code, out, _ = self.run_main("generate")
        self.assertEqual(code, 0)
        self.assertEqual(
            sorted(out.split()),
            sorted(shown(Path(a.path)) for a in nr.ARTIFACTS.values()),
        )
        for a in nr.ARTIFACTS.values():
            self.assertEqual((tmp / a.path).read_text(encoding="utf-8"), read(a.path))

    def test_refresh_rewrites_the_inventory_and_regenerates(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        inventory = tmp / "desktop-crates.json"
        with (
            mock.patch.object(nd, "refresh", return_value=nd.load()),
            mock.patch.object(nd, "INVENTORY", inventory),
            mock.patch.object(tp, "ROOT", tmp),
        ):
            code, out, _ = self.run_main("refresh")
        self.assertEqual(
            (code, out), (0, "third-party notices: refreshed and current\n")
        )
        self.assertEqual(
            inventory.read_text(encoding="utf-8"), read("licenses/desktop-crates.json")
        )

    def test_check_firmware_reports_the_build(self) -> None:
        with mock.patch.object(nf, "check_build", return_value=[]):
            self.assertEqual(
                self.run_main("check-firmware", "b")[:2],
                (0, "firmware notices cover b\n"),
            )
        with mock.patch.object(nf, "check_build", return_value=["libx.a"]):
            self.assertEqual(self.run_main("check-firmware", "b")[0], tp.FAILED)


if __name__ == "__main__":
    unittest.main()
