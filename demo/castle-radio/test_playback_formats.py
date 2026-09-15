"""The S3 format preference must stay bounded to deployed codec choices."""

import unittest
from unittest.mock import patch

import radio_jobs as server


class PlaybackFormatTests(unittest.TestCase):
    def test_supported_formats_and_rates(self):
        self.assertEqual(server.PLAYBACK_FORMATS["mp3"], (96, 44100))
        self.assertEqual(server.PLAYBACK_FORMATS["opus"], (64, 48000))
        self.assertEqual(server.PLAYBACK_FORMATS["wav"], (96, 44100))
        self.assertNotIn("flac", server.PLAYBACK_FORMATS)

    def test_unknown_and_flac_are_rejected(self):
        for value in ("flac", "aac", "", None):
            if value in ("", None):
                self.assertEqual(server.playback_format(value), "mp3")
            else:
                with self.assertRaises(ValueError):
                    server.playback_format(value)

    def test_cookie_preference(self):
        header = "theme=dark; castle_audio_format=opus; session=local"
        self.assertEqual(server.cookie_audio_format(header), "opus")

    def test_quality_presets_are_codec_specific(self):
        self.assertEqual(server.playback_options("mp3", "standard"), (96, 44100))
        self.assertEqual(server.playback_options("mp3", "high"), (160, 44100))
        self.assertEqual(server.playback_options("opus", "high"), (96, 48000))
        self.assertEqual(server.playback_options("opus", "max"), (128, 48000))
        self.assertEqual(server.playback_options("wav", "max"), (0, 44100))

    def test_unknown_quality_is_rejected(self):
        with self.assertRaises(ValueError):
            server.playback_quality("lossless")

    def test_saved_link_is_available_for_reprocessing(self):
        manifest = {
            "radio_monster": {
                "source": "https://example.test/monster",
                "audio": {"format": "mp3", "bitrate": 192, "bytes": 1234},
            }
        }
        details = server.source_metadata("radio_monster", manifest)
        self.assertEqual(details["source_kind"], "link")
        self.assertEqual(details["source_label"], "example.test/monster")
        self.assertTrue(details["source_available"])
        self.assertEqual(details["playback_quality"], "max")

    @patch("radio_jobs.catalog")
    @patch("radio_jobs.track_manifest")
    def test_reprocess_job_reuses_saved_link(self, manifest, catalog):
        catalog.return_value = [
            {"key": "radio_monster", "title": "Monster Mash", "split": True}
        ]
        manifest.return_value = {
            "radio_monster": {"source": "https://example.test/monster"}
        }
        job = server.reprocess_job("radio_monster", "opus", "high", False)
        self.assertEqual(job["source"], "https://example.test/monster")
        self.assertEqual(job["audio_format"], "opus")
        self.assertEqual(job["audio_quality"], "high")
        self.assertFalse(job["split"])
