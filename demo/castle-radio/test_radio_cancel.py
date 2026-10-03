"""Cancel means cancel, at every stage of a preparation.

grade report 2026-09-24 B6: the queue checked `cancelled` once, before the
download. A Cancel that arrived while the song was being analysed, while its
light show was built, or as the catalog was written was answered "ok" — and
the job ran on to Ready, with its catalog row written and `cancelled: True`
still on the record.
"""

import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import job_progress
import radio_jobs
import rich_show

#: A child that would outlive any test: only a kill ends it in time.
SLEEPER = [sys.executable, "-c", "import time; time.sleep(30)"]


class CancelEveryStageTests(unittest.TestCase):
    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.library = tmp / "tracks"
        self.library.mkdir()
        self.catalog = tmp / "catalog.json"
        for name, value in (
            ("LIBRARY", self.library),
            ("DATA", tmp),
            ("CATALOG", self.catalog),
        ):
            self.enterContext(patch.object(radio_jobs, name, value))
        self.enterContext(patch.dict(radio_jobs.HANDLES, {}, clear=True))
        self.enterContext(patch.dict(radio_jobs.JOBS, {}, clear=True))
        self.job = {"id": "radio_c", "source": "https://e.com/c", "done": False}
        radio_jobs.JOBS["radio_c"] = self.job
        # The pool's future for a job that is already running: it cannot be
        # cancelled there, so only the job itself can notice.
        running = MagicMock()
        running.cancel.return_value = False
        radio_jobs.HANDLES["radio_c"] = (running, threading.Event())
        self.enterContext(patch.object(radio_jobs, "run_tool", self.fake_import))
        self.shows = self.enterContext(patch.object(rich_show, "prepare"))

    def fake_import(self, job, script, args, timeout, extra_env=None):
        """What import_track.py leaves behind: the audio and its manifest row."""
        (self.library / "radio_c.mp3").write_bytes(b"ID3")
        (self.library / "tracks.json").write_text(
            json.dumps({"radio_c": {"source": "https://e.com/c"}}), encoding="utf-8"
        )

    def prepare(self, split=False):
        radio_jobs.prepare(self.job, self.job["source"], "", split, "mp3")

    def assert_cancelled_with_no_song(self):
        self.assertEqual(
            (self.job["phase"], self.job["done"], self.job["error"]),
            ("Cancelled", True, None),
        )
        self.assertNotIn("result", self.job)
        rows = (
            json.loads(self.catalog.read_text(encoding="utf-8"))
            if self.catalog.exists()
            else []
        )
        self.assertEqual([r for r in rows if r["key"] == "radio_c"], [])
        self.assertNotIn("radio_c", radio_jobs.HANDLES)

    def test_a_cancel_during_analysis_never_reaches_ready(self):
        def analyse(path, sensitivity, stereo, run=None):
            radio_jobs.cancel("radio_c")  # the listener presses Cancel now
            return 88200, {"onset_low": [[0.5, 0.8]]}

        with patch.object(radio_jobs, "crate_analysis", analyse):
            self.prepare()
        self.assert_cancelled_with_no_song()
        self.shows.assert_not_called()

    def test_a_cancel_while_the_light_show_is_built_never_reaches_ready(self):
        self.shows.side_effect = lambda *a: radio_jobs.cancel("radio_c")
        with patch.object(
            radio_jobs, "crate_analysis", return_value=(88200, {"onset_low": []})
        ):
            self.prepare()
        self.assert_cancelled_with_no_song()

    def test_the_analysis_child_itself_is_killed_not_waited_for(self):
        """The runner the job hands the analysis is the one Cancel stops: a
        child that would sleep 30 s is gone as soon as Cancel lands."""

        def analyse(path, sensitivity, stereo, run=None):
            assert run is not None, "the analysis was given no stoppable runner"
            run(SLEEPER, capture_output=True, check=False)
            raise AssertionError("the child ran to the end")

        threading.Timer(0.3, radio_jobs.cancel, ("radio_c",)).start()
        started = time.monotonic()
        with patch.object(radio_jobs, "crate_analysis", analyse):
            self.prepare()
        self.assertLess(time.monotonic() - started, 10)
        self.assert_cancelled_with_no_song()

    def test_the_light_show_builder_gets_the_same_runner(self):
        """The show's children (analyze_track again, esbuild, node) run
        through the very runner the test above proves Cancel can stop."""
        given = []

        def analyse(path, sensitivity, stereo, run=None):
            given.append(run)
            return 88200, {"onset_low": []}

        with patch.object(radio_jobs, "crate_analysis", analyse):
            self.prepare()
        self.assertEqual(self.job["phase"], "Ready in demo")
        self.assertIsNotNone(given[0])
        self.assertIs(self.shows.call_args.args[2], given[0])

    def test_an_uncancelled_job_still_writes_its_row(self):
        with patch.object(
            radio_jobs, "crate_analysis", return_value=(88200, {"onset_low": []})
        ):
            self.prepare()
        self.assertEqual((self.job["phase"], self.job["done"]), ("Ready in demo", True))
        rows = json.loads(self.catalog.read_text(encoding="utf-8"))
        self.assertEqual([r["key"] for r in rows], ["radio_c"])

    def test_a_cancel_after_ready_is_refused_not_half_applied(self):
        with patch.object(
            radio_jobs, "crate_analysis", return_value=(88200, {"onset_low": []})
        ):
            self.prepare()
        with self.assertRaises(ValueError):
            radio_jobs.cancel("radio_c")
        self.assertNotIn("cancelled", self.job)


class RunnerTests(unittest.TestCase):
    def test_it_answers_like_subprocess_run(self):
        echo = [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"]
        done = job_progress.runner()(echo, input=b"boo", capture_output=True)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, b"BOO"))
        text = job_progress.runner()(
            echo, input="hi", capture_output=True, text=True, encoding="utf-8"
        )
        self.assertEqual(text.stdout.strip(), "HI")
        fail = [sys.executable, "-c", "raise SystemExit(3)"]
        self.assertEqual(job_progress.runner()(fail).returncode, 3)
        with self.assertRaises(job_progress.subprocess.CalledProcessError):
            job_progress.runner()(fail, check=True)

    def test_stop_kills_the_child_and_raises_cancelled(self):
        stop = threading.Event()
        threading.Timer(0.3, stop.set).start()
        started = time.monotonic()
        with self.assertRaises(job_progress.Cancelled):
            job_progress.runner(stop)(SLEEPER, capture_output=True)
        self.assertLess(time.monotonic() - started, 10)

    def test_a_child_past_its_timeout_is_killed_and_reported(self):
        with self.assertRaises(job_progress.ToolFailed) as caught:
            job_progress.runner(timeout=0.3)(SLEEPER)
        self.assertNotIsInstance(caught.exception, job_progress.Cancelled)
        self.assertEqual(str(caught.exception), job_progress.ir.STALLED)
        self.assertIn("timed out", caught.exception.log)


class RichShowPassesTheRunnerTests(unittest.TestCase):
    def test_a_cancel_from_the_runner_is_not_reported_as_a_failed_show(self):
        """rich_show turns a tool's failure into "Could not prepare the light
        show" — a Cancelled must come through as itself instead."""
        library = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (library / "radio_r.mp3").write_bytes(b"ID3")
        calls = []

        def run(args, **kwargs):
            calls.append(Path(args[0]).name)
            raise job_progress.Cancelled("Cancelled")

        with self.assertRaises(job_progress.Cancelled):
            rich_show.prepare(library, {"key": "radio_r"}, run)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("analyze_track"), calls)


if __name__ == "__main__":
    unittest.main()
