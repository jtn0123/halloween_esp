"""Percentages must reflect tool reports and preserve indeterminate stages."""

import sys
import unittest

from job_progress import interpret, run


class ProgressTests(unittest.TestCase):
    def test_download_is_measured(self):
        values, _ = interpret(
            "[download]  41.8% of 2.39MiB at 1MiB/s ETA 00:03", "import", 0
        )
        self.assertEqual(values["percent"], 41.8)
        self.assertEqual(values["phase"], "Downloading audio")

    def test_demucs_carriage_return_record(self):
        values, _ = interpret(
            'CASTLE_PROGRESS {"line": " 63%|████| 18/30 [00:04<00:03]"}', "split", 0
        )
        self.assertEqual(values["percent"], 63)

    def test_unknown_conversion_has_no_fake_percentage(self):
        values, _ = interpret("[ExtractAudio] Destination: song.mp3", "import", 0)
        self.assertIsNone(values["percent"])

    def test_the_downloader_names_the_song_before_the_manifest_does(self):
        values, _ = interpret(
            "[download] Destination: /tmp/x/Monster Mash [HD].webm", "import", 0
        )
        self.assertEqual(values, {"found_title": "Monster Mash [HD]"})
        streamed, _ = interpret(
            'CASTLE_PROGRESS {"line": "[ExtractAudio] Destination: /t/Day-O.mp3"}',
            "import",
            0,
        )
        self.assertEqual(streamed["found_title"], "Day-O")
        self.assertEqual(streamed["phase"], "Converting audio")
        self.assertNotIn("found_title", interpret("Destination: x.mp3", "split", 0)[0])

    def test_a_cancelled_run_kills_the_child_and_says_so(self):
        import threading

        from job_progress import Cancelled

        stop = threading.Event()
        threading.Timer(0.3, stop.set).start()
        with self.assertRaises(Cancelled):
            run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                20,
                "import",
                lambda **_: None,
                None,
                stop,
            )

    def test_nine_channel_analyses(self):
        count = 0
        for layer in ("vocals", "backing", "combined"):
            for channel in ("left", "right", "both"):
                values, count = interpret(
                    f"  {layer} {channel} low:4 mid:3", "split", count
                )
        self.assertEqual(count, 9)
        self.assertEqual(values["percent"], 100)


class StreamingTests(unittest.TestCase):
    def test_child_output_reaches_reporter(self):
        updates = []
        code = 'print(\'CASTLE_PROGRESS {"line": " 50%|xx| 1/2"}\', flush=True)'
        run([sys.executable, "-c", code], 5, "split", lambda **v: updates.append(v))
        self.assertTrue(any(v.get("percent") == 50 for v in updates))

    def test_hung_child_is_bounded(self):
        with self.assertRaisesRegex(ValueError, "timed out"):
            run(
                [sys.executable, "-c", "import time; time.sleep(10)"],
                0.1,
                "split",
                lambda **v: None,
            )


class ExtraEnvTests(unittest.TestCase):
    def test_extra_env_reaches_the_child(self):
        import sys

        import job_progress

        output = job_progress.run(
            [
                sys.executable,
                "-c",
                "import os; print(os.environ['CASTLE_IMPORT_SOURCE'])",
            ],
            10,
            "import",
            lambda **values: None,
            {"CASTLE_IMPORT_SOURCE": "https://example.test/song"},
        )
        self.assertIn("https://example.test/song", output)


if __name__ == "__main__":
    unittest.main()
