"""sd_sync's scenes command talks about the scene tracks, not a stale count."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class SdSyncDocTests(unittest.TestCase):
    def test_scenes_help_does_not_say_eight_tracks(self):
        text = (ROOT / "tools" / "sd_sync.py").read_text()
        self.assertNotIn("the 8 scene tracks", text)
        self.assertIn("the scene tracks", text)


if __name__ == "__main__":
    unittest.main()
