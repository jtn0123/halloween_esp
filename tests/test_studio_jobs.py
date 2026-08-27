"""Does a background import report honestly on itself while it runs?

The job runner exists so a long download stops looking like a hang, which
means the only things worth testing are the ones a waiting person sees: a job
id that comes back immediately, a percentage that tracks yt-dlp's own output,
a phase that moves in the right order, and — when it goes wrong — an error
sentence rather than a wall of shell.

Nothing here touches the network. Progress lines are fed in as text, and the
subprocesses are `true`, `false`, and short python one-liners.
"""

from __future__ import annotations

import sys
import threading
import time
import unittest
import warnings
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import studio_jobs as sj


def setUpModule() -> None:
    """Quieten the ResourceWarning these tests provoke by running many jobs.

    `JobRunner._run` never closes the Popen's stdout — the interpreter closes
    it when the object is collected, which is prompt under refcounting but
    loud under warnings. It is hygiene, not a leak, and silencing it here
    keeps the suite's output readable rather than papering over a failure.
    """
    warnings.filterwarnings("ignore", category=ResourceWarning)


def wait_done(job: sj.Job, timeout: float = 10.0) -> sj.Job:
    """Spin until the runner's thread finishes, so assertions are not racy."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if job.phase in ("done", "failed"):
            return job
        time.sleep(0.005)
    raise AssertionError(f"job stuck in phase {job.phase!r}")


class TestProgressRegex(unittest.TestCase):
    """The percentage comes from parsing yt-dlp's stdout, so the parse matters.

    These are real lines, copied verbatim in shape. yt-dlp varies the spacing
    and prefixes the size with `~` when it is only an estimate, and a regex
    that only handles the tidy case silently reports 0% forever.
    """

    def test_standard_line(self) -> None:
        m = sj._PROGRESS.search(
            "[download]  41.8% of    2.39MiB at   17.12MiB/s ETA 00:03"
        )
        assert m is not None
        self.assertEqual(m.group("pct"), "41.8")
        self.assertEqual(m.group("size"), "2.39MiB")
        self.assertEqual(m.group("rate"), "17.12MiB/s")
        self.assertEqual(m.group("eta"), "00:03")

    def test_estimated_size_with_tilde(self) -> None:
        """`~` means yt-dlp is guessing the total; it must not break the parse."""
        m = sj._PROGRESS.search(
            "[download]   0.0% of ~  12.34MiB at  Unknown B/s ETA Unknown"
        )
        assert m is not None
        self.assertEqual(m.group("pct"), "0.0")
        self.assertEqual(m.group("size"), "12.34MiB")

    def test_rate_and_eta_are_optional(self) -> None:
        """The final line drops the ETA once there is nothing left to wait for."""
        m = sj._PROGRESS.search("[download] 100% of 2.39MiB")
        assert m is not None
        self.assertEqual(m.group("pct"), "100")
        self.assertIsNone(m.group("rate"))
        self.assertIsNone(m.group("eta"))

    def test_ignores_unrelated_output(self) -> None:
        self.assertIsNone(sj._PROGRESS.search("[youtube] Extracting URL: https://x"))
        self.assertIsNone(sj._PROGRESS.search("[download] Destination: foo.webm"))


class TestInterpret(unittest.TestCase):
    """Phase is the coarse story: queued -> fetching -> converting -> done.

    The browser shows the phase as a word, so a phase that never leaves
    "queued" is indistinguishable from a stalled import.
    """

    def test_starts_queued(self) -> None:
        self.assertEqual(sj.Job(id="x").phase, "queued")

    def test_a_progress_line_moves_it_to_fetching(self) -> None:
        job = sj.Job(id="x")
        sj.JobRunner._interpret(
            job, "[download]  41.8% of 2.39MiB at 17.12MiB/s ETA 00:03"
        )
        self.assertEqual(job.phase, "fetching")
        self.assertAlmostEqual(job.percent, 41.8)
        self.assertEqual(job.detail, "2.39MiB at 17.12MiB/s, 00:03 left")

    def test_detail_omits_missing_rate_and_eta(self) -> None:
        job = sj.Job(id="x")
        sj.JobRunner._interpret(job, "[download] 100% of 2.39MiB")
        self.assertEqual(job.detail, "2.39MiB")

    def test_audio_extraction_moves_it_to_converting(self) -> None:
        for line in (
            "[ExtractAudio] Destination: clip.mp3",
            '[ffmpeg] Merging formats into "clip.mkv"',
        ):
            job = sj.Job(id="x")
            sj.JobRunner._interpret(job, line)
            self.assertEqual(job.phase, "converting", line)
            self.assertEqual(job.percent, 100.0)
            self.assertEqual(job.detail, "extracting audio")

    def test_import_line_moves_it_to_analysing(self) -> None:
        job = sj.Job(id="x")
        sj.JobRunner._interpret(job, "imported chant  24.0s  onsets 40/88/31")
        self.assertEqual(job.phase, "analysing")
        self.assertEqual(job.detail, "detecting onsets")

    def test_full_transition_order(self) -> None:
        """The sequence a real import walks, driven by its own output."""
        job = sj.Job(id="x")
        seen = [job.phase]
        for line in (
            "[youtube] Extracting URL: https://example.invalid/v",
            "[download]   5.0% of 2.39MiB at 1.00MiB/s ETA 00:03",
            "[download] 100% of 2.39MiB",
            "[ExtractAudio] Destination: clip.mp3",
            "imported clip  24.0s",
        ):
            sj.JobRunner._interpret(job, line)
            if job.phase != seen[-1]:
                seen.append(job.phase)
        self.assertEqual(seen, ["queued", "fetching", "converting", "analysing"])

    def test_noise_does_not_change_phase(self) -> None:
        job = sj.Job(id="x")
        job.phase = "fetching"
        sj.JobRunner._interpret(job, "WARNING: something cosmetic")
        self.assertEqual(job.phase, "fetching")


class TestExplain(unittest.TestCase):
    """Raw yt-dlp output in a UI is a failure of nerve; check the translation.

    Each of these is a real failure someone will hit — a private link, a link
    pasted from a members-only stream, a typo'd URL — and each should come back
    as a sentence, not as `ERROR: [youtube] abc: Private video. Sign in…`.
    """

    CASES: ClassVar[list] = [
        (
            "ERROR: [youtube] abc: Private video. Sign in if you've been granted access",
            "That video is private.",
        ),
        ("ERROR: [youtube] abc: Video unavailable", "That video is unavailable."),
        (
            "ERROR: [youtube] abc: Sign in to confirm you're not a bot",
            "That video needs a signed-in account.",
        ),
        (
            "ERROR: [youtube] abc: Join this channel: members-only content",
            "That video is members-only.",
        ),
        ("ERROR: 'not a link' is not a valid URL", "That does not look like a link."),
        (
            "ERROR: Unsupported URL: https://example.invalid/thing",
            "Nothing here knows how to read that link.",
        ),
        (
            "ERROR: unable to download: HTTP Error 404: Not Found",
            "That link is a dead end (404).",
        ),
        ("no audio file was produced", "The download produced no audio."),
        (
            "ERROR: Requested format is not available",
            "No audio-only format was offered for that video.",
        ),
    ]

    def test_known_failures_become_sentences(self) -> None:
        for raw, friendly in self.CASES:
            self.assertEqual(
                sj.JobRunner._explain(["[youtube] abc", raw]),
                friendly,
                f"not translated: {raw!r}",
            )

    def test_match_is_case_insensitive(self) -> None:
        """yt-dlp's wording drifts between releases; matching on case is brittle."""
        self.assertEqual(
            sj.JobRunner._explain(["error: PRIVATE VIDEO"]), "That video is private."
        )

    def test_unknown_failure_falls_back_to_the_last_error_line(self) -> None:
        log = [
            "[youtube] fine",
            "ERROR: first thing went wrong",
            "still going",
            "ERROR: the thing that actually killed it",
        ]
        self.assertEqual(
            sj.JobRunner._explain(log), "the thing that actually killed it"
        )

    def test_fallback_keeps_the_whole_line_when_there_is_no_prefix(self) -> None:
        self.assertEqual(
            sj.JobRunner._explain(["something ERROR happened"]),
            "something ERROR happened",
        )

    def test_empty_log_explains_nothing(self) -> None:
        self.assertEqual(sj.JobRunner._explain([]), "")

    def test_log_with_no_error_explains_nothing(self) -> None:
        self.assertEqual(sj.JobRunner._explain(["[download] 100% of 2MiB"]), "")


class TestAsDict(unittest.TestCase):
    """The dict is the wire format the polling page reads; its shape is a contract."""

    def test_shape(self) -> None:
        d = sj.Job(id="abc123").as_dict()
        self.assertEqual(
            set(d), {"id", "phase", "percent", "detail", "error", "done", "log"}
        )
        self.assertEqual(d["id"], "abc123")
        self.assertEqual(d["phase"], "queued")
        self.assertFalse(d["done"])

    def test_percent_is_rounded_for_display(self) -> None:
        job = sj.Job(id="x")
        job.percent = 41.8333333
        self.assertEqual(job.as_dict()["percent"], 41.8)

    def test_done_is_true_for_both_terminal_phases(self) -> None:
        for phase in ("done", "failed"):
            job = sj.Job(id="x")
            job.phase = phase
            self.assertTrue(job.as_dict()["done"], phase)
        for phase in ("queued", "fetching", "converting", "analysing"):
            job = sj.Job(id="x")
            job.phase = phase  # type: ignore[assignment]  # loop var widens to str
            self.assertFalse(job.as_dict()["done"], phase)

    def test_log_is_capped_to_the_tail(self) -> None:
        """yt-dlp is chatty; shipping all of it to the browser on every poll
        would make the progress bar the most expensive part of the import."""
        job = sj.Job(id="x")
        job.log = [f"line {i}" for i in range(500)]
        tail = job.as_dict()["log"]
        self.assertEqual(len(tail), 40)
        self.assertEqual(tail[-1], "line 499")
        self.assertEqual(tail[0], "line 460")


class TestJobRunner(unittest.TestCase):
    """The runner drives real subprocesses — harmless ones, and never yt-dlp."""

    def setUp(self) -> None:
        self.runner = sj.JobRunner()

    def test_start_returns_immediately_with_an_id(self) -> None:
        """The whole point: the HTTP request must not wait for the download."""
        t0 = time.monotonic()
        job = self.runner.start(["sleep", "1"])
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 0.3, "start() blocked on the child process")
        self.assertEqual(len(job.id), 12)
        self.assertIn(job.phase, ("queued", "fetching"))
        self.assertIs(self.runner.get(job.id), job)

    def test_get_of_an_unknown_id_is_none(self) -> None:
        self.assertIsNone(self.runner.get("nosuchjob"))

    def test_a_gated_job_waits_for_the_studio_lock(self) -> None:
        """The studio's sync encodes and the background jobs take turns:
        while the gate is held the job sits queued, and it runs the moment
        the gate is released."""
        gate = threading.Lock()
        runner = sj.JobRunner(gate=gate)
        with gate:
            job = runner.start(["true"])
            time.sleep(0.15)
            self.assertEqual(job.phase, "queued")
        self.assertEqual(wait_done(job).phase, "done")

    def test_a_gated_job_holds_the_gate_while_its_child_runs(self) -> None:
        gate = threading.Lock()
        runner = sj.JobRunner(gate=gate)
        job = runner.start(["sleep", "0.3"])
        time.sleep(0.1)
        self.assertFalse(gate.acquire(blocking=False), "gate free mid-job")
        wait_done(job)
        self.assertTrue(gate.acquire(blocking=False))
        gate.release()

    def test_no_gate_is_the_default(self) -> None:
        self.assertIsNone(sj.JobRunner()._gate)

    def test_a_successful_command_ends_done_at_100(self) -> None:
        job = wait_done(self.runner.start(["true"]))
        self.assertEqual(job.phase, "done")
        self.assertEqual(job.percent, 100.0)
        self.assertEqual(job.error, "")

    def test_a_failing_command_ends_failed_with_a_reason(self) -> None:
        """A failure with nothing to explain still needs a non-empty message —
        an empty error box tells the person nothing at all."""
        job = wait_done(self.runner.start(["false"]))
        self.assertEqual(job.phase, "failed")
        self.assertTrue(job.error, "failed job carried no error text")
        self.assertIn("exit 1", job.error)

    def test_a_failing_command_gets_its_output_translated(self) -> None:
        job = wait_done(
            self.runner.start(
                [
                    sys.executable,
                    "-c",
                    "import sys; print('ERROR: [youtube] x: Private video'); sys.exit(1)",
                ]
            )
        )
        self.assertEqual(job.phase, "failed")
        self.assertEqual(job.error, "That video is private.")
        self.assertIn("ERROR: [youtube] x: Private video", job.log)

    def test_a_missing_binary_fails_instead_of_raising(self) -> None:
        """The thread has nobody to raise to, so an OSError must land on the job."""
        job = wait_done(self.runner.start(["/nonexistent/definitely-not-here"]))
        self.assertEqual(job.phase, "failed")
        self.assertTrue(job.error)

    def test_progress_from_a_real_child_reaches_the_job(self) -> None:
        """End to end through the pipe: printed line -> parsed percent."""
        job = wait_done(
            self.runner.start(
                [
                    sys.executable,
                    "-c",
                    "print('[download]  41.8% of 2.39MiB at 1.0MiB/s ETA 00:03')",
                ]
            )
        )
        self.assertEqual(job.phase, "done")  # finished after the line
        self.assertEqual(
            job.log, ["[download]  41.8% of 2.39MiB at 1.0MiB/s ETA 00:03"]
        )

    def test_blank_lines_are_not_logged(self) -> None:
        job = wait_done(
            self.runner.start([sys.executable, "-c", "print(); print('kept'); print()"])
        )
        self.assertEqual(job.log, ["kept"])

    def test_the_job_map_is_pruned(self) -> None:
        """A long studio session must not accumulate every import forever."""
        for _ in range(40):
            wait_done(self.runner.start(["true"]))
        self.assertEqual(len(self.runner._jobs), 40)
        newest = self.runner.start(["true"])
        self.assertLess(len(self.runner._jobs), 41, "finished jobs were never pruned")
        self.assertIsNotNone(
            self.runner.get(newest.id), "pruning threw away the job that just started"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestExplainBeyondYtDlp(unittest.TestCase):
    """Judge B, JB1-10: ffmpeg and demucs failures reached the operator as
    "import failed (exit 1)" or as a raw traceback with an absolute path."""

    TRACEBACK: ClassVar[list] = [
        "Traceback (most recent call last):",
        '  File "tools/import_track.py", line 450, in _import',
        "    x = ana.load_audio(out)",
        (
            "subprocess.CalledProcessError: Command '['ffmpeg', '-v', 'quiet', "
            "'-i', '/private/tmp/x/jb_drop.mp3']' returned non-zero exit status 1."
        ),
    ]

    def test_a_traceback_names_the_program_that_failed(self) -> None:
        self.assertEqual(
            sj.JobRunner._explain(self.TRACEBACK), "ffmpeg failed (exit 1)"
        )

    def test_the_last_meaningful_line_is_the_fallback(self) -> None:
        """import_track's own SystemExit sentences are already for a person;
        they must come through instead of the generic exit code."""
        log = [
            "fetching x",
            "[download] 100% of 1MiB",
            (
                "clip.wav doesn't look like playable audio — ffmpeg could not "
                "convert it (exit 1)"
            ),
        ]
        self.assertEqual(sj.JobRunner._explain(log), log[-1])

    def test_paths_come_back_as_basenames(self) -> None:
        self.assertEqual(
            sj.JobRunner._explain(["no such file: /private/tmp/abc/_upload/jb.wav"]),
            "no such file: jb.wav",
        )
        self.assertEqual(
            sj.basenames("see https://youtu.be/abc/def then /a/b/c.wav"),
            "see https://youtu.be/abc/def then c.wav",
        )

    def test_missing_demucs_and_ffmpeg_are_sentences(self) -> None:
        self.assertIn(
            "Demucs is not installed",
            sj.JobRunner._explain(["ModuleNotFoundError: No module named 'demucs'"]),
        )
        self.assertIn(
            "ffmpeg is not installed",
            sj.JobRunner._explain(
                ["FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'"]
            ),
        )

    def test_reason_takes_the_sync_paths_whole_output(self) -> None:
        text = "x\n\n" + "\n".join(self.TRACEBACK) + "\n"
        self.assertEqual(sj.reason(text), "ffmpeg failed (exit 1)")
        self.assertEqual(sj.reason(""), "")
