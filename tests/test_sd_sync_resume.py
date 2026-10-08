"""A publish cut off halfway, against the emulated castle: what the card holds.

`sd_sync scenes` runs over porch Wi-Fi, and the Wi-Fi can go at any byte.
The promise these tests hold it to is the owner's, docs/PRODUCTION-TODO.md
§7: the card is left with the old show or the new one and never half of
either — no half-written show.man, no manifest naming a file that has not
arrived, no `.part` sidecar left behind — and running it again finishes
the job, skipping what already landed without pulling it back to check.

The cut is real: tools/castle_emu's `drop_after` lets that many upload bytes
through and then hangs up mid-body, the client hearing nothing back. Every
card is a temp directory and every castle is 127.0.0.1 on port 0.
"""

from __future__ import annotations

import contextlib
import io
import os
import random
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import helpers  # noqa: F401  (hermetic env)

# isort: split
import gen_scene_cards
import published
import scene_manifest
import sd_sync
from castle_emu import CastleEmu


def show(*scenes: tuple[str, int]) -> dict:
    return {
        "zones": [{"id": "towerL"}, {"id": "towerR"}, {"id": "door"}],
        "scenes": [
            {"id": sid, "duration_ms": ms, "volume": 0.5, "base": {"towerL": "candle"}}
            for sid, ms in scenes
        ],
    }


def noise(n: int, seed: int) -> bytes:
    return random.Random(seed).randbytes(n)


class Resume(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="sd-resume-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.card = self.tmp / "card" / "scenes"
        self.card.mkdir(parents=True)
        audio = self.tmp / "audio"
        audio.mkdir()
        # On the card: last night's show, `vigil` and `gone`.
        gen_scene_cards.write(show(("vigil", 1000), ("gone", 2000)), self.card, {})
        (self.card / "01_vigil.mp3").write_bytes(noise(40_000, 1))
        (self.card / "02_gone.mp3").write_bytes(noise(30_000, 2))
        self.old_man = (self.card / "show.man").read_bytes()
        # On the Mac: tonight's, `vigil` re-cut and `storm` new.
        gen_scene_cards.write(
            show(("vigil", 1500), ("storm", 3000)), audio / "card" / "scenes", {}
        )
        (audio / "01_vigil.mp3").write_bytes(noise(96_000, 3))
        (audio / "02_storm.mp3").write_bytes(noise(64_000, 4))
        self.new = {p.name: p.read_bytes() for p in sorted(audio.glob("*.mp3"))}
        self.new |= {
            p.name: p.read_bytes()
            for p in sorted((audio / "card" / "scenes").iterdir())
        }
        self.assertNotEqual(
            self.new["vigil.cue"], (self.card / "vigil.cue").read_bytes()
        )
        self.emu = CastleEmu(port=0, sd_dir=self.tmp / "card")
        self.emu.start()
        self.addCleanup(self.emu.server_close)
        self.addCleanup(self.emu.shutdown)
        self.ip = f"127.0.0.1:{self.emu.port}"
        (self.tmp / "devices.toml").write_text("", encoding="utf-8")
        for patch in (
            mock.patch.object(sd_sync.bp, "AUDIO", audio),
            mock.patch.dict(
                os.environ, {"CASTLE_DEVICES": str(self.tmp / "devices.toml")}
            ),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def publish(self) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(sd_sync.cmd_scenes(self.ip), 0)
        return out.getvalue()

    def cut_at(self, n: int) -> str:
        """Publish with the link dropping after `n` bytes; what it said."""
        self.emu.drop_after = n
        with (
            self.assertRaises(SystemExit) as stopped,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            sd_sync.cmd_scenes(self.ip)
        return str(stopped.exception)

    def assert_whole(self) -> None:
        """No sidecar anywhere, and every scene the card's manifest names has
        its audio and its cue file on the card — the castle can play what it
        believes it has, whichever show that is."""
        self.assertEqual(sorted(self.card.parent.rglob("*.part")), [])
        for row in scene_manifest.decode((self.card / "show.man").read_bytes()):
            self.assertTrue((self.card / f"{row['audio']}.mp3").is_file(), row)
            self.assertTrue((self.card / f"{row['id']}.cue").is_file(), row)

    def assert_new_show(self) -> None:
        for name, data in self.new.items():
            self.assertEqual((self.card / name).read_bytes(), data, name)
        self.assertFalse((self.card / "gone.cue").exists(), "the sweep ran")
        self.assert_whole()

    def test_cut_mid_track_keeps_the_old_show_and_the_retry_skips_what_landed(
        self,
    ) -> None:
        said = self.cut_at(len(self.new["01_vigil.mp3"]) + 10_000)
        self.assertIn("stopped partway through 02_storm.mp3", said)
        self.assertIn("Run it again", said)
        self.assertEqual((self.card / "show.man").read_bytes(), self.old_man)
        self.assertEqual(
            (self.card / "01_vigil.mp3").read_bytes(), self.new["01_vigil.mp3"]
        )
        self.assertFalse((self.card / "02_storm.mp3").exists())
        self.assert_whole()
        record = published.Published(self.ip)
        self.assertTrue(record.matches("scenes/01_vigil.mp3", self.new["01_vigil.mp3"]))
        # The retry: the track that landed is skipped on the record alone.
        with mock.patch.object(
            sd_sync, "_scene_bytes_match", wraps=sd_sync._scene_bytes_match
        ) as fetched:
            out = self.publish()
        self.assertIn("01_vigil.mp3 unchanged, skipped", out)
        self.assertNotIn("01_vigil.mp3", [c.args[1] for c in fetched.call_args_list])
        self.assert_new_show()

    def test_cut_mid_manifest_leaves_the_old_one_whole_and_the_retry_sends_only_it(
        self,
    ) -> None:
        before_man = sum(len(v) for k, v in self.new.items() if k != "show.man")
        said = self.cut_at(before_man + 20)
        self.assertIn("stopped partway through show.man", said)
        self.assertEqual((self.card / "show.man").read_bytes(), self.old_man)
        # Every new file is there, and the old manifest still finds its own:
        # gone.cue is swept only after a new show.man has landed.
        self.assertTrue((self.card / "gone.cue").is_file())
        self.assertEqual((self.card / "storm.cue").read_bytes(), self.new["storm.cue"])
        self.assert_whole()
        with mock.patch.object(sd_sync, "_scene_bytes_match") as fetched:
            out = self.publish()
        fetched.assert_not_called()
        self.assertEqual(out.count("unchanged, skipped"), len(self.new) - 1)
        self.assertIn("(0 sent", out)
        self.assert_new_show()

    def test_a_refusal_is_the_castles_own_reason_not_a_cut(self) -> None:
        """The castle ANSWERED: its reason is the one to see, as before."""
        self.emu.sd_free_kb = 1
        with (
            self.assertRaises(urllib.error.HTTPError) as refused,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            sd_sync.cmd_scenes(self.ip)
        self.assertEqual(refused.exception.code, 507)
        self.assertEqual((self.card / "show.man").read_bytes(), self.old_man)
        self.assert_whole()

    def test_an_uncut_publish_is_the_new_show_and_answers_as_it(self) -> None:
        self.publish()
        self.assert_new_show()
        self.assertIsNone(self.emu.drop_after, "the knob is off unless a test sets it")


if __name__ == "__main__":
    unittest.main()
