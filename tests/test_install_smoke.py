"""The install smoke test's offline half: everything tests/install_smoke.py
decides without a network or an installer run — the fresh account, the
folders, the snapshots, the commands a double-click runs, the staged
release the installer is pointed at, and the processes it must take down.
The run itself is .github/workflows/install-smoke.yml."""

from __future__ import annotations

import io
import json
import os
import platform
import shutil
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import install_smoke_env as se  # tools/ on the path, then:

# isort: split
import desktop_env as de
import desktop_install as di
import desktop_release as rel
import install_smoke as smoke
import install_smoke_buyer as sb
import install_smoke_session as session

PY = sys.executable


class TempCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))


class FreshAccount(unittest.TestCase):
    def test_a_mac_buyer_has_none_of_the_developers_tools(self) -> None:
        base = {
            "HOME": "/Users/someone", "USER": "dev", "LANG": "en_US.UTF-8",
            "PATH": "/opt/homebrew/bin:/Users/someone/.cargo/bin:/usr/bin",
            "CARGO_HOME": "/Users/someone/.cargo", "VIRTUAL_ENV": "/x/.venv",
            "CASTLE_HOST": "10.0.0.2", "pythonLocation": "/hostedtoolcache/py",
        }  # fmt: skip
        env = se.buyer_env(base, "Darwin")
        self.assertEqual(env["PATH"], se.POSIX_PATH)
        self.assertEqual(env["HOME"], "/Users/someone")
        for gone in ("CARGO_HOME", "VIRTUAL_ENV", "CASTLE_HOST", "pythonLocation"):
            self.assertNotIn(gone, env)
        self.assertEqual(env["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(env["PYTHONUNBUFFERED"], "1")

    def test_a_windows_buyer_reads_the_environment_case_blind(self) -> None:
        base = {
            "SystemRoot": r"C:\Windows", "LocalAppData": r"C:\Users\user\AppData\Local",
            "Path": r"C:\Rust\bin;C:\Windows\system32", "USERPROFILE": r"C:\Users\user",
            "PSModulePath": r"C:\pwsh\Modules", "ChocolateyInstall": r"C:\choco",
        }  # fmt: skip
        env = se.buyer_env(base, "Windows")
        self.assertEqual(env["SYSTEMROOT"], r"C:\Windows")
        self.assertNotIn("PSModulePath", env)
        self.assertNotIn("ChocolateyInstall", env)
        parts = env["PATH"].split(";")
        self.assertIn(r"C:\Windows\system32", parts)
        self.assertIn(r"C:\Windows\System32\WindowsPowerShell\v1.0", parts)
        self.assertEqual(
            parts[-1], r"C:\Users\user\AppData\Local\Microsoft\WindowsApps"
        )
        self.assertNotIn("Rust", env["PATH"])

    def test_a_throwaway_home_moves_the_whole_profile(self) -> None:
        home = Path("/tmp/zoe")
        mac = se.buyer_env({"HOME": "/Users/someone"}, "Darwin", home)
        self.assertEqual(mac["HOME"], str(home))
        win = se.buyer_env({"USERPROFILE": r"C:\Users\someone"}, "Windows", home)
        self.assertEqual(win["USERPROFILE"], str(home))
        self.assertEqual(win["LOCALAPPDATA"], str(home / "AppData" / "Local"))
        self.assertEqual(win["APPDATA"], str(home / "AppData" / "Roaming"))
        self.assertTrue(win["PATH"].endswith(r"Local\Microsoft\WindowsApps"))

    def test_the_installers_own_folders_for_that_person(self) -> None:
        mac = se.install_dirs({"HOME": "/Users/you"}, "Darwin")
        self.assertEqual(mac.install, Path("/Users/you/Applications/CastleTools"))
        self.assertEqual(se.uv_path({"HOME": "/Users/you"}, "Darwin"),
                         Path("/Users/you/.local/bin/uv"))  # fmt: skip
        env = {"USERPROFILE": "/u/zoe", "LOCALAPPDATA": "/u/zoe/L"}
        win = se.install_dirs(env, "Windows")
        self.assertEqual(win.install, Path("/u/zoe/L/Programs/CastleTools"))
        self.assertEqual(win.data, Path("/u/zoe/L/CastleTools"))
        self.assertEqual(se.uv_path(env, "Windows").name, "uv.exe")
        with self.assertRaises(SystemExit):
            se.profile_home({}, "Darwin")


class Snapshots(TempCase):
    def test_files_and_links_without_walking_into_the_link(self) -> None:
        dirs = de.Dirs(self.tmp / "inst", self.tmp / "data", platform.system())
        (dirs.data / "tracks").mkdir(parents=True)
        (dirs.data / "tracks" / "song.mp3").write_bytes(b"ID3")
        (dirs.app / "tools").mkdir(parents=True)
        (dirs.app / "tools" / "a.py").write_text("x", encoding="utf-8")
        (dirs.app / "tools" / "__pycache__").mkdir()
        (dirs.app / "tools" / "__pycache__" / "a.pyc").write_bytes(b"c")
        self.assertEqual(de.link_radio_data(dirs), "linked")
        snap = se.snapshot(dirs.app, frozenset({"__pycache__"}))
        self.assertEqual(snap["tools/a.py"], (1, snap["tools/a.py"][1]))
        link = de.RADIO_DATA.as_posix()
        self.assertTrue(str(snap[link]).startswith("-> "))
        self.assertFalse(any("song.mp3" in p or "pyc" in p for p in snap))
        self.assertEqual(se.snapshot(self.tmp / "missing"), {})

    def test_diff_names_every_kind_of_change(self) -> None:
        before: se.Snapshot = {"a": (1, 1), "b": (2, 2), "c": "-> x"}
        after: se.Snapshot = {"a": (1, 1), "b": (2, 3), "d": (4, 4)}
        self.assertEqual(se.diff(before, after),
                         ["removed c", "added d", "changed b"])  # fmt: skip
        self.assertEqual(se.diff(before, before), [])


class Commands(unittest.TestCase):
    def test_the_readmes_command_and_the_launchers(self) -> None:
        tree = Path("/dl/halloween_esp-1.0")
        self.assertEqual(
            se.installer_command(tree, "Windows", ["--dry-run"]),
            ["cmd", "/c", str(tree / "installer" / "install.cmd"), "--dry-run"],
        )
        self.assertEqual(se.installer_command(tree, "Darwin", [])[:2],
                         ["sh", str(tree / "installer" / "install.sh")])  # fmt: skip
        win = de.Dirs(Path("/i"), Path("/d"), "Windows")
        self.assertEqual(
            se.launcher_command(win, 9000),
            ["cmd", "/c", str(Path("/i/Castle Tools.cmd")), "--port", "9000",
             "--no-browser"],
        )  # fmt: skip
        mac = de.Dirs(Path("/i"), Path("/d"), "Darwin")
        self.assertEqual(
            se.launcher_command(mac, 1)[1], str(Path("/i/Castle Tools.command"))
        )


class DownloadMarks(TempCase):
    def test_marks_land_where_this_os_keeps_them(self) -> None:
        system = platform.system()
        (self.tmp / "sub").mkdir()
        files = [self.tmp / "a.txt", self.tmp / "sub" / "b é.txt"]
        for f in files:
            f.write_text("x", encoding="utf-8")
        count = se.mark_downloaded(self.tmp, system)
        if system in ("Windows", "Darwin"):
            self.assertEqual(count, 2)
            self.assertTrue(all(se.is_marked(f, system) for f in files))
            fresh = self.tmp / "fresh.txt"
            fresh.write_text("x", encoding="utf-8")
            self.assertFalse(se.is_marked(fresh, system))
        else:  # Linux has no download mark to imitate
            self.assertEqual(count, 0)
            self.assertFalse(se.is_marked(files[0], system))


class Processes(TempCase):
    def test_tee_logs_every_line_and_its_exit(self) -> None:
        log = self.tmp / "logs" / "x.log"
        cmd = [PY, "-c", "print('one'); print('鬼'); raise SystemExit(3)"]
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        with redirect_stdout(io.StringIO()):
            code, said = se.tee(cmd, env, log)
        self.assertEqual((code, said), (3, "one\n鬼"))
        text = log.read_text(encoding="utf-8")
        self.assertIn("鬼", text)
        self.assertIn("[exit 3 after", text)

    def test_tee_kills_a_step_that_waits_forever(self) -> None:
        started = time.monotonic()
        with self.assertRaises(SystemExit) as caught, redirect_stdout(io.StringIO()):
            se.tee([PY, "-c", "import time; time.sleep(60)"], os.environ,
                   self.tmp / "hang.log", timeout=1)  # fmt: skip
        self.assertIn("timed out", str(caught.exception))
        self.assertLess(time.monotonic() - started, 30)

    def test_a_child_is_stopped_whole_and_its_log_kept(self) -> None:
        cmd = [PY, "-u", "-c", "print('up'); import time; time.sleep(60)"]
        child = se.Child(cmd, os.environ, self.tmp / "c.log")
        se.wait_for(lambda: "up" in child.tail(), 20, "the child to talk")
        self.assertTrue(child.alive())
        child.stop()
        self.assertFalse(child.alive())

    def test_wait_for_says_why_it_gave_up(self) -> None:
        se.wait_for(lambda: True, 1, "nothing")
        with self.assertRaises(SystemExit) as dead:
            se.wait_for(lambda: False, 5, "a server", lambda: False, every=0.01)
        self.assertIn("exited first", str(dead.exception))
        with self.assertRaises(SystemExit) as late:
            se.wait_for(lambda: False, 0.05, "a server", every=0.01)
        self.assertIn("not after", str(late.exception))

    def test_check_and_state(self) -> None:
        with redirect_stdout(io.StringIO()) as out:
            se.check(True, "fine")
        self.assertEqual(out.getvalue(), "ok: fine\n")
        with self.assertRaises(SystemExit):
            se.check(False, "broken")
        se.write_state(self.tmp, {"tree": "t", "home": None})
        self.assertEqual(se.read_state(self.tmp), {"tree": "t", "home": None})
        self.assertGreater(se.free_port(), 0)


class StagedRelease(TempCase):
    def bins(self, exe: str = "") -> Path:
        out = self.tmp / "built"
        out.mkdir()
        for name in rel.CORE_BINS:
            (out / f"{name}{exe}").write_bytes(f"#{name}".encode())
        return out

    def test_the_installer_takes_castle_core_from_the_staged_zip(self) -> None:
        path = sb.stage_release(sb.TAG, "aarch64-apple-darwin", self.bins(),
                                self.tmp / "release")  # fmt: skip
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(doc["tag"], sb.TAG)
        self.assertTrue(all(u.startswith("file:") for u in doc["assets"].values()))
        self.assertIn(rel.SUMS, doc["assets"])
        dirs = de.Dirs(self.tmp / "inst", self.tmp / "data", "Darwin")
        args = di.build_parser().parse_args(["--uv", "uv", "--release-json", str(path)])
        said: list[str] = []
        inst = di.Installer(args, dirs, which=lambda _n: None, machine="arm64",
                            say=said.append)  # fmt: skip
        self.addCleanup(shutil.rmtree, inst.scratch, True)
        self.assertEqual(inst.core_bins(self.tmp), "release")
        placed = dirs.app / "core" / "target" / "release" / "studio"
        self.assertEqual(placed.read_bytes(), b"#studio")
        self.assertEqual(inst.found["tag"], sb.TAG)

    def test_a_zip_that_does_not_match_its_sums_is_refused(self) -> None:
        path = sb.stage_release(sb.TAG, "x86_64-pc-windows-msvc", self.bins(".exe"),
                                self.tmp / "release")  # fmt: skip
        doc = json.loads(path.read_text(encoding="utf-8"))
        zip_name = rel.core_asset("x86_64-pc-windows-msvc", sb.TAG)
        Path.from_uri(doc["assets"][zip_name]).write_bytes(b"not the zip")
        dirs = de.Dirs(self.tmp / "inst", self.tmp / "data", "Windows")
        args = di.build_parser().parse_args(["--uv", "uv", "--release-json", str(path)])
        inst = di.Installer(args, dirs, which=lambda _n: None, machine="AMD64",
                            say=lambda _s: None)  # fmt: skip
        self.addCleanup(shutil.rmtree, inst.scratch, True)
        with self.assertRaises(rel.ReleaseError):
            inst.core_bins(self.tmp)


class TheBuyerAndTheCli(TempCase):
    def test_the_log_takes_any_character_on_an_ansi_pipe(self) -> None:
        raw = io.BytesIO()
        pipe = io.TextIOWrapper(raw, encoding="cp1252")
        with mock.patch.object(sys, "stdout", pipe):
            smoke.utf8_output()
            print("█ 鬼", end="")
            pipe.flush()
        self.assertEqual(raw.getvalue().decode("utf-8"), "█ 鬼")

    def test_outside_ci_a_run_needs_a_throwaway_home(self) -> None:
        se.write_state(self.tmp, {"tree": "t", "release_json": "r", "home": None})
        with mock.patch.object(sb, "in_ci", return_value=False):
            with self.assertRaises(SystemExit) as caught:
                sb.Buyer.load(self.tmp)
            self.assertIn("--home", str(caught.exception))
            with self.assertRaises(SystemExit):
                smoke.stage(self.tmp, self.tmp, None, "HEAD")
        home = self.tmp / "home"
        se.write_state(self.tmp, {"tree": "t", "release_json": "r", "home": str(home)})
        b = sb.Buyer.load(self.tmp)
        self.assertEqual(se.profile_home(b.env, b.system), home)
        self.assertEqual(b.release_flags, ["--release-json", "r"])
        self.assertEqual(b.logs, self.tmp / "logs")
        self.assertTrue(str(b.dirs.install).startswith(str(home)))

    def test_evidence_is_copied_beside_the_logs(self) -> None:
        home = self.tmp / "home"
        se.write_state(self.tmp, {"tree": "t", "release_json": "r", "home": str(home)})
        b = sb.Buyer.load(self.tmp)
        b.logs.mkdir()
        b.dirs.install.mkdir(parents=True)
        de.write_json(b.dirs.install_file, {"tag": sb.TAG})
        b.keep_evidence()
        kept = b.logs / f"{b.dirs.install.name}-{b.dirs.install_file.name}"
        self.assertEqual(json.loads(kept.read_text(encoding="utf-8")), {"tag": sb.TAG})

    def test_stage_wants_its_build(self) -> None:
        with redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit):
                smoke.main(["stage", "--work", str(self.tmp)])
            with self.assertRaises(SystemExit):
                smoke.main(["unknown", "--work", str(self.tmp)])

    def test_every_phase_is_reached_from_the_command_line(self) -> None:
        for phase, target in (("install", "install"), ("uninstall", "uninstall")):
            with mock.patch.object(smoke, target) as fn:
                self.assertEqual(smoke.main([phase, "--work", str(self.tmp)]), 0)
                fn.assert_called_once_with(self.tmp.resolve())
        with (
            mock.patch.object(session, "run") as run,
            mock.patch.object(sb.Buyer, "load", return_value="b"),
        ):
            smoke.main(["session", "--work", str(self.tmp)])
            run.assert_called_once_with("b")
        with mock.patch.object(smoke, "stage") as stage:
            smoke.main(["stage", "--work", str(self.tmp), "--core-bins", "c",
                        "--home", "h"])  # fmt: skip
            _work, bins, home, ref = stage.call_args.args
            self.assertEqual((bins.name, home.name, ref), ("c", "h", "HEAD"))


if __name__ == "__main__":
    unittest.main()
