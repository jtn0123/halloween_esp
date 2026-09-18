"""The SHOW held to the firmware — the card files, by reading the firmware.

tests/test_firmware_contract.py is the WIRE: routes, error strings, status
keys, the validators' byte rules. This is the other contract the same castle
has, and it is one layer down — since v5.67 a scene is two files on the card
(`/sd/scenes/show.man` and `<id>.cue`), so the show's own limits are
wire-visible: the ids `/api/status` lists and the names `/api/scene` accepts
both come out of the manifest, and the names `missing` carries come out of
trying to read it.

Three copies of one layout — the C struct, tools/scene_manifest.py, the
emulator — and this is what holds them equal. The BYTES, and the runner's
behaviour over them, are tests/test_scene_manifest_cxx.py's.

Its own module because test_firmware_contract.py is at the line cap, and this
is a real seam rather than a slice: one file asks "do the two castles answer
the same request the same way", this one asks "do the two castles read the same
card the same way".
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))  # firmware_source

import castle_emu
import castle_emu_events
import scene_manifest
from firmware_source import SD_RTC, SD_SCENES, SD_STATE, grab


class TestTheSceneManifest(unittest.TestCase):
    """v5.67: a scene is a card file, so its LIMITS are wire-visible — the
    ids /api/status lists and the names /api/scene accepts both come out of
    /sd/scenes/show.man. Three copies of one layout (the C struct,
    tools/scene_manifest.py, the emulator) and this is what holds them.
    The BYTES are tests/test_scene_manifest_cxx.py's."""

    def test_the_layout_is_one_layout(self) -> None:
        self.assertEqual(int(grab(r"kVersion = (\d+);", SD_SCENES)),
                         scene_manifest.VERSION)  # fmt: skip
        self.assertEqual(int(grab(r"kMaxScenes = (\d+);", SD_SCENES)),
                         scene_manifest.MAX_SCENES)  # fmt: skip
        # The C sizes are asserted in the header itself (static_assert); what
        # a Python test can add is that Python agrees about them.
        self.assertEqual(int(grab(r"kIdMax = (\d+);", SD_SCENES)),
                         scene_manifest.ID_MAX + 1)  # fmt: skip
        self.assertEqual(int(grab(r"kAudioMax = (\d+);", SD_SCENES)),
                         scene_manifest.AUDIO_MAX + 1)  # fmt: skip
        self.assertIn('memcmp(h.magic, "CSMF", 4)', SD_SCENES)
        self.assertEqual(scene_manifest.MAGIC, b"CSMF")

    def test_the_scene_ceiling_is_the_same_number_everywhere(self) -> None:
        """SCENE_LIMIT: check_loc.py refuses a thirteenth scene in
        scenes.yaml, the studio refuses it at splice time, and the card
        reader refuses a manifest that claims one — three places that have to
        agree or the refusal happens somewhere useless."""
        loc = (ROOT / "tools" / "check_loc.py").read_text()
        self.assertEqual(int(grab(r"SCENE_LIMIT = (\d+)", loc)),
                         scene_manifest.MAX_SCENES)  # fmt: skip
        rs = (ROOT / "core" / "src" / "vocab.rs").read_text()
        self.assertEqual(int(grab(r"SCENE_LIMIT: usize = (\d+)", rs)),
                         scene_manifest.MAX_SCENES)  # fmt: skip

    def test_the_missing_list_is_bounded_on_both_castles(self) -> None:
        """/api/status carries `missing` and is polled once a second, so the
        string cannot be allowed to grow with every failed scene start."""
        self.assertEqual(int(grab(r"kMissingMax = (\d+);", SD_STATE)),
                         castle_emu.MISSING_MAX)  # fmt: skip

    def test_the_missing_list_heals_on_both_castles(self) -> None:
        """v5.68: it also has to SHRINK. Both castles must own a withdrawal,
        or the one that does not carries a stale complaint forever — which is
        exactly the bug: a republished cue file needed a reboot to stop being
        reported missing."""
        self.assertIn("inline void heal_missing(const std::string &name)", SD_STATE)
        self.assertTrue(hasattr(castle_emu.CastleEmu, "heal_missing"))

    def test_scene_missing_is_a_kind_both_rings_know(self) -> None:
        self.assertIn("scene_missing", castle_emu_events.OTHER_KINDS)
        self.assertIn('return "scene_missing";', SD_RTC)


if __name__ == "__main__":
    unittest.main()
