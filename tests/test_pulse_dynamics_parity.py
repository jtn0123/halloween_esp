"""Per-hit dynamics and vocabulary: the other half of generator parity.

Split from test_generator_parity.py at the 500-line cap along the seam that
was already there: that file checks the basic strike/timeline agreement;
this one checks the per-hit dynamics fields (pixels, ms, colour blending)
and that every language's effect vocabulary is the same list. The shared
fixtures (PULSE_SCENE, MARKERS, the strike extractors) are imported from
the sibling, the same way test_stream_dynamics already does.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from typing import Any, ClassVar

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import effect_vocab as ev
import gen_esphome as ge
import gen_previewer as gp
from test_generator_parity import (
    MARKERS,
    PULSE_SCENE,
    ZIDS,
    esphome_strikes,
    previewer_strikes,
)


def dynamic_strikes(
    side: str, scene: dict[str, Any], markers: dict[str, Any]
) -> list[tuple[Any, ...]]:
    """Strikes including the per-hit dynamics fields (pixels, ms, colour)."""
    if side == "esphome":
        cues = ge.pulse_cues(scene, markers)
    else:
        cues = [
            c
            for c in gp.to_previewer(scene, 1, "", markers)["cues"]
            if c["op"] == "strike"
        ]
    return sorted(
        (
            c["t"],
            tuple(c.get("targets") or ZIDS),
            c["intensity"],
            tuple(float(v) for v in c["color"]),
            c.get("pixels", "all"),
            c.get("ms", 120),
        )
        for c in cues
    )


class TestPulseDynamicsParity(unittest.TestCase):
    """The per-hit dynamics: colour blend, velocity masks, boost, ms.

    Each is arithmetic duplicated across gen_esphome.py, gen_previewer.py and
    web/src/track_lights.ts. These pin the two Python copies to each other and
    to the exact numbers the TypeScript copy is tested against, so all three
    meet at the same values.
    """

    DYN: ClassVar[dict[str, Any]] = {
        "synth": "heartbeat",
        "zone": "door",
        "intensity": 0.6,
        "color": [1.0, 0.0, 0.0, 0.0],
        "color_hot": [1.0, 0.5, 0.0, 0.4],
        "pixels_by_vel": True,
        "ms": 110,
        "boost_at": 0.85,
        "boost_targets": ["towerL", "towerR"],
    }

    def scene(self, **over: Any) -> dict[str, Any]:
        return dict(PULSE_SCENE, pulse=[dict(self.DYN, **over)])

    def test_colour_blends_by_velocity_identically(self) -> None:
        # heartbeat velocities: 1.0, 0.55, 0.91, 0.4
        a = dynamic_strikes("esphome", self.scene(), MARKERS)
        b = dynamic_strikes("previewer", self.scene(), MARKERS)
        self.assertEqual(a, b)
        by_t = {t: col for t, _z, _i, col, _p, _m in a}
        self.assertEqual(by_t[0], (1.0, 0.5, 0.0, 0.4))  # vel 1.0: hot
        self.assertEqual(by_t[153], (1.0, 0.275, 0.0, 0.22))  # vel 0.55
        self.assertEqual(by_t[1600], (1.0, 0.2, 0.0, 0.16))  # vel 0.40

    def test_velocity_picks_the_same_mask(self) -> None:
        masks = {
            t: p
            for t, _z, _i, _c, p, _m in dynamic_strikes(
                "esphome", self.scene(), MARKERS
            )
        }
        # vel 0.40 sits ON the centre/scatter edge; both sides use `<`.
        self.assertEqual(masks, {0: "all", 153: "scatter", 820: "all", 1600: "scatter"})
        self.assertEqual(
            masks,
            {
                t: p
                for t, _z, _i, _c, p, _m in dynamic_strikes(
                    "previewer", self.scene(), MARKERS
                )
            },
        )

    def test_soft_hits_land_on_the_centre(self) -> None:
        m = {"parity": {"heartbeat": [[100, 0.2]]}}
        for side in ("esphome", "previewer"):
            ((_t, _z, _i, _c, pixels, _ms),) = dynamic_strikes(side, self.scene(), m)
            self.assertEqual(pixels, "center")

    def test_boost_spills_onto_the_extra_zones(self) -> None:
        for side in ("esphome", "previewer"):
            zones = {t: z for t, z, *_ in dynamic_strikes(side, self.scene(), MARKERS)}
            # vel 1.0 and 0.91 clear boost_at 0.85; 0.55 and 0.40 do not.
            self.assertEqual(zones[0], ("door", "towerL", "towerR"))
            self.assertEqual(zones[820], ("door", "towerL", "towerR"))
            self.assertEqual(zones[153], ("door",))
            self.assertEqual(zones[1600], ("door",))

    def test_boost_does_not_fire_without_targets(self) -> None:
        s = self.scene()
        del s["pulse"][0]["boost_targets"]
        for side in ("esphome", "previewer"):
            zones = {t: z for t, z, *_ in dynamic_strikes(side, s, MARKERS)}
            self.assertEqual(zones[0], ("door",))

    def test_ms_reaches_both_sides(self) -> None:
        for side in ("esphome", "previewer"):
            self.assertEqual(
                {m for *_rest, m in dynamic_strikes(side, self.scene(), MARKERS)}, {110}
            )

    def test_colour_cycle_agrees_hit_for_hit(self) -> None:
        """`colors:` cycles by hit index, identically on both sides."""
        cyc = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
        s = self.scene(colors=cyc)
        del s["pulse"][0]["color_hot"]  # bare cycle, no blend
        a = dynamic_strikes("esphome", s, MARKERS)
        b = dynamic_strikes("previewer", s, MARKERS)
        self.assertEqual(a, b)
        # heartbeat markers in time order: t=0, 153, 820, 1600 — cycle wraps.
        by_t = {t: col for t, _z, _i, col, _p, _m in a}
        self.assertEqual(by_t[0], (1.0, 0.0, 0.0, 0.0))
        self.assertEqual(by_t[153], (0.0, 1.0, 0.0, 0.0))
        self.assertEqual(by_t[820], (0.0, 0.0, 1.0, 0.0))
        self.assertEqual(by_t[1600], (1.0, 0.0, 0.0, 0.0))

    def test_colour_cycle_blends_toward_hot(self) -> None:
        cyc = [[1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0]]
        s = self.scene(colors=cyc, color_hot=[1.0, 1.0, 0.0, 0.0])
        a = dynamic_strikes("esphome", s, MARKERS)
        self.assertEqual(a, dynamic_strikes("previewer", s, MARKERS))
        by_t = {t: col for t, _z, _i, col, _p, _m in a}
        self.assertEqual(by_t[0], (1.0, 1.0, 0.0, 0.0))  # vel 1.0 -> hot
        self.assertEqual(by_t[153], (0.55, 0.55, 0.45, 0.0))  # vel .55 from blue

    def test_plain_streams_are_untouched(self) -> None:
        """A stream without the new fields renders exactly as before."""
        a = esphome_strikes(PULSE_SCENE, MARKERS)
        b = previewer_strikes(PULSE_SCENE, MARKERS)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 14)


class TestVocabularyAgreement(unittest.TestCase):
    """tools/effect_vocab.py against the two copies a Python test can't import.

    The TS (web/src/effects.ts, types.ts) and C++ (firmware/castle_effects.h)
    vocabularies are parsed from their source text — the technique
    test_firmware_contract.py uses — so nothing here is hand-copied.
    """

    TS = (ROOT / "web" / "src" / "effects.ts").read_text()
    TYPES = (ROOT / "web" / "src" / "types.ts").read_text()
    CXX = (ROOT / "firmware" / "castle_effects.h").read_text()

    @staticmethod
    def ts_array(text: str, name: str) -> list[str]:
        m = re.search(rf"export const {name} = \[([^\]]*)\] as const", text)
        assert m, name
        return re.findall(r'"(\w+)"', m.group(1))

    def test_python_generators_share_the_vocab(self) -> None:
        self.assertIs(ge.EFFECT_IDS, ev.EFFECT_IDS)
        self.assertEqual(set(ge.EFFECT_IDS), gp.KNOWN_EFFECTS)
        for table in (ev.EFFECT_IDS, ev.OVERLAY_IDS, ev.PALETTE_IDS, ev.FLASH_MODE_IDS):
            self.assertEqual(list(table.values()), list(range(len(table))), table)

    def test_effects_ts_implements_exactly_the_vocab(self) -> None:
        body = self.TS.split("export const EFFECTS:", 1)[1].split("\n};", 1)[0]
        impl = re.findall(r"^  (\w+): \(", body, re.MULTILINE)
        self.assertEqual(set(impl), set(ev.EFFECT_IDS))
        union = self.TYPES.split("export type EffectName =", 1)[1].split(";", 1)[0]
        self.assertEqual(re.findall(r'"(\w+)"', union), list(ev.EFFECT_IDS))

    def test_effects_ts_name_arrays_match_in_order(self) -> None:
        self.assertEqual(self.ts_array(self.TS, "OVERLAY_NAMES"), list(ev.OVERLAY_IDS))
        self.assertEqual(self.ts_array(self.TS, "PALETTE_NAMES"), list(ev.PALETTE_IDS))
        self.assertEqual(self.ts_array(self.TS, "FLASH_MODES"), list(ev.FLASH_MODE_IDS))

    def test_firmware_enums_match_names_and_ids(self) -> None:
        eff = {n.lower(): int(i) for n, i in re.findall(r"EFF_(\w+) = (\d+)", self.CXX)}
        self.assertEqual(eff, ev.EFFECT_IDS)
        ov = {n.lower(): int(i) for n, i in re.findall(r"OV_(\w+) = (\d+)", self.CXX)}
        self.assertEqual(ov, ev.OVERLAY_IDS)
        pal = self.CXX.split("constexpr float PALETTES[", 1)[1].split("};", 1)[0]
        self.assertEqual(re.findall(r"//\s*(\w+)", pal), list(ev.PALETTE_IDS))
        # Strike masks are ints 0..3 in C (no enum); the gate must know each.
        modes = {int(m) for m in re.findall(r"mode == (\d)", self.CXX)} | {0}
        self.assertEqual(modes, set(ev.FLASH_MODE_IDS.values()))


if __name__ == "__main__":
    unittest.main(verbosity=2)


if __name__ == "__main__":
    unittest.main()
