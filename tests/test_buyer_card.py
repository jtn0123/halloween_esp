"""tools/buyer_card.py: a sold castle's SD card, made with no castle.

The card is the hand-off, so what is pinned is what is ON it:

  * exactly the shipped show's files (manifest, cue files, card-rate audio),
    the firmware notices and the written source offer — nothing else, and
    in particular nothing from tracks/ and no site;
  * a scene that needs a song is refused before anything is rendered or
    written;
  * the offer says what GPLv3 §6 b) asks for (docs/LICENSING.md): three
    years from its date, the repo and the tagged release, the physical-copy
    option, the Installation Information;
  * the castle's owner page links the two card names this tool writes;
  * a directory holding anything but an earlier card is refused, and an
    earlier card is replaced whole.

The fixture show is two short synthesised scenes, so the real renderer runs
(the bytes are `make publish`'s) without paying for the whole show.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import buyer_card as bc
import fw_formats
import scene_manifest
from castle_emu_flash import OWNER_PAGE

BASE = {"towerL": "chill", "towerR": "chill", "door": "ember"}


def probe(sid: str, kind: str = "ambient", **over: Any) -> dict[str, Any]:
    s = {
        "id": sid, "name": sid.title(), "kind": kind, "volume": 0.5,
        "duration_ms": 1200, "loop": False, "blurb": "x", "base": BASE,
        "score": [{"t": 0.0, "synth": "wind", "dur": 1.0}], "cues": [],
    }  # fmt: skip
    s.update(over)
    return s


def write_show(path: Path, scenes: list[dict[str, Any]]) -> Path:
    doc = yaml.safe_load(bc.SHOW.read_text(encoding="utf-8"))
    doc["scenes"] = scenes
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


class TestTheCard(unittest.TestCase):
    tmp: Path
    out: Path
    ids: list[str]

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="castle-buyer-card-test-"))
        show = write_show(
            cls.tmp / "show.yaml", [probe("glow"), probe("jump", "motion")]
        )
        cls.out = cls.tmp / "card"
        cls.ids = bc.build(cls.out, show, "v9.9.9", dt.date(2028, 2, 29))

    @classmethod
    def tearDownClass(cls) -> None:
        import shutil

        shutil.rmtree(cls.tmp, ignore_errors=True)

    def files(self) -> set[str]:
        return {
            p.relative_to(self.out).as_posix()
            for p in self.out.rglob("*")
            if p.is_file()
        }

    def test_exactly_the_show_and_the_licences(self) -> None:
        self.assertEqual(self.ids, ["glow", "jump"])
        self.assertEqual(
            self.files(),
            {
                "scenes/show.man", "scenes/glow.cue", "scenes/jump.cue",
                "scenes/01_glow.mp3", "scenes/02_jump.mp3",
                bc.CARD_NOTICES, bc.CARD_OFFER,
            },
        )  # fmt: skip
        self.assertFalse((self.out / "site").exists(), "/ must be the owner page")

    def test_the_audio_is_real_and_the_manifest_reads(self) -> None:
        head = (self.out / "scenes" / "01_glow.mp3").read_bytes()[:2]
        self.assertEqual((head[0], head[1] & 0xE0), (0xFF, 0xE0), "an MPEG frame")
        blob = (self.out / "scenes" / "show.man").read_bytes()
        self.assertEqual(scene_manifest.evening_ids(blob), ["glow"])
        heads = fw_formats.heads((self.out / "scenes").iterdir())
        self.assertIsNone(
            fw_formats.refusal({"version": fw_formats.this_firmware()}, heads)
        )

    def test_the_notices_are_the_firmwares(self) -> None:
        self.assertEqual(
            (self.out / bc.CARD_NOTICES).read_bytes(), bc.NOTICES.read_bytes()
        )

    def test_the_offer_says_what_section_6b_asks(self) -> None:
        offer = (self.out / bc.CARD_OFFER).read_text(encoding="utf-8")
        for words in (
            "made on 2028-02-29",
            "valid until\n2031-02-28",  # three years; no 29 Feb in 2031
            "spare parts or customer support",
            "anyone\nwho possesses",
            f"{bc.REPO}/releases/tag/v9.9.9",
            "durable physical medium",
            "INSTALLATION INFORMATION",
            "make build-buyer",
            f"firmware {fw_formats.this_firmware()}",
        ):
            self.assertIn(words, offer)
        self.assertNotIn("@", offer, "no address anyone has not chosen to publish")
        self.assertLessEqual(max(len(line) for line in offer.splitlines()), 78)

    def test_an_earlier_card_is_replaced_and_hidden_files_kept(self) -> None:
        out = self.tmp / "again"
        (out / "scenes").mkdir(parents=True)
        (out / "scenes" / "gone.cue").write_bytes(b"old")
        (out / ".fseventsd").mkdir()
        show = write_show(self.tmp / "one.yaml", [probe("glow")])
        with (
            mock.patch.object(bc, "CARD_DIR", out),
            mock.patch.object(bc, "SHOW", show),
            contextlib.redirect_stdout(io.StringIO()) as said,
        ):
            self.assertEqual(bc.main([]), 0)
        self.assertIn("show: glow", said.getvalue())
        self.assertFalse((out / "scenes" / "gone.cue").exists())
        self.assertTrue((out / ".fseventsd").is_dir())
        offer = (out / bc.CARD_OFFER).read_text(encoding="utf-8")
        self.assertIn(f"{bc.REPO}/releases\n", offer, "no tag: the releases page")
        self.assertIn("the release that carries firmware", offer)


class TestTheTag(unittest.TestCase):
    def test_a_release_tag_passes_whole_and_anything_else_is_refused(self) -> None:
        for tag in ("v0.2.0", "v1.10.3", "v0.2.0-rc.1", "v0.0.0-dryrun.7"):
            self.assertEqual(bc.release_tag(tag), tag)
        for bad in ("0.2.0", "v0.2", "v0.2.0/../x", "v0.2.0-", "v0.2.0 ", "vx.y.z"):
            with self.subTest(tag=bad), self.assertRaises(argparse.ArgumentTypeError):
                bc.release_tag(bad)


class TestRefusals(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_a_scene_that_needs_a_song_never_reaches_a_card(self) -> None:
        song = probe("song", audio_file="tracks/song.mp3")
        show = write_show(self.tmp / "s.yaml", [probe("glow"), song])
        out = self.tmp / "card"
        with self.assertRaisesRegex(SystemExit, r"song: audio_file: tracks/song\.mp3"):
            bc.build(out, show)
        self.assertFalse(out.exists(), "refused before anything was written")

    def test_an_onset_pulse_is_a_song_too(self) -> None:
        beat = probe("beat", pulse=[{"synth": "onset_low", "zones": ["door"]}])
        show = write_show(self.tmp / "s.yaml", [beat])
        with self.assertRaisesRegex(SystemExit, "beat: pulse synth onset_low"):
            bc.build(self.tmp / "card", show)

    def test_a_directory_with_someone_elses_files_is_refused(self) -> None:
        out = self.tmp / "card"
        out.mkdir()
        (out / "holiday.jpg").write_bytes(b"x")
        show = write_show(self.tmp / "s.yaml", [probe("glow")])
        with self.assertRaisesRegex(SystemExit, "already holds holiday.jpg"):
            bc.build(out, show)
        self.assertEqual([p.name for p in out.iterdir()], ["holiday.jpg"])
        (self.tmp / "file").write_bytes(b"")
        with self.assertRaisesRegex(SystemExit, "not a directory"):
            bc.check_target(self.tmp / "file")

    def test_the_shipped_show_itself_passes_the_song_check(self) -> None:
        bc.refuse_tracks(yaml.safe_load(bc.SHOW.read_text(encoding="utf-8")))

    def test_a_leap_day_offer_ends_on_the_28th(self) -> None:
        self.assertEqual(bc.years_later(dt.date(2028, 2, 29), 3), dt.date(2031, 2, 28))
        self.assertEqual(bc.years_later(dt.date(2026, 10, 3), 3), dt.date(2029, 10, 3))


class TestTheOwnerPageLinksTheCard(unittest.TestCase):
    def test_the_page_names_the_files_this_tool_writes(self) -> None:
        names = re.findall(r"'([\w-]+\.txt)':'", OWNER_PAGE)
        self.assertEqual(
            {f"licenses/{n}" for n in names}, {bc.CARD_NOTICES, bc.CARD_OFFER}
        )
        self.assertIn("fetch('/api/files?d=licenses')", OWNER_PAGE)
        self.assertIn("<a href=/sd/licenses/${x[0]}>", OWNER_PAGE)


if __name__ == "__main__":
    unittest.main()
