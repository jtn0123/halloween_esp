"""tools/desktop_smoke.py: the desktop app's first-run smoke in release.yml.

The smoke itself needs a built app and the internet, so it runs in the
release job only. What is tested here is everything around that: finding
the app in a bundle, the probes, the waiting, the quitting, and the
verdicts on what each launch logged. A stand-in app (a small Python
server on the two ports) is launched for real. The strings the smoke reads
in the app's log are checked against the Rust that writes them.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import platform
import plistlib
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import desktop_smoke as smoke

SRC = ROOT / "desktop" / "src-tauri" / "src"
SYSTEM = "Windows" if os.name == "nt" else "Darwin"
GOOD = {
    "service": "castle-radio",
    "protocol": 1,
    "core_ready": True,
    "capabilities": dict.fromkeys(smoke.CAPABILITIES, True),
    "checks": [],
}

#: The stand-in app: both servers on the ports the smoke chose, and the log
#: lines a launch writes — a setup only when FAKE_SETUP is set.
FAKE_APP = textwrap.dedent(
    """
    import json, os, threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
        if os.environ.get("FAKE_SETUP"):
            log.write("[t] castle-tools: setup (FirstRun): from the bundle\\n")
            log.write("[t] castle-tools: setup finished in 1s\\n")
        log.write("[t] castle-tools: starting Castle Radio from the app's own "
                  "runtime at /rt: python on port 1\\n")
    BODIES = {"/radio/tools": json.loads(os.environ["FAKE_STATUS"]),
              "/studio/tracks": {"tracks": [], "scenes": []}}
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(BODIES.get(self.path, {})).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a):
            pass
    for var in ("CASTLE_STUDIO_PORT", "CASTLE_DESK_PORT"):
        srv = ThreadingHTTPServer(("127.0.0.1", int(os.environ[var])), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Event().wait()
    """
)


class Served:
    """A real HTTP server on a free port, answering one canned body."""

    def __init__(self, body: bytes) -> None:
        payload = body

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_a: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self.server.server_address[1])
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class Done:
    """A Popen stand-in that has already exited."""

    returncode = 3
    pid = 1

    def poll(self) -> int:
        return self.returncode


class Running(Done):
    def poll(self) -> None:  # type: ignore[override]
        return None


def proc(stand_in: Done) -> subprocess.Popen[bytes]:
    """A stand-in where wait_for wants a process: it only polls."""
    return cast("subprocess.Popen[bytes]", stand_in)


class ProbeTests(unittest.TestCase):
    def test_a_free_port_can_be_bound(self) -> None:
        import socket

        with socket.socket() as s:
            s.bind(("127.0.0.1", smoke.free_port()))

    def test_get_json_takes_only_a_json_object(self) -> None:
        for body, want in (
            (json.dumps(GOOD).encode(), GOOD),
            (b"[1, 2]", None),
            (b"not json", None),
        ):
            served = Served(body)
            self.addCleanup(served.close)
            with self.subTest(body=body[:10]):
                self.assertEqual(smoke.get_json(served.port, "/radio/tools"), want)
        self.assertIsNone(smoke.get_json(smoke.free_port(), "/radio/tools", 0.5))

    def test_the_identities_are_probe_rs_identities(self) -> None:
        self.assertTrue(smoke.is_radio(GOOD))
        self.assertFalse(smoke.is_radio({**GOOD, "protocol": 2}))
        self.assertTrue(smoke.is_studio({"tracks": [], "scenes": []}))
        self.assertFalse(smoke.is_studio({"tracks": []}))

    def test_capable_names_what_a_finished_setup_still_lacks(self) -> None:
        self.assertEqual(smoke.capable(GOOD), [])
        lacking = {**GOOD, "core_ready": False, "capabilities": {"importing": True}}
        self.assertEqual(
            smoke.capable(lacking), ["url_importing", "separation", "core_ready"]
        )
        self.assertEqual(smoke.capable({}), [*smoke.CAPABILITIES, "core_ready"])


class FindingTheApp(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_the_log_is_tauris_app_log_dir(self) -> None:
        win = smoke.log_path("Windows", {"LOCALAPPDATA": r"C:\L"})
        self.assertEqual(win.parts[-3:], (smoke.IDENTIFIER, "logs", smoke.LOG_NAME))
        mac = smoke.log_path("Darwin", {"HOME": "/Users/me"})
        self.assertEqual(
            mac, Path("/Users/me/Library/Logs") / smoke.IDENTIFIER / smoke.LOG_NAME
        )

    def test_mac_starts_the_executable_info_plist_names(self) -> None:
        with self.assertRaisesRegex(smoke.SmokeError, "expected one .app"):
            smoke.mac_app(self.tmp)
        contents = self.tmp / "macos" / "Castle Tools.app" / "Contents"
        contents.mkdir(parents=True)
        (contents / "Info.plist").write_bytes(
            plistlib.dumps({"CFBundleExecutable": "castle-tools"})
        )
        self.assertEqual(smoke.mac_app(self.tmp), contents / "MacOS" / "castle-tools")

    def test_windows_installs_silently_and_starts_what_it_installed(self) -> None:
        local = self.tmp / "local"
        env = {"LOCALAPPDATA": str(local)}
        ran: list[list[str]] = []
        with self.assertRaisesRegex(smoke.SmokeError, "expected one setup"):
            smoke.windows_app(self.tmp, env, lambda cmd, **_k: ran.append(cmd))
        nsis = self.tmp / "nsis"
        nsis.mkdir()
        setup = nsis / "Castle Tools_1.2.3_x64-setup.exe"
        setup.write_bytes(b"")
        with self.assertRaisesRegex(smoke.SmokeError, "installed no Castle Tools"):
            smoke.windows_app(self.tmp, env, lambda cmd, **_k: ran.append(cmd))
        self.assertEqual(ran, [[str(setup), "/S"]])
        folder = local / "Castle Tools"
        folder.mkdir(parents=True)
        for name in ("uninstall.exe", "castle-tools.exe"):
            (folder / name).write_bytes(b"")
        self.assertEqual(
            smoke.windows_app(self.tmp, env, lambda *_a, **_k: None),
            folder / "castle-tools.exe",
        )

    def test_the_log_is_read_from_this_launchs_mark(self) -> None:
        log = self.tmp / "app.log"
        self.assertEqual((smoke.size(log), smoke.read_from(log, 0)), (0, ""))
        # Bytes, as logfile.rs writes them: a text-mode write would make
        # every newline two bytes on Windows and move the offsets.
        log.write_bytes(b"first\nsecond\n")
        self.assertEqual(smoke.read_from(log, 6), "second\n")
        log.write_bytes(b"new\n")  # moved aside at start
        self.assertEqual(smoke.read_from(log, 14), "new\n")


class WaitingAndQuitting(unittest.TestCase):
    def setUp(self) -> None:
        patch = mock.patch.object(smoke, "POLL", 0.01)
        patch.start()
        self.addCleanup(patch.stop)

    def test_wait_for_returns_the_first_good_answer(self) -> None:
        answers: Iterator[smoke.Json | None] = iter([None, {"service": "x"}, GOOD])
        got = smoke.wait_for(
            proc(Running()), lambda: next(answers), smoke.is_radio, 5, "radio"
        )
        self.assertEqual(got, GOOD)

    def test_an_app_that_exits_or_never_answers_fails(self) -> None:
        exited, running = proc(Done()), proc(Running())
        with self.assertRaisesRegex(smoke.SmokeError, r"exited \(3\) before radio"):
            smoke.wait_for(exited, lambda: None, smoke.is_radio, 5, "radio")
        with self.assertRaisesRegex(smoke.SmokeError, "did not answer within 0s"):
            smoke.wait_for(running, lambda: None, smoke.is_radio, 0, "radio")

    def test_a_failed_setup_fails_at_once_with_its_reason(self) -> None:
        at = "[2026-10-03T12:00:09Z] castle-tools:"  # logfile.rs's prefix
        wrote = f"{at} setup (FirstRun): …\n{at} setup failed: no internet\n"
        why = smoke.setup_failure(wrote)
        self.assertEqual(why, f"{at} setup failed: no internet")
        self.assertIsNone(smoke.setup_failure(f"{at} setup finished in 9s\n"))
        running = proc(Running())
        with self.assertRaisesRegex(
            smoke.SmokeError, "radio will not answer: .*no internet"
        ):
            smoke.wait_for(
                running, lambda: None, smoke.is_radio, 60, "radio", lambda: why
            )

    def test_quit_asks_politely_where_it_can(self) -> None:
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(proc.kill)
        smoke.quit_app(proc, SYSTEM)
        self.assertIsNotNone(proc.poll())
        ran: list[list[str]] = []
        smoke.quit_app(proc, "Windows", lambda cmd, **_k: ran.append(cmd))
        self.assertEqual(ran, [], "an app that is gone is not asked again")
        fake = mock.Mock(pid=42)
        fake.poll.return_value = None
        smoke.quit_app(fake, "Windows", lambda cmd, **_k: ran.append(cmd))
        self.assertEqual(ran, [["taskkill", "/PID", "42", "/T", "/F"]])
        fake.wait.side_effect = [subprocess.TimeoutExpired("app", 30), 0]
        smoke.quit_app(fake, "Darwin")
        fake.send_signal.assert_called_once()
        fake.kill.assert_called_once()

    def test_gone_waits_for_the_port_to_stop_answering(self) -> None:
        with mock.patch.object(smoke, "get_json", return_value=None):
            smoke.gone(1)
        with (
            mock.patch.object(smoke, "get_json", return_value=GOOD),
            mock.patch.object(smoke.time, "sleep"),
            self.assertRaisesRegex(smoke.SmokeError, "still answers"),
        ):
            smoke.gone(1, timeout=0)


class Verdicts(unittest.TestCase):
    """judge() on what two launches logged."""

    FIRST = (
        f"{smoke.SETUP_BEGAN}FirstRun)\n{smoke.SETUP_DONE} 300s\n"
        f"{smoke.STARTED_OWN} at /rt\n"
    )
    SECOND = f"{smoke.STARTED_OWN} at /rt\n"

    def judge(self, *launches: tuple[dict[str, object], str]) -> str:
        out = io.StringIO()
        runs = [(status, log, 1.0) for status, log in launches]
        with (
            mock.patch.object(smoke, "start", side_effect=runs) as start,
            contextlib.redirect_stdout(out),
        ):
            smoke.judge(["app"], {}, 60, Path("log"), "Darwin")
        env = start.call_args.args[1]
        self.assertNotEqual(env["CASTLE_STUDIO_PORT"], env["CASTLE_DESK_PORT"])
        return out.getvalue()

    def test_a_set_up_first_launch_and_a_quiet_second_pass(self) -> None:
        said = self.judge((GOOD, self.FIRST), (GOOD, self.SECOND))
        self.assertIn("second launch: both servers answered", said)

    def test_each_way_a_launch_can_fail(self) -> None:
        lacking = {**GOOD, "capabilities": {}}
        for launches, why in (
            (((GOOD, self.SECOND),), "without a finished setup"),
            (((GOOD, self.FIRST.replace(smoke.STARTED_OWN, "x")),), "first launch's"),
            (((lacking, self.FIRST),), "still lacks importing"),
            (((GOOD, self.FIRST), (GOOD, self.FIRST)), "set the runtime up again"),
            (((GOOD, self.FIRST), (GOOD, "")), "second launch's log never"),
        ):
            with self.subTest(why=why), self.assertRaisesRegex(smoke.SmokeError, why):
                self.judge(*launches)

    def test_main_reports_a_failure_with_the_logs_tail(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = Path(tmp.name) / "app.log"
        log.write_text("line one\nthe reason\n", encoding="utf-8")
        err, out = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(smoke, "log_path", return_value=log),
            mock.patch.object(smoke, "mac_app", return_value=Path("/a")),
            mock.patch.object(smoke, "windows_app", return_value=Path("/a")),
            mock.patch.object(smoke, "judge", side_effect=smoke.SmokeError("no")),
            contextlib.redirect_stderr(err),
        ):
            self.assertEqual(smoke.main([tmp.name]), 1)
        self.assertIn("desktop smoke FAILED: no", err.getvalue())
        self.assertIn("the reason", err.getvalue())
        with (
            mock.patch.object(smoke, "mac_app", return_value=Path("/a")),
            mock.patch.object(smoke, "windows_app", return_value=Path("/a")),
            mock.patch.object(smoke, "judge") as judge,
            contextlib.redirect_stdout(out),
        ):
            self.assertEqual(smoke.main([tmp.name, "--timeout", "5"]), 0)
        self.assertEqual(judge.call_args.args[0], [str(Path("/a"))])
        self.assertEqual(judge.call_args.args[2], 5.0)
        self.assertIn("the second reused it", out.getvalue())


class AStandInApp(unittest.TestCase):
    """start() and judge() against a real process on real ports."""

    def test_two_launches_of_an_app_that_sets_up_once(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        app = Path(tmp.name) / "app.py"
        app.write_text(FAKE_APP, encoding="utf-8")
        log = Path(tmp.name) / "app.log"
        env = {**os.environ, "FAKE_LOG": str(log), "FAKE_STATUS": json.dumps(GOOD)}
        launches: list[str] = []
        real = smoke.start

        def start(cmd: list[str], env: dict[str, str], *rest: object) -> object:
            env = {**env, "FAKE_SETUP": "" if launches else "1"}
            launches.append(cmd[0])
            return real(cmd, env, *rest)  # type: ignore[arg-type]

        with (
            mock.patch.object(smoke, "POLL", 0.05),
            mock.patch.object(smoke, "start", start),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            smoke.judge([sys.executable, str(app)], env, 30, log, platform.system())
        self.assertEqual(len(launches), 2)
        text = log.read_text(encoding="utf-8")
        self.assertEqual(text.count(smoke.SETUP_DONE), 1)
        self.assertEqual(text.count(smoke.STARTED_OWN), 2)


class TheAppWritesWhatTheSmokeReads(unittest.TestCase):
    def rust(self, name: str) -> str:
        return (SRC / name).read_text(encoding="utf-8")

    def test_the_log_lines_are_the_apps(self) -> None:
        setup = self.rust("setup.rs")
        self.assertIn(f'"{smoke.SETUP_BEGAN}{{:?}}): ', setup)
        self.assertIn(f'"{smoke.SETUP_DONE} {{}}s"', setup)
        self.assertIn(f'"{smoke.SETUP_FAILED}{{message}}"', setup)
        self.assertIn(
            '"starting {} from the {} at {}: {} on port {}"', self.rust("supervisor.rs")
        )
        self.assertIn('Service::Radio => "Castle Radio"', self.rust("service.rs"))
        self.assertIn(
            'Source::Bundled => "app\'s own runtime"', self.rust("runtime.rs")
        )
        self.assertEqual(
            smoke.STARTED_OWN, "starting Castle Radio from the app's own runtime"
        )
        self.assertIn(f'"{smoke.LOG_NAME}"', self.rust("lib.rs"))

    def test_the_identifier_and_the_port_variables_are_the_apps(self) -> None:
        conf = json.loads(
            (ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(conf["identifier"], smoke.IDENTIFIER)
        service = self.rust("service.rs")
        for var in ("CASTLE_STUDIO_PORT", "CASTLE_DESK_PORT"):
            self.assertIn(f'=> "{var}"', service)

    def test_release_yml_stages_the_bundle_and_smokes_the_app(self) -> None:
        job = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        bundle = job.index("python tools/desktop_bundle.py stage")
        build = job.index("- name: tauri build")
        run = job.index("python tools/desktop_smoke.py")
        stage = job.index("release_assets.py stage-desktop")
        self.assertLess(bundle, build)
        self.assertLess(build, run)
        self.assertLess(run, stage)


if __name__ == "__main__":
    unittest.main()
