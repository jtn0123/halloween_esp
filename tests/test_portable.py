"""The macOS/Windows seams: portable_fs (lock, atomic rename),
portable_proc (process groups) and progress_process (pipes without select).

Each helper's POSIX half runs for real here. The Windows half cannot run on
this machine, so it is driven through the same code with the platform
switched and the Windows-only pieces (msvcrt, taskkill,
CREATE_NEW_PROCESS_GROUP) stood in for — what each one is CALLED with is
the contract, and that is what these pin.
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import portable_fs
import portable_proc
import progress_process

PY = sys.executable

# A child that takes the lock, says so, holds it, and lets go.
HOLDER = (
    "import sys, time; sys.path.insert(0, sys.argv[1]); import portable_fs\n"
    "from pathlib import Path\n"
    "with portable_fs.exclusive(Path(sys.argv[2])):\n"
    "    print('held', flush=True); time.sleep(float(sys.argv[3]))\n"
)


# A grandchild that appends to a file every 20 ms for a minute: while the
# file keeps growing, it is alive. (Not os.kill(pid, 0) — on Windows that
# TERMINATES the process rather than asking after it.)
BEAT = (
    "import pathlib, sys, time\n"
    "f = pathlib.Path(sys.argv[1])\n"
    "for _ in range(3000):\n"
    "    with f.open('a', encoding='utf-8') as out: out.write('.')\n"
    "    time.sleep(0.02)\n"
)


def parent_of_beat(beat: Path) -> str:
    """A child that starts the beating grandchild, waits for its first beat,
    says so, and sleeps."""
    return (
        "import subprocess, sys, time, pathlib\n"
        f"subprocess.Popen([sys.executable, '-c', {BEAT!r}, {str(beat)!r}])\n"
        f"while not pathlib.Path({str(beat)!r}).exists(): time.sleep(0.01)\n"
        "print('ready', flush=True); time.sleep(60)\n"
    )


def still_beating(beat: Path) -> bool:
    """Has the beat file grown over a quarter second, after a short settle?"""
    time.sleep(0.2)
    before = beat.stat().st_size
    time.sleep(0.25)
    return beat.stat().st_size != before


class TestLock(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)

    def test_a_second_process_waits_for_the_first(self) -> None:
        lock = self.tmp / "tracks.lock"
        child = subprocess.Popen(
            [PY, "-c", HOLDER, str(ROOT / "tools"), str(lock), "0.6"],
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self.addCleanup(child.wait)
        assert child.stdout is not None
        self.assertEqual(child.stdout.readline().strip(), "held")
        started = time.monotonic()
        with portable_fs.exclusive(lock):
            waited = time.monotonic() - started
        child.stdout.close()
        self.assertGreater(waited, 0.3, "the lock did not exclude the holder")

    def test_the_lock_file_is_never_truncated(self) -> None:
        # The Rust twin opens it without truncation; a stray byte stays put.
        lock = self.tmp / "tracks.lock"
        lock.write_bytes(b"x")
        with portable_fs.exclusive(lock):
            pass
        self.assertEqual(lock.read_bytes(), b"x")

    def test_windows_locks_the_shared_span_and_waits_out_lk_lock(self) -> None:
        calls: list[tuple[int, int]] = []
        tries = iter([OSError(36, "deadlock avoided"), None])

        def locking(_fd: int, mode: int, span: int) -> None:
            calls.append((mode, span))
            if mode == 1 and (err := next(tries)) is not None:
                raise err

        fake = SimpleNamespace(LK_UNLCK=0, LK_LOCK=1, locking=locking)
        with (
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.dict(sys.modules, {"msvcrt": fake}),
            portable_fs.exclusive(self.tmp / "tracks.lock"),
        ):
            pass
        span = portable_fs.LOCK_SPAN
        self.assertEqual(calls, [(1, span), (1, span), (0, span)])


class TestReplace(unittest.TestCase):
    def test_a_busy_target_is_retried(self) -> None:
        with (
            mock.patch.object(
                portable_fs.os, "replace", side_effect=[PermissionError, None]
            ) as rep,
            mock.patch.object(portable_fs.time, "sleep") as nap,
        ):
            portable_fs.replace("a", "b")
        self.assertEqual(rep.call_count, 2)
        nap.assert_called_once_with(portable_fs.REPLACE_PAUSE)

    def test_a_target_that_stays_busy_raises(self) -> None:
        with (
            mock.patch.object(portable_fs.os, "replace", side_effect=PermissionError),
            mock.patch.object(portable_fs.time, "sleep"),
            self.assertRaises(PermissionError),
        ):
            portable_fs.replace("a", "b")

    def test_it_really_replaces(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td, "new"), Path(td, "old")
            src.write_text("new", encoding="utf-8")
            dst.write_text("old", encoding="utf-8")
            portable_fs.replace(src, dst)
            self.assertEqual(dst.read_text(encoding="utf-8"), "new")
            self.assertFalse(src.exists())


class TestProcessGroups(unittest.TestCase):
    def test_kill_tree_takes_the_grandchild_too(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            beat = Path(td, "beat")
            child = subprocess.Popen(
                [PY, "-c", parent_of_beat(beat)],
                stdout=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                **portable_proc.group_kwargs(),
            )
            assert child.stdout is not None
            self.assertEqual(child.stdout.readline().strip(), "ready")
            child.stdout.close()
            self.assertTrue(still_beating(beat))
            portable_proc.kill_tree(child)
            child.wait(5)
            self.assertFalse(still_beating(beat), "the grandchild outlived the kill")

    def test_killing_a_finished_child_is_quiet(self) -> None:
        child = subprocess.Popen([PY, "-c", "pass"], **portable_proc.group_kwargs())
        child.wait()
        portable_proc.kill_tree(child)  # must not raise

    def test_posix_starts_a_session(self) -> None:
        with mock.patch.object(sys, "platform", "darwin"):
            self.assertEqual(portable_proc.group_kwargs(), {"start_new_session": True})

    def test_windows_starts_a_process_group(self) -> None:
        with (
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.object(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, create=True
            ),
        ):
            self.assertEqual(portable_proc.group_kwargs(), {"creationflags": 0x200})

    def test_windows_kills_the_tree_with_taskkill(self) -> None:
        process = mock.Mock(pid=4242)
        process.kill.side_effect = OSError("already gone")
        with (
            mock.patch.object(sys, "platform", "win32"),
            mock.patch.object(portable_proc.subprocess, "run") as run,
        ):
            portable_proc.kill_tree(process)
        self.assertEqual(run.call_args[0][0], ["taskkill", "/T", "/F", "/PID", "4242"])
        process.kill.assert_called_once()


class TestUtf8Env(unittest.TestCase):
    def test_the_child_is_told_utf8_and_nothing_else_changes(self) -> None:
        env = portable_proc.utf8_env({"PATH": "/bin", "PYTHONUTF8": "0"})
        self.assertEqual(
            env, {"PATH": "/bin", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        )

    def test_the_default_base_is_this_process_and_is_not_mutated(self) -> None:
        with mock.patch.dict(os.environ, {"CASTLE_T_MARK": "kept"}):
            env = portable_proc.utf8_env()
            self.assertEqual(env["CASTLE_T_MARK"], "kept")
            env["CASTLE_T_MARK"] = "changed"
            self.assertEqual(os.environ["CASTLE_T_MARK"], "kept")


class TestRunProgress(unittest.TestCase):
    def test_both_streams_are_kept_and_streamed(self) -> None:
        script = (
            "import sys; print('one'); print('two\\rthree');"
            " sys.stderr.write('héllo\\n')"
        )
        shown = io.StringIO()
        with redirect_stdout(shown):
            r = progress_process.run_progress([PY, "-c", script], 30)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.replace("\r\n", "\n"), "one\ntwo\rthree\n")
        self.assertEqual(r.stderr.strip(), "héllo")
        lines = [ln for ln in shown.getvalue().splitlines() if ln]
        self.assertIn('CASTLE_PROGRESS {"line": "three"}', lines)
        self.assertEqual(len(lines), 4)

    def test_a_stall_times_out_and_takes_the_grandchild(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            beat = Path(td, "beat")
            cmd = [PY, "-c", parent_of_beat(beat)]
            started = time.monotonic()
            with (
                redirect_stdout(io.StringIO()),
                self.assertRaises(subprocess.TimeoutExpired),
            ):
                progress_process.run_progress(cmd, 1.5)
            self.assertLess(time.monotonic() - started, 10)
            self.assertTrue(beat.exists(), "the grandchild never started")
            self.assertFalse(still_beating(beat), "the grandchild outlived the kill")


if __name__ == "__main__":
    unittest.main()
