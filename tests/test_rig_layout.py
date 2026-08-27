"""Fixture geometry: the rules the cross-language parity test cannot see.

web/test/rig_parity.ts proves the browser and this module place every pixel
identically, but only for the catalogue's default counts. These cover the
edges around that: overrides, the legacy fallback, and the invariants an
overlay relies on.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import rig_layout as rl

ZIDS = ["towerL", "towerR", "door"]
ZONES: list[dict[str, Any]] = [{"id": z, "channel": i + 1} for i, z in enumerate(ZIDS)]


class TestCounts(unittest.TestCase):
    def test_a_part_decides_its_own_pixel_count(self) -> None:
        """A Ring 16 has sixteen pixels. Letting scenes.yaml claim otherwise
        would generate a strip length the cues do not match."""
        with self.assertRaises(SystemExit):
            rl.layout_of("ring16", 20)

    def test_an_override_equal_to_the_part_is_fine(self) -> None:
        """The desk emits `pixels:` for every zone, including fixed ones."""
        self.assertEqual(rl.layout_of("ring16", 16).n, 16)

    def test_loose_singles_take_a_count_and_are_clamped(self) -> None:
        self.assertEqual(rl.layout_of("mini", 2).n, 2)
        self.assertEqual(rl.layout_of("mini", 99).n, 5)  # only five in the pack
        self.assertEqual(rl.layout_of("mini", 0).n, 1)

    def test_unknown_fixture_stops_the_build(self) -> None:
        with self.assertRaises(SystemExit):
            rl.layout_of("ring24")


class TestZoneLayouts(unittest.TestCase):
    def test_a_zone_without_a_fixture_falls_back_to_pixels_per_zone(self) -> None:
        """An unedited scenes.yaml must still build — that is what keeps the
        rig work from being a flag day."""
        got = rl.zone_layouts(ZONES, 7)
        self.assertEqual([got[z].n for z in ZIDS], [7, 7, 7])
        self.assertEqual(got["door"].center, 0, "seven pixels is a Jewel")

    def test_the_through_hole_build_is_a_row_not_a_jewel(self) -> None:
        """pixels_per_zone: 1 is the 8 mm through-hole option; a single lamp
        has no ring to be the centre of."""
        got = rl.zone_layouts(ZONES, 1)
        self.assertEqual(got["door"].n, 1)
        self.assertIsNone(got["door"].center)

    def test_zones_may_differ_from_each_other(self) -> None:
        zones: list[dict[str, Any]] = [
            {"id": "towerL", "fixture": "jewel7"},
            {"id": "towerR", "fixture": "ring16"},
            {"id": "door", "fixture": "wing32"},
        ]
        got = rl.zone_layouts(zones, 7)
        self.assertEqual([got[z].n for z in ZIDS], [7, 16, 32])


class TestInvariants(unittest.TestCase):
    def test_every_fixture_with_pixels_has_a_core_to_strike(self) -> None:
        """A `pixels: center` strike that lights nothing reads as a dead cue,
        not as a design choice."""
        for fid in rl.FIXTURES:
            lay = rl.layout_of(fid)
            if lay.n == 0:
                continue
            self.assertGreaterEqual(sum(lay.core), 1, fid)

    def test_the_coordinate_tables_are_one_entry_per_pixel(self) -> None:
        """The firmware indexes these with no bounds check."""
        for fid in rl.FIXTURES:
            lay = rl.layout_of(fid)
            for name, table in (
                ("walk", lay.walk),
                ("fall", lay.fall),
                ("core", lay.core),
                ("pos", lay.pos),
            ):
                self.assertEqual(len(table), lay.n, f"{fid}.{name}")

    def test_travel_coordinates_stay_normalised(self) -> None:
        """Overlays scale these by the pixel count; a value outside 0..1 puts
        a chase head off the end of the fixture."""
        for fid in rl.FIXTURES:
            lay = rl.layout_of(fid)
            for v in (*lay.walk, *lay.fall):
                self.assertGreaterEqual(v, 0.0, fid)
                self.assertLessEqual(v, 1.0, fid)

    def test_only_the_jewel_claims_a_centre_pixel(self) -> None:
        """A bare ring has no middle; using index 0 as one would put an ember
        core at whatever point of the circle happened to be wired first."""
        self.assertEqual(rl.layout_of("jewel7").center, 0)
        for fid in ("ring12", "ring16", "stick8", "wing32", "mini"):
            self.assertIsNone(rl.layout_of(fid).center, fid)
