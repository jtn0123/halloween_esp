"""A failed import reads as one sentence the castle's owner can act on.

The radio's jobs end the way the Rust studio's do (studio_jobs.rs): the
sentence is tools/import_reason.py's — the importer's own last line when it
printed one — the child's words stay behind Details (`error_detail`), and a
link that failed because the downloader is old carries the one-button fix
(`action`). A Cancel is still "Cancelled", never a failure (grade report
2026-09-24 B6), and a line relayed from yt-dlp while it ran (grade report
2026-09-24 B2) is a quote, never the verdict.
"""

import errno
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import radio_env  # noqa: F401 — the sandbox first, then tools/ on the path

# isort: split
import import_reason as ir
import import_routes
import job_progress
import radio_jobs
import rich_show
from job_progress import ToolFailed, failure, run

TOO_LONG = (
    "song.wav is 2:00:00 long, and Castle Tools imports up to 15 minutes — "
    "set a start and length to import just part of it, or choose a shorter song."
)


def child(*lines, code=1):
    """A child that prints `lines` (a str to stdout, a 1-tuple to stderr)
    and exits with `code`."""
    body = "import sys\n"
    for line in lines:
        out = "sys.stderr" if isinstance(line, tuple) else "sys.stdout"
        text = line[0] if isinstance(line, tuple) else line
        body += f"print({text!r}, file={out}, flush=True)\n"
    return [sys.executable, "-c", body + f"raise SystemExit({code})"]


class ChildFailureTests(unittest.TestCase):
    def ran(self, args):
        with self.assertRaises(ToolFailed) as caught:
            run(args, 30, "import", lambda **v: None)
        return caught.exception

    def test_the_importers_last_sentence_is_what_the_owner_reads(self):
        relayed = (
            'CASTLE_PROGRESS {"line": "WARNING: [youtube] Unable to extract nsig"}'
        )
        exc = self.ran(child(relayed, ("    ffprobe said: 7200.0",), (TOO_LONG,)))
        self.assertEqual(str(exc), TOO_LONG)
        # The relayed line is kept, as a quote; the exit is a log fact.
        self.assertIn("    WARNING: [youtube] Unable to extract nsig\n", exc.log)
        self.assertNotIn("CASTLE_PROGRESS", exc.log)
        self.assertTrue(exc.log.endswith("(the job ended with exit 1)\n"))

    def test_a_relayed_warning_is_never_the_reason_a_later_step_failed(self):
        relayed = 'CASTLE_PROGRESS {"line": "ERROR: [youtube] x: Private video"}'
        exc = self.ran(
            child(relayed, ("Traceback (most recent call last):",), ("KeyError: 'x'",))
        )
        self.assertEqual(str(exc), ir.GENERIC)

    def test_an_old_downloader_offers_the_update(self):
        exc = self.ran(
            child(
                ("    ERROR: [youtube] abc: Unable to extract nsig",),
                (ir.DOWNLOADER_OLD,),
            )
        )
        said = failure(exc)
        self.assertEqual(said["error"], ir.DOWNLOADER_OLD)
        self.assertEqual(said["action"], ir.UPDATE_DOWNLOADER)
        self.assertIn("Unable to extract nsig", said["error_detail"])

    def test_a_program_that_cannot_start_is_a_broken_install(self):
        exc = self.ran(["/no/such/python"])
        self.assertEqual(str(exc), ir.START_FAILED)
        self.assertIn("python", exc.log)
        with self.assertRaises(ToolFailed) as caught:
            job_progress.runner()(["/no/such/analyze_track"])
        self.assertEqual(str(caught.exception), ir.START_FAILED)

    def test_a_silent_failure_is_still_a_sentence(self):
        exc = self.ran(child(code=3))
        self.assertEqual(str(exc), ir.GENERIC)
        self.assertIn("exit 3", exc.log)


class InProcessFailureTests(unittest.TestCase):
    """What the job's own code raised, in the owner's words."""

    def test_no_exception_name_or_errno_reaches_the_owner(self):
        cases = {
            OSError(errno.ENOSPC, "No space left on device"): ir.DISK_FULL,
            PermissionError(errno.EACCES, "Permission denied"): ir.NO_ACCESS,
            ValueError("memory allocation of 9 bytes failed"): ir.OUT_OF_MEMORY,
            ValueError(
                "Could not prepare the light show: Command '['/x/bin/node', 'a']' "
                "returned non-zero exit status 1."
            ): ir.TOOL_FAILED.format(prog="node"),
            KeyError("layers"): ir.GENERIC,
        }
        for exc, want in cases.items():
            with self.subTest(exc=repr(exc)):
                said = failure(exc)
                self.assertEqual(said["error"], want)
                self.assertIsNone(said["action"])
                self.assertIn(type(exc).__name__, said["error_detail"])


class PrepareTests(unittest.TestCase):
    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.library = tmp / "tracks"
        self.library.mkdir()
        for name, value in (
            ("LIBRARY", self.library),
            ("DATA", tmp),
            ("CATALOG", tmp / "catalog.json"),
        ):
            self.enterContext(patch.object(radio_jobs, name, value))
        self.enterContext(patch.dict(radio_jobs.HANDLES, {}, clear=True))
        self.enterContext(patch.dict(radio_jobs.JOBS, {}, clear=True))
        self.job: dict[str, Any] = {
            "id": "radio_f",
            "source": "https://e.com/f",
            "done": False,
        }
        radio_jobs.JOBS["radio_f"] = self.job
        self.enterContext(patch.object(rich_show, "prepare"))
        self.enterContext(
            patch.object(radio_jobs, "crate_analysis", return_value=(88200, {}))
        )

    def prepare(self, tool, split=False):
        with patch.object(radio_jobs, "run_tool", tool):
            radio_jobs.prepare(self.job, self.job["source"], "", split, "mp3")

    def imported(self, job, script, args, timeout, extra_env=None):
        if script == "stems.py":
            raise ToolFailed(ir.DEMUCS_MISSING, "    pip install demucs\n")
        (self.library / "radio_f.mp3").write_bytes(b"ID3")
        (self.library / "tracks.json").write_text(
            json.dumps({"radio_f": {"source": "https://e.com/f"}}), encoding="utf-8"
        )

    def test_a_failed_link_reads_as_its_sentence_with_the_fix(self):
        log = "    ERROR: [youtube] x: nsig extraction failed\n" + ir.DOWNLOADER_OLD
        self.prepare(MagicMock(side_effect=ToolFailed(ir.DOWNLOADER_OLD, log)))
        job = self.job
        self.assertEqual((job["phase"], job["done"]), ("Import failed", True))
        self.assertEqual(job["error"], ir.DOWNLOADER_OLD)
        self.assertEqual(job["action"], ir.UPDATE_DOWNLOADER)
        self.assertEqual(job["error_detail"], log)

    def test_a_cancel_is_still_cancelled_not_a_failure(self):
        self.prepare(MagicMock(side_effect=job_progress.Cancelled("Cancelled")))
        self.assertEqual((self.job["phase"], self.job["error"]), ("Cancelled", None))
        self.assertNotIn("action", self.job)

    def test_a_failed_split_keeps_the_song_and_says_why(self):
        self.prepare(self.imported, split=True)
        self.assertEqual(self.job["phase"], "Ready · split needs attention")
        self.assertIsNone(self.job["error"])
        self.assertEqual(self.job["result"]["split_error"], ir.DEMUCS_MISSING)
        self.assertIn("pip install demucs", self.job["error_detail"])


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.data = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(import_routes, "DATA", self.data))

    def upload(self, name, body=b"ID3 audio"):
        handler = MagicMock()
        handler.headers = {"X-Filename": name}
        handler.rfile.read.return_value = body
        return import_routes.upload_job(handler, "radio_u", len(body))

    def test_a_full_disk_says_so_and_leaves_no_half_song(self):
        class Full:
            def __init__(self, path, mode):
                Path(path).write_bytes(b"ID")  # what reached the disk

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def write(self, _body):
                raise OSError(errno.ENOSPC, "No space left on device")

        with patch.object(import_routes, "open", Full, create=True):
            with self.assertRaises(ValueError) as caught:
                self.upload("song.mp3")
        self.assertEqual(str(caught.exception), ir.DISK_FULL)
        self.assertEqual(list(self.data.iterdir()), [])

    def test_a_decomposed_name_becomes_one_title(self):
        job = self.upload("Café Noir.mp3")
        self.assertEqual(job["title"], "Café Noir")

    def test_a_retry_forgets_the_last_failure(self):
        job = {
            "id": "radio_r",
            "source": "https://e.com/r",
            "done": True,
            "error": ir.DOWNLOADER_OLD,
            "error_detail": "ERROR: …",
            "action": ir.UPDATE_DOWNLOADER,
        }
        handler = MagicMock()
        handler.json_body.return_value = {"id": "radio_r"}
        with (
            patch.dict(radio_jobs.JOBS, {"radio_r": job}, clear=True),
            patch.object(import_routes, "submit"),
        ):
            import_routes.post_retry(handler)
        self.assertEqual(
            (job["error"], job["error_detail"], job["action"]), (None, None, None)
        )


if __name__ == "__main__":
    unittest.main()
