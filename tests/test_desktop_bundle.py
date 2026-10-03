"""tools/desktop_bundle.py: the desktop app's castle/ folder, and the contract
between it, the app that reads it (desktop/src-tauri/src/bundle.rs,
setup_cmd.rs, install_tree.rs, runtime.rs, setup_run.rs) and the installer
that app runs.

A bundle is staged the way release.yml stages one: from this tree, a
castle-core zip made by release_assets.zip_core, and a uv archive. The
archive is a stand-in pinned by its own sha256, so nothing is downloaded.
The staged folder is then held to what the Rust side reads: the names, the
bundle.json fields, the installer's flags and the progress lines.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import tempfile
import unittest
import zipfile
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import desktop_bundle as db
import desktop_env as de
import desktop_install as di
import desktop_progress as progress
import desktop_release as rel
import desktop_tree as dt
import release_assets as ra
import ship_guard as g
from desktop_thirdparty import Pin

SRC = ROOT / "desktop" / "src-tauri" / "src"
MAC, WIN = "aarch64-apple-darwin", "x86_64-pc-windows-msvc"
TAG = "v1.2.3"
UV_BODY = b"#!uv stand-in\n"


def rust(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def rust_str(source: str, name: str) -> str:
    m = re.search(rf'const {name}: &str = "([^"]*)";', source)
    if m is None:
        raise AssertionError(f"no const {name}: &str in the Rust source")
    return m.group(1)


def uv_archive(target: str) -> tuple[Pin, bytes]:
    """A uv release archive for `target` holding UV_BODY, and a pin for it."""
    pin = db.UV_PINS[target]
    buf = io.BytesIO()
    if pin.url.endswith(".zip"):
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(pin.members[0], UV_BODY)
    else:
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo(pin.members[0])
            info.size = len(UV_BODY)
            tf.addfile(info, io.BytesIO(UV_BODY))
    body = buf.getvalue()
    return Pin(pin.url, hashlib.sha256(body).hexdigest(), pin.members), body


def core_zip(target: str, out: Path, bins: tuple[str, ...] = ra.CORE_BINS) -> None:
    """The release's castle-core zip for `target`, holding `bins`."""
    built = out / "built"
    built.mkdir(parents=True, exist_ok=True)
    for name in ra.CORE_BINS:
        (built / db.exe(target, name)).write_bytes(f"#!{name}\n".encode())
    ra.zip_core(TAG, target, built, out)
    if bins != ra.CORE_BINS:  # rewrite it without the missing ones
        path = out / ra.core_zip_name(target, TAG)
        wanted = {ra.NOTICES_IN_ZIP, *(db.exe(target, b) for b in bins)}
        with zipfile.ZipFile(path) as zf:
            keep = {i.filename: zf.read(i) for i in zf.infolist()}
        with zipfile.ZipFile(path, "w") as zf:
            for name, data in keep.items():
                if name in wanted:
                    zf.writestr(name, data)


@contextlib.contextmanager
def pinned(target: str) -> Iterator[rel.Fetch]:
    """UV_PINS[target] pinned to the stand-in, and a fetch that serves it."""
    pin, body = uv_archive(target)
    with mock.patch.dict(db.UV_PINS, {target: pin}):
        yield lambda url: body if url == pin.url else b"not this"


def small_tree(root: Path) -> Path:
    """A tree that is not a clone (so it is walked), with a VERSION template."""
    for rel_path, text in (
        ("tools/desktop_install.py", "#"),
        ("installer/VERSION", "$Format:%(describe:tags=true)$\n"),
        ("demo/castle-radio/server.py", "#"),
    ):
        (root / rel_path).parent.mkdir(parents=True, exist_ok=True)
        (root / rel_path).write_text(text, encoding="utf-8")
    return root


class StagedFromThisTree(unittest.TestCase):
    """One macOS bundle staged from this very tree, as release.yml does."""

    castle: Path
    _tmp: tempfile.TemporaryDirectory[str]

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls._tmp.name)
        core_zip(MAC, tmp / "core-zip")
        with pinned(MAC) as fetch:
            cls.castle = db.stage(
                TAG, MAC, tmp / "core-zip", tmp / "sidecar", ROOT, fetch
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def files(self, top: str) -> set[Path]:
        base = self.castle / top
        return {p.relative_to(base) for p in base.rglob("*") if p.is_file()}

    def test_the_folder_is_the_tree_castle_core_and_uv_and_nothing_else(self) -> None:
        self.assertEqual(self.castle, self.castle.parent / db.CASTLE)
        self.assertEqual(
            {p.name for p in self.castle.iterdir()},
            {db.APP, db.BIN, db.UV, db.ABOUT},
        )
        self.assertEqual(
            {p.name for p in self.files(db.BIN)},
            {*ra.CORE_BINS, ra.NOTICES_IN_ZIP},
        )
        self.assertEqual(self.files(db.UV), {Path("uv")})
        self.assertEqual((self.castle / db.UV / "uv").read_bytes(), UV_BODY)
        if os.name != "nt":  # Windows has no executable bit to keep
            for program in (
                self.castle / db.UV / "uv",
                self.castle / db.BIN / "studio",
            ):
                self.assertTrue(os.access(program, os.X_OK), program)

    def test_app_is_the_installers_own_file_list(self) -> None:
        self.assertEqual(self.files(db.APP), set(dt.source_files(ROOT)))
        for personal in g.PERSONAL:
            self.assertFalse((self.castle / db.APP / personal).exists(), personal)

    def test_the_bundled_tree_names_its_release_the_way_a_release_zip_does(
        self,
    ) -> None:
        app = self.castle / db.APP
        self.assertEqual(
            (app / di.VERSION_FILE).read_text(encoding="utf-8"), f"{TAG}\n"
        )
        args = di.build_parser().parse_args(["--uv", "uv"])
        dirs = de.Dirs(app / "i", app / "d", "Darwin")
        inst = di.Installer(args, dirs, which=lambda _n: None, say=lambda _m: None)
        self.assertEqual(inst.release_tag(app), TAG)

    def test_bundle_json_is_what_bundle_rs_reads(self) -> None:
        about = json.loads((self.castle / db.ABOUT).read_text(encoding="utf-8"))
        self.assertEqual(
            about,
            {
                "schema": db.SCHEMA,
                "tag": TAG,
                "target": MAC,
                "uv": db.UV_VERSION,
                "stamp": db.stamp(self.castle),
            },
        )
        read = set(re.findall(r'field\("(\w+)"\)', rust("bundle.rs")))
        self.assertEqual(read, {"tag", "stamp"})
        self.assertLessEqual(read | {"schema"}, set(about))

    def test_the_runtime_the_app_finds_in_it_is_there(self) -> None:
        app = self.castle / db.APP
        self.assertTrue((app / rust_str(rust("setup_cmd.rs"), "INSTALLER")).is_file())
        self.assertTrue((app / rust_str(rust("runtime.rs"), "SERVER")).is_file())
        self.assertTrue((app / "requirements-desktop.lock").is_file())

    def test_the_staged_bundle_ships_nothing_personal(self) -> None:
        """What release.yml's ship guard will read in the .app.tar.gz."""
        with tempfile.TemporaryDirectory() as dist:
            with tarfile.open(Path(dist) / "Castle.app.tar.gz", "w:gz") as tf:
                tf.add(self.castle, "Castle Tools.app/Contents/Resources/castle")
            self.assertEqual(g.scan_release(Path(dist)), [])


class StageTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.repo = small_tree(self.tmp / "repo")

    def stage(self, target: str = WIN, fetch: rel.Fetch | None = None) -> Path:
        if not (self.tmp / "core-zip" / ra.core_zip_name(target, TAG)).exists():
            core_zip(target, self.tmp / "core-zip")
        with pinned(target) as served:
            return db.stage(
                TAG, target, self.tmp / "core-zip", self.tmp / "out", self.repo,
                fetch or served,
            )  # fmt: skip

    def test_windows_carries_uv_exe_and_the_exe_programs(self) -> None:
        castle = self.stage()
        self.assertEqual((castle / db.UV / "uv.exe").read_bytes(), UV_BODY)
        for name in ra.CORE_BINS:
            self.assertTrue((castle / db.BIN / f"{name}.exe").is_file(), name)
        self.assertEqual(
            (castle / db.APP / di.VERSION_FILE).read_text(encoding="utf-8"), f"{TAG}\n"
        )

    def test_the_stamp_follows_every_byte_and_only_the_carried_files(self) -> None:
        castle = self.stage()
        first = db.stamp(castle)
        (castle / db.ABOUT).write_text("{}", encoding="utf-8")
        self.assertEqual(db.stamp(castle), first, "bundle.json is not stamped")
        for changed in (
            castle / db.APP / "tools" / "desktop_install.py",
            castle / db.BIN / "studio.exe",
            castle / db.UV / "uv.exe",
        ):
            with self.subTest(changed=changed.name):
                before = db.stamp(castle)
                changed.write_bytes(changed.read_bytes() + b"!")
                self.assertNotEqual(db.stamp(castle), before)
        (castle / db.APP / "tools" / "moved.py").write_bytes(b"")
        renamed = db.stamp(castle)
        (castle / db.APP / "tools" / "moved.py").rename(castle / db.APP / "moved.py")
        self.assertNotEqual(db.stamp(castle), renamed, "a path is part of the stamp")

    def test_restaging_leaves_nothing_of_the_last_bundle(self) -> None:
        castle = self.stage()
        (castle / "stale.txt").write_text("old", encoding="utf-8")
        (castle / db.APP / "gone.py").write_text("old", encoding="utf-8")
        castle = self.stage()
        self.assertFalse((castle / "stale.txt").exists())
        self.assertFalse((castle / db.APP / "gone.py").exists())

    def test_a_castle_core_zip_without_every_program_is_refused(self) -> None:
        core_zip(WIN, self.tmp / "core-zip", bins=("studio", "analyze_track"))
        with self.assertRaisesRegex(SystemExit, "lacks scene_render"):
            self.stage()

    def test_no_castle_core_zip_is_refused(self) -> None:
        (self.tmp / "core-zip").mkdir()
        with pinned(WIN) as fetch, self.assertRaisesRegex(SystemExit, "no castle-core"):
            db.stage(TAG, WIN, self.tmp / "core-zip", self.tmp / "o", self.repo, fetch)

    def test_a_target_or_tag_the_release_cannot_name_is_refused(self) -> None:
        for tag, target, why in (
            (TAG, "x86_64-unknown-linux-gnu", "not a desktop target"),
            ("1.2.3", WIN, "not a release tag"),
        ):
            with self.subTest(why=why), self.assertRaisesRegex(SystemExit, why):
                db.stage(
                    tag, target, self.tmp, self.tmp / "o", self.repo, rel.http_fetch
                )

    def test_uv_that_is_not_the_pinned_bytes_is_refused(self) -> None:
        with self.assertRaises(rel.ReleaseError):
            self.stage(fetch=lambda _url: b"something else")
        self.assertFalse((self.tmp / "out" / db.CASTLE / db.ABOUT).exists())

    def test_a_uv_archive_without_uv_in_it_is_refused(self) -> None:
        pin, _ = uv_archive(WIN)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("README.md", "no uv here")
        body = buf.getvalue()
        lacking = Pin(pin.url, hashlib.sha256(body).hexdigest(), pin.members)
        with (
            mock.patch.dict(db.UV_PINS, {WIN: lacking}),
            self.assertRaisesRegex(rel.ReleaseError, "has no uv.exe"),
        ):
            db.fetch_uv(WIN, self.tmp / "uv", lambda _u: body, self.tmp)

    def test_main_stages_reports_and_fails_plainly(self) -> None:
        real = db.stage
        core_zip(WIN, self.tmp / "core-zip")
        out, err = io.StringIO(), io.StringIO()
        argv = ["stage", TAG, WIN, str(self.tmp / "core-zip"), str(self.tmp / "o")]
        with pinned(WIN) as fetch:

            def stage(tag: str, target: str, zips: Path, dest: Path) -> Path:
                return real(tag, target, zips, dest, self.repo, fetch)

            with (
                mock.patch.object(db, "stage", stage),
                contextlib.redirect_stdout(out),
                contextlib.redirect_stderr(err),
            ):
                self.assertEqual(db.main(argv), 0)
                self.assertEqual(db.main(["stage", TAG]), 2)
        self.assertIn(f"3 app files, castle-core, uv {db.UV_VERSION}", out.getvalue())
        self.assertIn("desktop_bundle.py stage TAG TARGET", err.getvalue())
        with (
            mock.patch.object(db, "stage", side_effect=rel.ReleaseError("no route")),
            contextlib.redirect_stderr(err),
        ):
            self.assertEqual(db.main(argv), 1)
        self.assertIn("desktop_bundle: no route", err.getvalue())


class ContractTests(unittest.TestCase):
    """The names the app's Rust reads, held equal to the Python that writes."""

    def test_the_bundle_names_are_bundle_rs_names(self) -> None:
        src = rust("bundle.rs")
        for name, value in (
            ("CASTLE", db.CASTLE),
            ("APP", db.APP),
            ("BIN", db.BIN),
            ("UV", db.UV),
            ("ABOUT", db.ABOUT),
        ):
            with self.subTest(name=name):
                self.assertEqual(rust_str(src, name), value)
        self.assertIn(f"pub const SCHEMA: u64 = {db.SCHEMA};", src)
        self.assertEqual(rust_str(rust("install_tree.rs"), "RECORD"), de.INSTALL_FILE)

    def test_the_python_the_app_sets_up_is_the_installers(self) -> None:
        python = rust_str(rust("setup_cmd.rs"), "PYTHON")
        installer = (ROOT / "tools" / "desktop_install.py").read_text(encoding="utf-8")
        self.assertRegex(installer, rf'"--python",\s*"{re.escape(python)}"')
        script = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")
        self.assertIn(f'"$uv" python install {python}\n', script)

    def test_the_progress_markers_are_setup_run_rs_markers(self) -> None:
        src = rust("setup_run.rs")
        self.assertEqual(rust_str(src, "STEP_MARK"), progress.STEP_MARK)
        self.assertEqual(rust_str(src, "FAILED_MARK"), progress.FAILED_MARK)

    def test_every_flag_the_app_passes_the_installer_parses(self) -> None:
        body = (
            rust("setup_cmd.rs").split("pub fn install(", 1)[1].split("\n    }\n", 1)[0]
        )
        flags = set(re.findall(r'"(--[a-z-]+)"', body))
        argv = [
            "--uv", "uv", "--source", "app", "--prefix", "rt", "--data-dir", "d",
            "--core-from", "bin", "--ffmpeg", "download", "--no-launcher",
            "--progress", "--repair",
        ]  # fmt: skip
        self.assertEqual(flags, {a for a in argv if a.startswith("--")})
        args = di.build_parser().parse_args(argv)
        self.assertEqual(
            (args.core_from, args.ffmpeg, args.no_launcher, args.progress, args.repair),
            (Path("bin"), "download", True, True, True),
        )

    def test_the_core_tools_runtime_rs_wants_are_release_programs(self) -> None:
        tools = re.search(r"CORE_TOOLS: \[&str; 2\] = \[([^\]]*)\]", rust("runtime.rs"))
        assert tools is not None
        named = set(re.findall(r'"(\w+)"', tools.group(1)))
        self.assertLessEqual(named, set(ra.CORE_BINS))

    def test_every_target_has_a_uv_pin_from_astral(self) -> None:
        self.assertEqual(set(db.UV_PINS), set(ra.DESKTOP_TARGETS))
        for target, pin in db.UV_PINS.items():
            with self.subTest(target=target):
                self.assertIn(f"/{db.UV_VERSION}/uv-{target}.", pin.url)
                self.assertRegex(pin.sha256, r"^[0-9a-f]{64}$")
                self.assertEqual(
                    pin.members[-1].rsplit("/", 1)[-1], db.exe(target, "uv")
                )


if __name__ == "__main__":
    unittest.main()
