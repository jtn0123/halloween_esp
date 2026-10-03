"""The licence files on a sold castle's card, served alike by both castles.

v5.77 puts the firmware notices and the GPLv3 written source offer on the
buyer's card (tools/buyer_card.py, licenses/), and the owner page links
each one the card holds. Two things had to change for that to be readable,
and both live on two castles that must not drift:

  * `.txt` is served as text/plain; charset=utf-8. It was
    application/octet-stream, which a browser downloads rather than shows.
    That is sd_web_site.h's content_type() and the emulator's TYPES
    (tools/castle_emu_flash.py). The two tables had already drifted once:
    the emulator never learned `.opus`, which v5.76 added to the board.
    So the whole table is compared here, not only the new row.
  * the owner page asks /api/files?d=licenses which of the two files are
    there. That listing, and the files themselves, are put to the C and
    the emulator over identical cards.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

import buyer_card
from castle_emu_flash import TYPES
from firmware_web_harness import WebPairCase

SITE = (ROOT / "firmware" / "sd_web_site.h").read_text(encoding="utf-8")


def c_types() -> dict[str, str]:
    body = SITE[SITE.index("inline const char *content_type(") :]
    body = body[: body.index("\n}\n")]
    return dict(re.findall(r'if \(ends\("(\.\w+)"\)\) return "([^"]+)";', body))


class TestTheTypeTable(unittest.TestCase):
    def test_the_emulator_types_every_suffix_the_board_does(self) -> None:
        self.assertEqual(TYPES, c_types())
        self.assertEqual(TYPES[".txt"], "text/plain; charset=utf-8")


class TestLicencesOnTheCard(WebPairCase):
    NOTICES = "THIRD-PARTY NOTICES — the castle's firmware image\n".encode()
    OFFER = b"WRITTEN OFFER FOR SOURCE CODE\n"

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        for card in (cls.pair.card_c, cls.pair.card_e):
            lic = card / "licenses"
            lic.mkdir()
            (card / buyer_card.CARD_NOTICES).write_bytes(cls.NOTICES)
            (card / buyer_card.CARD_OFFER).write_bytes(cls.OFFER)
            (card / "whisper.opus").write_bytes(b"OggS")

    def test_each_file_reads_as_text_in_the_browser(self) -> None:
        for name, body in (
            (buyer_card.CARD_NOTICES, self.NOTICES),
            (buyer_card.CARD_OFFER, self.OFFER),
        ):
            with self.subTest(name=name):
                r = self.same("GET", f"/sd/{name}".encode())
                self.assertEqual((r.status, r.body), (200, body))
                self.assertEqual(r.ctype, "text/plain; charset=utf-8")

    def test_the_listing_the_owner_page_reads_names_both(self) -> None:
        # Not self.same: the board lists in readdir order (hash order on
        # Linux CI, sorted on APFS) and the emulator sorts. The owner page
        # looks each name up, so order is not part of the contract — the
        # entries are, and both castles must give the same ones.
        c, e = self.pair.both("GET", b"/api/files?d=licenses")
        self.assertEqual((c.status, c.ctype, c.extra), (e.status, e.ctype, e.extra))
        self.assertEqual(c.status, 200)

        def entries(body: bytes) -> list[tuple[str, int, bool]]:
            rows = json.loads(body)
            return sorted((r["name"], r["size"], r["dir"]) for r in rows)

        self.assertEqual(entries(c.body), entries(e.body))
        self.assertEqual(
            entries(c.body),
            [
                (Path(buyer_card.CARD_OFFER).name, len(self.OFFER), False),
                (Path(buyer_card.CARD_NOTICES).name, len(self.NOTICES), False),
            ],
        )

    def test_an_opus_song_is_typed_alike(self) -> None:
        r = self.same("GET", b"/sd/whisper.opus")
        self.assertEqual((r.status, r.ctype), (200, "audio/ogg"))


if __name__ == "__main__":
    unittest.main()
