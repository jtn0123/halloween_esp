"""A Castle Radio sync cut off partway, against the emulated castle.

remote_library.transfer sends a song's light show first and its audio last,
and skips whatever the castle already holds identical by the CRC it reported
(castle_sent.py). Held to: the link dropping mid-song leaves no sidecar, no
song on the castle's list without its show, and a sentence that says what to
do; the retry sends only what did not land; an older castle that reports no
CRC proves nothing; and a castle whose copy differs is sent the new one.

The cut is tools/castle_emu's `drop_after`: that many upload bytes, then the
link goes mid-body. Every card and record is a temp directory.
"""

import random
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import radio_env  # noqa: F401 — the sandbox first, then tools/ on the path

# isort: split
import castle_sent
import device_bridge
import remote_library
from castle_emu import CastleEmu

KEY = "radio_cut"


class SyncResume(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="radio-resume-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.card = self.tmp / "card"
        self.emu = CastleEmu(port=0, sd_dir=self.card, scenes=["vigil"])
        self.emu.start()
        self.addCleanup(self.emu.server_close)
        self.addCleanup(self.emu.shutdown)
        self.host = f"127.0.0.1:{self.emu.port}"
        for patch in (
            mock.patch.object(device_bridge, "HOST", self.host),
            mock.patch.object(castle_sent, "FILE", self.tmp / "castle-sent.json"),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.audio = random.Random(7).randbytes(200_000)
        self.show = [
            ("radio_cut.show.json", b'{"v":1}' * 50),
            ("radio_cut.cue", b"C" * 900),
        ]
        self.addCleanup(remote_library._JOBS.pop, KEY, None)

    def sync(self):
        remote_library._JOBS[KEY] = {"key": KEY, "done": False, "error": None}
        with mock.patch.object(
            remote_library,
            "upload_with_progress",
            wraps=remote_library.upload_with_progress,
        ) as upload:
            remote_library.transfer(
                KEY, "/api/files", "radio_cut.mp3", self.audio, self.show
            )
        return remote_library.job(KEY), [c.args[2] for c in upload.call_args_list]

    def on_card(self):
        return sorted(p.name for p in self.card.iterdir() if p.is_file())

    def test_a_cut_mid_song_lists_nothing_half_done_and_the_retry_sends_only_the_song(
        self,
    ):
        shows = sum(len(blob) for _, blob in self.show)
        self.emu.drop_after = shows + 50_000
        job, sent = self.sync()
        self.assertEqual(
            sent, ["radio_cut.show.json", "radio_cut.cue", "radio_cut.mp3"]
        )
        self.assertEqual(job["phase"], "Sync failed")
        self.assertIn("stopped answering partway through radio_cut.mp3", job["error"])
        self.assertIn("sync again to finish", job["error"])
        # The show is there, the song is not, and nothing is half of anything:
        # castle-direct.js lists a card song only once its audio has landed.
        self.assertEqual(self.on_card(), ["radio_cut.cue", "radio_cut.show.json"])
        job, sent = self.sync()
        self.assertIsNone(job["error"])
        self.assertEqual(sent, ["radio_cut.mp3"], "the show that landed is skipped")
        self.assertEqual(
            (job["sent_bytes"], job["percent"]), (shows + len(self.audio), 100)
        )
        self.assertEqual((self.card / "radio_cut.mp3").read_bytes(), self.audio)
        # And a third time, nothing at all goes.
        self.assertEqual(self.sync()[1], [])

    def test_a_changed_song_is_resent_and_an_unproven_copy_is_too(self):
        self.sync()
        self.audio = self.audio[:-1] + b"!"  # re-imported: same size, new bytes
        job, sent = self.sync()
        self.assertIsNone(job["error"])
        self.assertEqual(sent, ["radio_cut.mp3"])
        self.assertEqual((self.card / "radio_cut.mp3").read_bytes(), self.audio)
        # No record (a new computer, a wiped data dir): the castle's copies
        # are unproven, so all of it goes again — slower, never wrong.
        castle_sent.FILE.unlink()
        self.assertEqual(
            self.sync()[1], [name for name, _ in self.show] + ["radio_cut.mp3"]
        )

    def test_the_record_believes_only_a_crc_the_castle_reported(self):
        data = b"abc" * 10
        path = "/api/files/x.cue"
        castle_sent.record(self.host, path, data, None)  # firmware before v5.42
        castle_sent.record(self.host, path, data, "00000000")  # not these bytes
        self.assertFalse(castle_sent.landed(self.host, path, data, len(data)))
        castle_sent.record(self.host, path, data, castle_sent.crc(data))
        self.assertTrue(castle_sent.landed(self.host, path, data, len(data)))
        self.assertFalse(castle_sent.landed(self.host, path, data, len(data) + 1))
        self.assertFalse(castle_sent.landed("10.0.0.1", path, data, len(data)))
        castle_sent.FILE.write_text("not json", encoding="utf-8")
        self.assertFalse(castle_sent.landed(self.host, path, data, len(data)))

    def test_no_castle_is_said_plainly_not_as_a_cut(self):
        with mock.patch.object(device_bridge, "HOST", ""):
            job, sent = self.sync()
        self.assertEqual(sent, [])
        self.assertNotIn("partway", job["error"])
        self.assertIn("No castle found yet", job["error"])


if __name__ == "__main__":
    unittest.main()
