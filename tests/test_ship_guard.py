"""tools/ship_guard.py: nothing personal ships (docs/PRODUCTION-TODO.md §8).

Two halves. The guard is run against planted leaks — a text line, a temp
git repository, a staged release directory with archives in it — so each
rule is seen to bite. And it is run against this repository, which must come
back clean: that test is the guard, run by `make test` on every change.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import release_assets
import ship_guard as g

SELLER = "10.27.27.81"


class TestScanText(unittest.TestCase):
    def test_the_documented_examples_pass(self) -> None:
        for ip in sorted(g.EXAMPLE_IPS):
            with self.subTest(ip=ip):
                self.assertEqual(g.scan_text("tools/x.py", f"host = {ip}:80"), [])

    def test_the_sellers_lan_is_history_only(self) -> None:
        line = f"the porch castle answered at {SELLER}\n"
        self.assertEqual(g.scan_text("docs/notes/03-build.md", line), [])
        self.assertEqual(g.scan_text("tests/test_x.py", line), [])
        self.assertEqual(
            g.scan_text("demo/castle-radio/page.js", line),
            [f"demo/castle-radio/page.js:1: the seller's LAN address {SELLER}"],
        )

    def test_any_other_private_address_is_a_finding_anywhere(self) -> None:
        for ip in ("192.168.7.40", "172.20.1.9", "10.44.0.3"):
            with self.subTest(ip=ip):
                found = g.scan_text("docs/notes/x.md", f"a\nseen at {ip}.\n")
                self.assertEqual(len(found), 1)
                self.assertIn("docs/notes/x.md:2: a private-LAN address", found[0])

    def test_what_only_looks_like_an_address_is_not_one(self) -> None:
        for line in (
            "8.8.8.8 and 1.1.1.1 are public",
            "esphome 10.300.1.1 is not a version anyone ships",
            "v5.10.0.1 is a build number",
            "a 192.168.1 prefix is not an address",
            "110.27.27.81 is somebody else's",
        ):
            with self.subTest(line=line):
                self.assertEqual(g.scan_text("tools/x.py", line), [])

    def test_a_mac_is_counted_against_its_allowance(self) -> None:
        mac = "84:f7:03:aa:bb:cc"
        self.assertEqual(g.scan_text("docs/RUNBOOK.md", f"reserve {mac}"), [])
        twice = f"reserve {mac}\nand {mac}"
        self.assertEqual(len(g.scan_text("docs/RUNBOOK.md", twice)), 1)
        self.assertEqual(
            g.scan_text("tools/x.py", mac), [f"tools/x.py:1: a MAC address {mac}"]
        )
        self.assertEqual(g.scan_text("tools/x.py", "at 12:30:45:00 sharp"), [])

    def test_a_real_home_directory_is_a_finding(self) -> None:
        for line, user in (
            ("/Users/alice/Music/x.mp3", "alice"),
            ("/home/bob/.cache", "bob"),
            ("C:\\Users\\carol\\AppData", "carol"),
            ('"C:\\\\Users\\\\dave\\\\x"', "dave"),
        ):
            with self.subTest(line=line):
                self.assertEqual(
                    g.scan_text("tools/x.py", line),
                    [f"tools/x.py:1: a home directory ({user})"],
                )
        for line in ("/Users/me/x", "/home/runner/work", "C:\\Users\\USERNAME\\"):
            with self.subTest(line=line):
                self.assertEqual(g.scan_text("tools/x.py", line), [])


class GitRepo(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.git("init", "-q")

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.root), *args], check=True)

    def add(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.git("add", "--", rel)


class TestScanTree(GitRepo):
    def test_a_clean_tree_passes(self) -> None:
        self.add("tools/a.py", 'HOST = "192.168.1.20"\n')
        self.assertEqual(g.scan_tree(self.root), [])

    def test_tracked_secrets_are_a_finding(self) -> None:
        self.add("firmware/secrets.yaml", "wifi_ssid: x\n")
        self.assertEqual(
            g.scan_tree(self.root),
            ["firmware/secrets.yaml: tracked — Wi-Fi secrets are never committed"],
        )

    def test_the_sellers_files_must_be_export_ignore(self) -> None:
        self.add("devices.toml", f'[porch]\nhost = "{SELLER}"\n')
        self.add("tracks/tracks.json", "{}\n")
        found = g.scan_tree(self.root)
        self.assertEqual(len(found), 2, found)
        self.assertTrue(all("not export-ignore" in f for f in found), found)
        self.add(".gitattributes", "".join(f"{p} export-ignore\n" for p in g.PERSONAL))
        self.assertEqual(g.scan_tree(self.root), [], "devices.toml is history")

    def test_a_castle_key_in_the_tracked_inventory_is_a_finding(self) -> None:
        self.add(".gitattributes", "".join(f"{p} export-ignore\n" for p in g.PERSONAL))
        self.add("devices.toml", '[porch]\nhost = "192.168.1.20"\nkey = ""\n')
        self.assertEqual(g.scan_tree(self.root), [], "an empty key is no key")
        (self.root / "devices.toml").write_text(
            '[porch]\nhost = "192.168.1.20"\nkey = "pumpkin-42"\n', encoding="utf-8"
        )
        self.assertEqual(g.scan_tree(self.root), [], "the working copy never ships")
        self.git("add", "--", "devices.toml")
        self.assertEqual(
            g.scan_tree(self.root),
            ["devices.toml: tracked with a castle key in it (castle_keys.py)"],
        )

    def test_untracked_files_and_the_guard_itself_are_not_read(self) -> None:
        (self.root / "scratch.txt").write_text(SELLER, encoding="utf-8")
        for path in g.UNREAD:
            self.add(path, f"SELLER_NET = '{SELLER}'\n")
        self.assertEqual(g.scan_tree(self.root), [])

    def test_a_leak_in_shipping_code_is_named_by_line(self) -> None:
        self.add("demo/castle-radio/page.js", f"ok\nfetch('http://{SELLER}/')\n")
        self.assertEqual(
            g.scan_tree(self.root),
            [f"demo/castle-radio/page.js:2: the seller's LAN address {SELLER}"],
        )

    def test_a_binary_file_is_not_read_as_text(self) -> None:
        path = self.root / "audio" / "x.bin"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\0\1\2 192.168.7.40 \0")
        self.git("add", "--", "audio/x.bin")
        self.assertEqual(g.scan_tree(self.root), [])


class TestScanRelease(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dist = Path(self._tmp.name)

    def zip(self, name: str, members: dict[str, bytes]) -> None:
        with zipfile.ZipFile(self.dist / name, "w") as z:
            for member, data in members.items():
                z.writestr(member, data)

    def targz(self, name: str, members: dict[str, bytes]) -> None:
        with tarfile.open(self.dist / name, "w:gz") as t:
            for member, data in members.items():
                info = tarfile.TarInfo(member)
                info.size = len(data)
                t.addfile(info, io.BytesIO(data))

    def test_a_clean_release_passes(self) -> None:
        tag = "v0.1.0"
        for name in release_assets.expected_assets(tag, desktop=True):
            if name.endswith(".zip"):
                self.zip(name, {"studio": b"\x7fELF\0\0 192.168.4.1"})
            elif name.endswith(".tar.gz"):
                self.targz(name, {"Castle Tools.app/Contents/Info.plist": b"<x/>"})
            else:
                (self.dist / name).write_bytes(b"\xe9\0\0" + b"image" * 100)
        self.assertEqual(g.scan_release(self.dist), [])

    def test_the_release_contract_names_nothing_personal(self) -> None:
        for desktop in (False, True):
            names = release_assets.expected_assets("v1.2.3", desktop=desktop)
            self.assertEqual([n for n in names if g._name(n, n)], [])

    def test_personal_files_by_name_at_any_depth(self) -> None:
        (self.dist / "devices.toml").write_text("[x]\n", encoding="utf-8")
        self.zip("core.zip", {"castle/firmware/secrets.yaml": b"x"})
        self.targz("app.tar.gz", {"Castle.app/Contents/tracks/tracks.json": b"{}"})
        found = g.scan_release(self.dist)
        self.assertEqual(
            sorted(found),
            [
                (
                    "app.tar.gz!Castle.app/Contents/tracks/tracks.json: a personal "
                    "file (tracks.json) in a release"
                ),
                (
                    "core.zip!castle/firmware/secrets.yaml: a personal file "
                    "(secrets.yaml) in a release"
                ),
                "devices.toml: a personal file (devices.toml) in a release",
            ],
        )

    def test_text_and_binaries_are_read_inside_archives(self) -> None:
        self.zip("core.zip", {"README.txt": b"open /Users/alice/x\n"})
        self.targz("app.tar.gz", {"bin/app": b"\0\0castle=" + SELLER.encode()})
        (self.dist / "image.bin").write_bytes(b"\xe9\0" + SELLER.encode() + b"\0")
        (self.dist / "latest.json").write_text(
            '{"u": "http://10.44.0.3/"}', encoding="utf-8"
        )
        self.assertEqual(
            sorted(g.scan_release(self.dist)),
            [
                f"app.tar.gz!bin/app: the seller's LAN address {SELLER} in a binary",
                "core.zip!README.txt:1: a home directory (alice)",
                f"image.bin: the seller's LAN address {SELLER} in a binary",
                (
                    "latest.json:1: a private-LAN address that is not a documented "
                    "example 10.44.0.3"
                ),
            ],
        )

    def test_main_fails_on_a_finding_and_passes_without(self) -> None:
        out = io.StringIO()
        with (
            mock.patch.object(g, "scan_tree", return_value=[]),
            contextlib.redirect_stdout(out),
        ):
            self.assertEqual(g.main(["--release", str(self.dist)]), 0)
            (self.dist / "devices.toml").write_text("", encoding="utf-8")
            self.assertEqual(g.main(["--release", str(self.dist)]), 1)
        self.assertIn("ship guard: PASS", out.getvalue())
        self.assertIn("ship guard: FAIL — 1 finding(s)", out.getvalue())


class TestThisRepository(unittest.TestCase):
    """The guard, run: this checkout is what a release tag would ship."""

    def test_the_sellers_files_are_tracked_and_export_ignore(self) -> None:
        tracked = g.tracked()
        present = [p for p in g.PERSONAL if p in tracked]
        self.assertEqual(g.export_ignored(present), set(present))

    def test_nothing_personal_ships(self) -> None:
        self.assertEqual(g.scan_tree(), [])


if __name__ == "__main__":
    unittest.main()
