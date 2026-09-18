"""The publish's skip record: what a second `sd_sync scenes` does NOT fetch.

A publish runs after every scene save, so the interesting behaviour is the
steady state — nothing changed, and the tool must say so without pulling the
whole 8 MB scene library back over Wi-Fi to check (grade report 2026-09-17 pm
G1). The record that makes that possible is `audio/card/.published.json`
(tools/published.py), and the point of these tests is that it is an
ACCELERATOR: wrong, stale or missing, the byte compare is still there.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import published
import sd_sync
from test_sd_sync import SdCase


class TestPublishedRecord(SdCase):
    def seed_card(self, *, vigil: bytes = b"x" * 100) -> None:
        """One scene, rendered and already on the card byte for byte."""
        (self.tmp / "audio").mkdir(exist_ok=True)
        (self.tmp / "audio" / "01_vigil.mp3").write_bytes(vigil)
        self.card.subdirs["scenes"] = {"01_vigil.mp3": len(vigil)}
        self.card.blobs["scenes/01_vigil.mp3"] = vigil
        self.seed_show("vigil")

    def scene_gets(self) -> list[str]:
        return [p for m, p, _n in self.card.calls if m == "GET" and "/sd/scenes/" in p]

    def test_the_first_publish_byte_compares_and_records_what_it_learned(self) -> None:
        self.seed_card()
        self.assertEqual(self.run_quiet(sd_sync.cmd_scenes, "10.0.0.9"), 0)
        self.assertIn("/sd/scenes/01_vigil.mp3", self.scene_gets())
        rec = json.loads(published.record_path().read_text())
        self.assertIn("scenes/01_vigil.mp3", rec["10.0.0.9"])

    def test_a_second_publish_with_nothing_changed_fetches_nothing(self) -> None:
        self.seed_card()
        self.run_quiet(sd_sync.cmd_scenes, "10.0.0.9")
        # The cue file the first run pushed is on the card now, unchanged too.
        cue = (self.tmp / "audio" / "card" / "scenes" / "vigil.cue").read_bytes()
        self.card.subdirs["scenes"]["vigil.cue"] = len(cue)
        self.card.blobs["scenes/vigil.cue"] = cue
        self.run_quiet(sd_sync.cmd_scenes, "10.0.0.9")
        self.card.calls.clear()
        self.assertEqual(self.run_quiet(sd_sync.cmd_scenes, "10.0.0.9"), 0)
        self.assertEqual(self.scene_gets(), [])
        # show.man still goes every time; nothing else does.
        self.assertEqual([p for p, _n in self._puts()], ["/api/scenes/show.man"])

    def test_a_changed_render_is_re_sent_even_at_the_same_size(self) -> None:
        self.seed_card()
        self.run_quiet(sd_sync.cmd_scenes, "10.0.0.9")
        (self.tmp / "audio" / "01_vigil.mp3").write_bytes(b"z" * 100)
        self.card.calls.clear()
        self.assertEqual(self.run_quiet(sd_sync.cmd_scenes, "10.0.0.9"), 0)
        self.assertIn("/api/scenes/01_vigil.mp3", [p for p, _n in self._puts()])

    def test_a_record_from_another_host_is_not_evidence_about_this_one(self) -> None:
        """Two castles do not share a card, so the hash is filed per host and
        a match under the wrong key proves nothing."""
        self.seed_card()
        self.run_quiet(sd_sync.cmd_scenes, "1.2.3.4")
        rec = json.loads(published.record_path().read_text())
        self.assertEqual(list(rec), ["1.2.3.4"])
        self.card.calls.clear()
        self.assertEqual(self.run_quiet(sd_sync.cmd_scenes, "10.0.0.9"), 0)
        self.assertIn("/sd/scenes/01_vigil.mp3", self.scene_gets())
        self.assertEqual(
            sorted(json.loads(published.record_path().read_text())),
            ["1.2.3.4", "10.0.0.9"],
        )

    def test_an_unreadable_record_is_simply_no_record(self) -> None:
        path = published.record_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json at all")
        self.seed_card()
        self.assertEqual(self.run_quiet(sd_sync.cmd_scenes, "10.0.0.9"), 0)
        self.assertIn("/sd/scenes/01_vigil.mp3", self.scene_gets())


if __name__ == "__main__":
    import unittest

    unittest.main()
