"""Removal must preserve originals for Undo and never touch unrelated songs."""

import json
import tempfile
import unittest
from pathlib import Path

import library_ops


class RemovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.library = self.data / "tracks"
        self.library.mkdir()
        self.key = "radio_fixture"
        self.row = {"key": self.key, "title": "A prepared song", "split": True}
        self.other = {"key": "radio_other", "title": "Keep me"}
        self.catalog = self.data / "catalog.json"
        self.catalog.write_text(json.dumps([self.row, self.other]))
        self.files = {
            self.library / f"{self.key}.mp3": b"full mix",
            self.library / "stems" / self.key / "vocals.mp3": b"voice",
            self.library / "stems" / self.key / "backing.mp3": b"background",
            self.library / "_src" / f"{self.key}.wav": b"original",
            self.data / f"{self.key}.yaml": b"generated cues",
        }
        for path, content in self.files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        self.unrelated = self.library / "radio_other.mp3"
        self.unrelated.write_bytes(b"untouched")

    def test_remove_and_undo_restore_every_layer(self):
        library_ops.remove(self.data, self.library, self.catalog, self.key)
        self.assertEqual(json.loads(self.catalog.read_text()), [self.other])
        self.assertTrue(all(not path.exists() for path in self.files))
        self.assertEqual(self.unrelated.read_bytes(), b"untouched")
        library_ops.restore(self.data, self.catalog, self.key)
        for path, content in self.files.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertIn(self.row, json.loads(self.catalog.read_text()))

    def test_unknown_id_changes_nothing(self):
        with self.assertRaises(ValueError):
            library_ops.remove(self.data, self.library, self.catalog, "radio_unknown")
        self.assertEqual(len(json.loads(self.catalog.read_text())), 2)
        self.assertTrue(all(path.exists() for path in self.files))

    def test_restore_refuses_to_overwrite_newer_audio(self):
        library_ops.remove(self.data, self.library, self.catalog, self.key)
        replacement = self.library / f"{self.key}.mp3"
        replacement.write_bytes(b"newer audio")
        with self.assertRaises(ValueError):
            library_ops.restore(self.data, self.catalog, self.key)
        self.assertEqual(replacement.read_bytes(), b"newer audio")
        self.assertEqual(json.loads(self.catalog.read_text()), [self.other])

    def test_opus_playback_file_is_removed_and_restored(self):
        mp3 = self.library / f"{self.key}.mp3"
        mp3.unlink()
        opus = self.library / f"{self.key}.opus"
        opus.write_bytes(b"compact playback")
        library_ops.remove(self.data, self.library, self.catalog, self.key)
        self.assertFalse(opus.exists())
        library_ops.restore(self.data, self.catalog, self.key)
        self.assertEqual(opus.read_bytes(), b"compact playback")

    def test_named_playback_file_is_trashed(self):
        mp3 = self.library / f"{self.key}.mp3"
        mp3.unlink()
        named = self.library / "custom_play.opus"
        named.write_bytes(b"named mix")
        self.row["playback_file"] = "custom_play.opus"
        self.catalog.write_text(json.dumps([self.row, self.other]))
        library_ops.remove(self.data, self.library, self.catalog, self.key)
        self.assertFalse(named.exists())
        library_ops.restore(self.data, self.catalog, self.key)
        self.assertEqual(named.read_bytes(), b"named mix")


if __name__ == "__main__":
    unittest.main()
