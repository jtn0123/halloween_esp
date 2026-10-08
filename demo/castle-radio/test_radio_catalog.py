"""`source_available` is read from the disk, never from catalog.json.

grade report 2026-09-24 B8: the catalog wrote back rows merged with
source_metadata, and on the next read the stored copy won over the fresh
one, so an uploaded song kept `source_available: true` after its saved
original was deleted — and the library kept "Change audio" enabled for it.
"""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import radio_jobs
import rich_show


class SourceAvailableTests(unittest.TestCase):
    def setUp(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.library = tmp / "tracks"
        self.library.mkdir()
        self.catalog = tmp / "catalog.json"
        self.original = tmp / "upload.mp3"
        self.original.write_bytes(b"ID3")
        for name, value in (
            ("LIBRARY", self.library),
            ("DATA", tmp),
            ("CATALOG", self.catalog),
        ):
            self.enterContext(patch.object(radio_jobs, name, value))
        self.write_manifest("radio_f")

    def write_manifest(self, *keys):
        source = radio_jobs.FILE_PREFIX + str(self.original)
        manifest = {key: {"source": source} for key in keys}
        (self.library / "tracks.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    def stored_rows(self):
        return json.loads(self.catalog.read_text(encoding="utf-8"))

    def test_a_stale_stored_value_loses_to_the_disk(self):
        """A catalog written before the fix still says true; the read says
        what the disk says, both ways."""
        row = {"key": "radio_f", "title": "F", "source_available": True}
        self.catalog.write_text(json.dumps([row]), encoding="utf-8")
        self.assertTrue(radio_jobs.catalog()[0]["source_available"])
        self.original.unlink()
        self.assertFalse(radio_jobs.catalog()[0]["source_available"])
        row["source_available"] = False
        self.catalog.write_text(json.dumps([row]), encoding="utf-8")
        self.original.write_bytes(b"ID3")
        self.assertTrue(radio_jobs.catalog()[0]["source_available"])

    def test_the_rest_of_the_row_is_still_the_stored_one(self):
        row = {"key": "radio_f", "title": "F", "source_label": "My Song.mp3"}
        self.catalog.write_text(json.dumps([row]), encoding="utf-8")
        self.assertEqual(radio_jobs.catalog()[0]["source_label"], "My Song.mp3")

    def test_a_prepared_song_writes_no_source_available_for_any_row(self):
        """The write that froze it: every row goes back without the field,
        the old ones included."""
        old = {"key": "radio_o", "title": "O", "source_available": True}
        self.catalog.write_text(json.dumps([old]), encoding="utf-8")
        self.write_manifest("radio_o", "radio_f")
        job = {"id": "radio_f", "source": "https://e.com/f", "done": False}

        def fake_import(job, script, args, timeout, extra_env=None):
            (self.library / "radio_f.mp3").write_bytes(b"ID3")

        running = MagicMock()
        running.cancel.return_value = False
        with (
            patch.dict(radio_jobs.JOBS, {"radio_f": job}, clear=True),
            patch.dict(
                radio_jobs.HANDLES,
                {"radio_f": (running, threading.Event())},
                clear=True,
            ),
            patch.object(radio_jobs, "run_tool", fake_import),
            patch.object(
                radio_jobs, "crate_analysis", return_value=(88200, {"onset_low": []})
            ),
            patch.object(rich_show, "prepare"),
        ):
            radio_jobs.prepare(job, job["source"], "", False, "mp3")
        # The owner's words, not the demo's ("Ready in demo" until 2026-10).
        self.assertEqual(job["phase"], "Ready on this computer")
        self.assertEqual(job["detail"], "Audio and lights are ready on this computer")
        rows = self.stored_rows()
        self.assertEqual([r["key"] for r in rows], ["radio_o", "radio_f"])
        self.assertEqual([r for r in rows if "source_available" in r], [])
        self.original.unlink()
        self.assertEqual(
            [r["source_available"] for r in radio_jobs.catalog()], [False, False]
        )


if __name__ == "__main__":
    unittest.main()
