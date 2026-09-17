"""Pins for Castle Radio player behaviour that lives in the page scripts."""

import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class PlayerSourceTests(unittest.TestCase):
    def test_hidden_built_ins_are_filtered_out_of_the_queue(self):
        preview = (HERE / "preview.js").read_text()
        self.assertIn("queue=queue.filter(i=>!tracks[i].deleted)", preview)

    def test_shuffle_off_restores_the_pre_shuffle_order(self):
        app = (HERE / "app.js").read_text()
        self.assertIn("unshuffled=queue.slice()", app)
        self.assertNotIn("queue.toSorted((a,b)=>a-b)", app)

    def test_loadedmetadata_ignores_non_finite_duration(self):
        app = (HERE / "app.js").read_text()
        self.assertIn("Number.isFinite(audio.duration)", app)

    def test_undo_restore_puts_the_song_back_in_the_queue(self):
        preview = (HERE / "preview.js").read_text()
        self.assertIn("queue.push(t.id)", preview)


if __name__ == "__main__":
    unittest.main()
