"""The two readers the notices trust: the desktop crate inventory, built
from `cargo metadata`, and the firmware check, built from a build's linker
maps. Both are fed synthetic inputs here, so the test needs neither cargo
nor an ESP-IDF build; the real inputs are compared in
test_third_party_notices.py and in release.yml / firmware.yml.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import notices_desktop as nd
import notices_firmware as nf
from notices_model import shown

APP = ("castle-tools", "0.1.0")


def dep(name: str, *kinds: str | None) -> dict:
    return {"pkg": f"{name} 1.0.0", "dep_kinds": [{"kind": k} for k in kinds]}


class InventoryTests(unittest.TestCase):
    """app -> a (normal), b (build only), c (a proc-macro); a -> d (normal
    AND dev); on Windows only, app -> e. The lock also holds f, which no
    graph reaches."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def pkg(self, name: str, version: str = "1.0.0", **kw: object) -> dict:
        folder = self.tmp / f"{name}-{version}"
        folder.mkdir()
        p: dict[str, object] = {
            "id": f"{name} {version}",
            "name": name,
            "version": version,
            "manifest_path": str(folder / "Cargo.toml"),
            "targets": [{"kind": ["proc-macro" if kw.pop("proc", False) else "lib"]}],
            "license": "MIT",
            "authors": [],
        }
        p.update(kw)
        return p

    def meta(self, windows: bool) -> dict:
        app = self.app
        pkgs = [app, *self.deps]
        root_deps = [dep("a", None), dep("b", "build"), dep("c", None)]
        if windows:
            root_deps.append(dep("e", None))
        nodes = [
            {"id": app["id"], "deps": root_deps},
            {"id": "a 1.0.0", "deps": [dep("d", None, "dev")]},
        ]
        nodes += [{"id": p["id"], "deps": []} for p in self.deps if p["name"] != "a"]
        return {"packages": pkgs, "resolve": {"root": app["id"], "nodes": nodes}}

    def build(self) -> dict:
        self.app = self.pkg(*APP)
        self.deps = [
            self.pkg("a"),
            self.pkg("b"),
            self.pkg("c", proc=True),
            self.pkg("d", authors=["Dee <dee@example.com>"]),
            self.pkg("e", license=None, license_file="LICENSE"),
        ]
        a = self.tmp / "a-1.0.0"
        (a / "LICENSE-MIT").write_text(
            "Copyright (c) 2020 The A people\n", encoding="utf-8", newline="\n"
        )
        (a / "NOTICE").write_text("A notice.\n", encoding="utf-8", newline="\n")
        (a / "src.rs").write_text(
            "Copyright (c) 1999 not a licence file\n", encoding="utf-8", newline="\n"
        )
        metas = {"macos": self.meta(False), "windows": self.meta(True)}
        lock = {APP, *((n, "1.0.0") for n in "abcdef")}
        return nd.inventory(lock, metas)

    def test_only_normal_non_macro_edges_are_linked(self) -> None:
        doc = self.build()
        linked = {p["name"]: p["linked"] for p in doc["packages"]}
        self.assertEqual(
            linked,
            {
                "a": ["macos", "windows"],
                "b": [],
                "c": [],
                "d": ["macos", "windows"],
                "e": ["windows"],
                "f": [],
            },
        )
        self.assertEqual(doc["app"], {"name": APP[0], "version": APP[1]})

    def test_entries_carry_licence_copyright_source_and_notice(self) -> None:
        by = {p["name"]: p for p in self.build()["packages"]}
        self.assertEqual(
            by["a"],
            {
                "name": "a",
                "version": "1.0.0",
                "linked": ["macos", "windows"],
                "license": "MIT",
                "copyright": ["Copyright (c) 2020 The A people"],
                "source": "https://crates.io/crates/a/1.0.0",
                "notice": "A notice.\n",
            },
        )
        self.assertEqual(by["b"], {"name": "b", "version": "1.0.0", "linked": []})
        self.assertEqual(
            by["d"]["copyright"],
            ["No copyright line in the package; authors per Cargo.toml: Dee"],
        )
        self.assertEqual(by["e"]["license"], "LicenseRef-file:LICENSE")
        self.assertEqual(
            by["e"]["copyright"], ["No copyright line or author in the package"]
        )

    def test_components_say_which_platform_carries_them(self) -> None:
        doc = self.build()
        notes = {c.name: c.note for c in nd.crates(doc)}
        self.assertEqual(notes, {"a": "", "d": "", "e": "windows only"})
        self.assertEqual([c.name for c in nd.crates(doc, "macos")], ["a", "d"])
        self.assertEqual(nd.notice_texts(doc), {"a 1.0.0": "A notice.\n"})

    def test_dump_is_one_line_per_package_and_round_trips(self) -> None:
        doc = self.build()
        text = nd.dump(doc)
        self.assertEqual(json.loads(text), doc)
        self.assertEqual(
            sum(line.startswith('    {"') for line in text.splitlines()), 6
        )

    def test_the_committed_inventory_is_its_own_dump(self) -> None:
        raw = nd.INVENTORY.read_text(encoding="utf-8")
        self.assertEqual(nd.dump(json.loads(raw)), raw)


class CargoTests(unittest.TestCase):
    def test_metadata_is_offline_and_locked(self) -> None:
        done = SimpleNamespace(returncode=0, stdout='{"packages": []}', stderr="")
        with mock.patch.object(nd.subprocess, "run", return_value=done) as run:
            self.assertEqual(
                nd.cargo_metadata(ROOT, "x86_64-pc-windows-msvc"), {"packages": []}
            )
        argv = run.call_args.args[0]
        self.assertIn("--offline", argv)
        self.assertIn("--locked", argv)
        self.assertEqual(argv[-2:], ["--filter-platform", "x86_64-pc-windows-msvc"])

    def test_a_failed_metadata_says_how_to_fill_the_cache(self) -> None:
        failed = SimpleNamespace(returncode=101, stdout="", stderr="no network")
        with (
            mock.patch.object(nd.subprocess, "run", return_value=failed),
            self.assertRaises(SystemExit) as raised,
        ):
            nd.cargo_metadata(ROOT, "aarch64-apple-darwin")
        self.assertIn("cargo fetch --locked", str(raised.exception))

    def test_refresh_reads_both_release_targets(self) -> None:
        seen: list[str] = []

        def fake(_dir: Path, target: str) -> dict:
            seen.append(target)
            return {
                "packages": [
                    {"id": "app", "name": APP[0], "version": APP[1], "targets": []}
                ],
                "resolve": {"root": "app", "nodes": [{"id": "app", "deps": []}]},
            }

        with mock.patch.object(nd, "cargo_metadata", fake):
            doc = nd.refresh()
        self.assertEqual(sorted(seen), sorted(nd.TARGETS.values()))
        self.assertTrue(doc["packages"])
        self.assertFalse(any(p["linked"] for p in doc["packages"]))


MAP = """\
Archive member included to satisfy reference by file (symbol)

{tc}/xtensa-esp-elf/lib/libc.a(lib_a-memcpy.o)
                              esp-idf/main/libmain.a(app_main.c.obj) (memcpy)
/b/esp-idf/main/libmain.a(app_main.c.obj)
/b/src/libsrc.a(main.cpp.o)
{extra}
Discarded input sections

/b/after/libnot_counted.a(x.o)
"""


class FirmwareBuildTests(unittest.TestCase):
    def setUp(self) -> None:
        self.build = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.build)
        (self.build / "build" / "bootloader").mkdir(parents=True)
        self.tc = f"/t/idf/tools/xtensa-esp-elf/{nf.TOOLCHAIN}"
        self.write_map()
        (self.build / "build" / "bootloader" / "bootloader.map").write_text(
            MAP.format(tc=self.tc, extra=""), encoding="utf-8", newline="\n"
        )
        self.write_lock(dict(nf.MANAGED))
        version_h = self.build / "src" / "esphome" / "core" / "version.h"
        version_h.parent.mkdir(parents=True)
        version_h.write_text(
            f'#define ESPHOME_VERSION "{nf.ESPHOME}"\n', encoding="utf-8", newline="\n"
        )

    def write_map(self, extra: str = "", tc: str | None = None) -> None:
        text = MAP.format(tc=tc or self.tc, extra=extra)
        (self.build / "build" / "castle.map").write_text(
            text, encoding="utf-8", newline="\n"
        )

    def write_lock(self, managed: dict[str, str], idf: str = nf.IDF_VERSION) -> None:
        lines = ["dependencies:"]
        for name, version in {**managed, "idf": idf}.items():
            lines += [
                f"  {name}:",
                "    source:",
                "      type: service",
                f"    version: '{version}'",
            ]
        lines.append("manifest_hash: abc")
        (self.build / "dependencies.lock").write_text(
            "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
        )

    def test_a_build_the_table_covers_passes(self) -> None:
        self.assertEqual(nf.check_build(self.build), [])

    def test_an_archive_the_table_does_not_know_fails(self) -> None:
        self.write_map("/b/esp-idf/new/libnewthing.a(n.o)")
        self.assertEqual(
            nf.check_build(self.build),
            [
                "castle.map: libnewthing.a is linked but not in notices_firmware.ARCHIVES"
            ],
        )

    def test_another_toolchain_fails(self) -> None:
        self.write_map(tc="/t/idf/tools/xtensa-esp-elf/esp-15.1.0_20270101")
        self.assertEqual(
            nf.check_build(self.build),
            [f"castle.map: linked by a toolchain other than {nf.TOOLCHAIN}"],
        )

    def test_another_idf_or_component_version_fails(self) -> None:
        managed = dict(nf.MANAGED)
        managed["espressif/mdns"] = "9.9.9"
        del managed["zorxx/multipart-parser"]
        managed["someone/new-thing"] = "1.0.0"
        self.write_lock(managed, idf="5.5.6")
        errors = nf.check_build(self.build)
        self.assertEqual(
            errors,
            [
                f"ESP-IDF 5.5.6 built this; the table is {nf.IDF_VERSION}",
                "component espressif/mdns 9.9.9 is not the reviewed one (1.12.0)",
                "component someone/new-thing 1.0.0 is not the reviewed one (None)",
                "component zorxx/multipart-parser is in the table but not in this build",
            ],
        )

    def test_another_esphome_fails(self) -> None:
        version_h = self.build / "src" / "esphome" / "core" / "version.h"
        version_h.write_text(
            '#define ESPHOME_VERSION "2027.1.0"\n', encoding="utf-8", newline="\n"
        )
        self.assertEqual(
            nf.check_build(self.build),
            [f"ESPHome 2027.1.0 built this; the table is {nf.ESPHOME}"],
        )

    def test_not_a_build_directory(self) -> None:
        empty = self.build / "empty"
        empty.mkdir()
        self.assertIn("no linker map", nf.check_build(empty)[0])
        (self.build / "dependencies.lock").unlink()
        self.assertEqual(
            nf.check_build(self.build),
            [f"{shown(self.build / 'dependencies.lock')} is missing"],
        )
        (self.build / "build" / "castle.map").write_text(
            "not a map\n", encoding="utf-8", newline="\n"
        )
        self.assertIn("no archive members", nf.check_build(self.build)[0])

    def test_every_archive_names_components_in_the_table(self) -> None:
        named = {c for comps in nf.ARCHIVES.values() for c in comps}
        self.assertEqual(named, set(nf.FIRMWARE))


if __name__ == "__main__":
    unittest.main()
