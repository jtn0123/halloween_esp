"""Prepared shows retain the rich timeline and export the exact preview."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import cue_file
import rich_show


class RichShowTests(unittest.TestCase):
    def test_dense_simultaneous_hits_keep_timing_decay_and_pixel_variation(self):
        wave = {
            "duration": 12,
            "onsets": {
                band: [[i * 0.09, 0.2 + (i % 8) * 0.1] for i in range(100)]
                for band in ("onset_low", "onset_mid", "onset_high")
            },
        }
        blob, preview = rich_show.build("radio_test", wave)
        decoded = cue_file.decode(blob)
        strikes = [c for c in decoded["records"] if c["op"] == "strike"]
        self.assertEqual(len(strikes), 300)
        self.assertGreater(len({tuple(c["color"]) for c in strikes}), 20)
        self.assertGreater(len({c["mode"] for c in strikes}), 1)
        self.assertTrue(any(c["t"] % 250 for c in strikes))
        self.assertTrue(all(0 < c["decay"] < 1 for c in strikes))
        self.assertEqual(preview, rich_show.preview_from_blob("radio_test", blob))

    def test_split_streams_stay_on_their_assigned_fixture(self):
        marks = {
            "onset_low": [[0.3, 0.4]],
            "onset_mid": [[0.6, 0.5]],
            "onset_high": [[0.9, 0.6]],
        }
        wave = {"duration": 2, "onsets": marks}
        layers = {
            name: {channel: {"onsets": marks} for channel in ("both", "left", "right")}
            for name in ("vocals", "backing")
        }
        blob, _ = rich_show.build("radio_test", wave, layers)
        records = cue_file.decode(blob)["records"]
        self.assertEqual(len(records), 9)
        self.assertEqual(
            sorted(c["mask"] for c in records), [1] * 3 + [2] * 3 + [4] * 3
        )


class PreparedFilesTests(unittest.TestCase):
    def test_analyzer_failure_is_reported_to_the_caller(self):
        from unittest.mock import patch

        with patch.object(
            rich_show, "_prepare", side_effect=SystemExit("analysis failed")
        ):
            with self.assertRaisesRegex(ValueError, "analysis failed"):
                rich_show.prepare(Path("/unused"), {"key": "radio_test"})

    def test_prepare_preview_is_decoded_card_and_audio_is_untouched(self):
        import json
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp)
            audio = library / "radio_test.mp3"
            audio.write_bytes(b"unchanged audio")
            row = {"key": "radio_test", "title": "Test"}
            wave = {"duration": 2, "onsets": {"onset_low": [[0.5, 0.8]]}}
            with patch.object(rich_show, "waveform", return_value=wave):
                info = rich_show.prepare(library, row)
            self.assertEqual(audio.read_bytes(), b"unchanged audio")
            blob = audio.with_suffix(".cue").read_bytes()
            preview = json.loads(audio.with_suffix(".show.json").read_text())
            expected = rich_show.preview_from_blob(row["key"], blob)
            expected["name"] = "Test"
            self.assertEqual(preview, expected)
            self.assertEqual(info["records"], 1)
            self.assertEqual(info["bytes"], len(blob))
            # Replacing audio invalidates the old prepared timeline.
            import os

            stamp = audio.with_suffix(".show.json").stat().st_mtime_ns + 1000000
            os.utime(audio, ns=(stamp, stamp))
            self.assertIsNone(rich_show.metadata(library, row))

    def test_missing_split_is_reported_without_overwriting_a_show(self):
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            library = Path(tmp)
            (library / "radio_test.mp3").write_bytes(b"audio")
            with patch.object(rich_show, "waveform", return_value={}):
                with self.assertRaisesRegex(ValueError, "Separated analysis"):
                    rich_show.prepare(library, {"key": "radio_test", "split": True})
            self.assertFalse((library / "radio_test.cue").exists())
