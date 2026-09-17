"""The card light show, end to end without a device: what render_cues.py
writes, what `sd_sync cues` pushes, and what the castle then says about it.

tests/test_cue_file_cxx.py holds the FILE equal between Python and the
firmware header. These hold the three decisions around it: every pulse is
kept (PULSE_CAP is a script's limit, not a file's), a cue file only goes to
the card beside a song of the same name, and /api/status reports `cues`
for exactly as long as that song is the thing playing.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import castle_emu
import cue_file
import helpers  # noqa: F401  (hermetic env)
import render_cues
import sd_sync
from pulse_dynamics import PULSE_CAP
from test_cue_file_cxx import CUES, SCENE, ZONES
from test_sd_sync import FakeCard

DOC = {
    "zones": [{"id": z} for z in ZONES],
    "scenes": [
        {**SCENE, "id": "song", "name": "Song", "kind": "custom", "duration_ms": 200000,
         "audio_file": "tracks/song.mp3",
         "pulse": [{"synth": "onset_low", "zones": ["door"], "intensity": 0.8,
                    "decay": 0.9, "color": [1, 0.1, 0, 0]}],
         "cues": []}
    ],
}  # fmt: skip


class TestRender(unittest.TestCase):
    def test_a_file_keeps_every_pulse_a_script_would_thin(self) -> None:
        hits = [[round(0.5 + i * 0.3, 3), 0.6] for i in range(PULSE_CAP * 3)]
        wave = {"duration": 200.0, "onsets": {"onset_low": hits}, "env": []}
        with (
            tempfile.TemporaryDirectory() as tmp,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with (
                mock.patch.object(render_cues, "OUT", Path(tmp)),
                mock.patch.object(
                    render_cues, "track_file", return_value=Path("song.mp3")
                ),
                mock.patch.object(render_cues, "waveform", return_value=wave),
            ):
                out, total, pulses = render_cues.render("song", DOC)
            doc = cue_file.decode(out.read_bytes())
        self.assertEqual((total, pulses), (PULSE_CAP * 3, PULSE_CAP * 3))
        self.assertEqual([r["t"] for r in doc["records"]][:3], [500, 800, 1100])
        self.assertEqual(doc["duration_ms"], 200000)

    def test_markers_are_the_ones_render_audio_writes(self) -> None:
        wave = {
            "duration": 10.0,
            "onsets": {"onset_low": [[0.708, 0.37, 0.07], [9.95, 1.0, 0.0]]},
        }
        self.assertEqual(
            render_cues.markers_ms(wave, {}), {"onset_low": [[708, 0.37, 0.07]]}
        )


class TestSync(unittest.TestCase):
    def test_a_cue_file_goes_beside_its_song_and_nowhere_else(self) -> None:
        card = FakeCard({"song.mp3": 4096, "old.cue": 3})
        blob = cue_file.encode(SCENE, CUES, ZONES)
        with tempfile.TemporaryDirectory() as tmp:
            cues = Path(tmp) / "card" / "cues"
            cues.mkdir(parents=True)
            (cues / "song.cue").write_bytes(blob)
            (cues / "stranger.cue").write_bytes(blob)
            out = io.StringIO()
            with (
                mock.patch.object(sd_sync, "api", card),
                mock.patch.object(sd_sync.bp, "AUDIO", Path(tmp)),
                contextlib.redirect_stdout(out),
            ):
                self.assertEqual(sd_sync.cmd_cues("castle"), 0)
                puts = [c for c in card.calls if c[0] == "PUT"]
                self.assertEqual(puts, [("PUT", "/api/files/song.cue", len(blob))])
                # The second run finds the same bytes there and sends nothing.
                card.blobs["song.cue"] = blob
                card.calls.clear()
                self.assertEqual(sd_sync.cmd_cues("castle"), 0)
                self.assertFalse([c for c in card.calls if c[0] == "PUT"])
        self.assertIn("no song called stranger", out.getvalue())


class TestSitePublish(unittest.TestCase):
    """The page that went out with no songs in it said nothing about it."""

    def publish(self, library: str) -> str:
        page = f'<script id="radio-library" type="application/json">{library}</script>'
        out = io.StringIO()
        with (
            mock.patch.object(sd_sync, "build_site", return_value=page.encode()),
            mock.patch.object(sd_sync, "upload"),
            contextlib.redirect_stdout(out),
        ):
            self.assertEqual(sd_sync.cmd_site("castle"), 0)
        return out.getvalue()

    def test_an_empty_library_is_said_out_loud(self) -> None:
        self.assertIn("NO imported songs", self.publish("[]"))

    def test_a_library_with_songs_is_not_a_warning(self) -> None:
        self.assertNotIn("WARNING", self.publish('[{"key": "radio_531d"}]'))


class TestStatus(unittest.TestCase):
    def setUp(self) -> None:
        self.card = Path(tempfile.mkdtemp(prefix="emu-cues-sd-"))
        for name in ("song.mp3", "plain.mp3", "broken.mp3"):
            (self.card / name).write_bytes(b"\xff\xfb" + b"\0" * 30000)
        self.blob = cue_file.encode(SCENE, CUES, ZONES)
        (self.card / "song.cue").write_bytes(self.blob)
        (self.card / "broken.cue").write_bytes(self.blob[:-1])
        self.emu = castle_emu.CastleEmu(port=0, sd_dir=self.card, scenes=["vigil"])
        self.emu.start()
        self.addCleanup(self.emu.server_close)
        self.addCleanup(self.emu.shutdown)

    def cues_after(self, action: str, arg: str) -> Any:
        self.emu._apply(action, arg)
        time.sleep(0.01)
        return self.emu.status_json()["cues"]

    def test_cues_is_the_loaded_show_and_only_while_it_plays(self) -> None:
        self.assertEqual(self.emu.status_json()["cues"], 0)
        self.assertEqual(self.cues_after("PLAY", "song.mp3"), len(CUES))
        self.assertEqual(self.cues_after("PLAY", "plain.mp3"), 0)
        self.assertEqual(self.cues_after("PLAY", "broken.mp3"), 0)  # refused whole
        self.assertEqual(self.cues_after("PLAY", "song.mp3"), len(CUES))
        self.assertEqual(self.cues_after("SCENE", "vigil"), 0)
        self.assertEqual(self.cues_after("PLAY", "song.mp3"), len(CUES))
        self.assertEqual(self.cues_after("STOP", ""), 0)


if __name__ == "__main__":
    unittest.main()
