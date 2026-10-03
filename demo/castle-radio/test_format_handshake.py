"""Castle Radio's sync asks the publish handshake (tools/fw_formats.py)
before a byte goes: a song whose light show is in a format the castle's
firmware cannot read is refused with the sentence that says to update the
castle first, and nothing lands on the card."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import remote_library

# isort: split
import cue_file
import device_bridge
import fw_formats
from castle_emu import CastleEmu


def cue(version):
    return cue_file.HEADER.pack(cue_file.MAGIC, version, 0, 0, 0, 0)


class SyncHandshake(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.library = self.root / "tracks"
        self.library.mkdir()
        (self.library / "song.mp3").write_bytes(b"\xff\xfb" * 32)
        (self.library / "song.show.json").write_text("{}", encoding="utf-8")
        self.row = {"key": "song"}
        # The show is current: start() sends it as it is, never rebuilds it.
        meta = patch.object(
            remote_library.rich_show, "metadata", return_value={"ok": 1}
        )
        meta.start()
        self.addCleanup(meta.stop)

    def castle(self, version):
        emu = CastleEmu(
            port=0, sd_dir=self.root / "card", scenes=["vigil"], version=version
        )
        emu.start()
        self.addCleanup(emu.server_close)
        self.addCleanup(emu.shutdown)
        host = patch.object(device_bridge, "HOST", f"127.0.0.1:{emu.port}")
        host.start()
        self.addCleanup(host.stop)
        fresh = patch.dict(device_bridge._status_cache, {"state": None})
        fresh.start()
        self.addCleanup(fresh.stop)
        return emu

    def test_a_v2_show_is_refused_by_a_v1_castle_before_any_upload(self):
        (self.library / "song.cue").write_bytes(cue(cue_file.VERSION_2))
        emu = self.castle("5.70")
        with self.assertRaises(ValueError) as caught:
            remote_library.start(self.root, self.library, [self.row], "song")
        self.assertIn(fw_formats.UPDATE_FIRST, str(caught.exception))
        self.assertIn("5.71", str(caught.exception))
        self.assertFalse((emu.sd_dir / "song.mp3").exists())
        self.assertNotIn("song", remote_library._JOBS)

    def test_the_same_castle_takes_a_v1_show(self):
        (self.library / "song.cue").write_bytes(cue(cue_file.VERSION))
        self.castle("5.70")
        with patch.object(remote_library._POOL, "submit") as submit:
            job = remote_library.start(self.root, self.library, [self.row], "song")
        self.addCleanup(remote_library._JOBS.pop, "song", None)
        self.assertEqual(job["phase"], "Queued")
        submit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
