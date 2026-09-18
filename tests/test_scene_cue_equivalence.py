"""The v5.67 migration's proof: a card cue file writes what the script wrote.

Until v5.67 every scene in scenes.yaml was an ESPHome script — a lambda of
base-state assignments, then a `delay:` and a lambda per cue. Now it is a
file on the card that firmware/castle_cues.h walks. The claim being made is
that nothing about the SHOW changed, only where it is kept, and this is the
test that had to exist before the generator could be deleted.

It compares, for every scene in the real scenes.yaml:

  * the base look — the assignments the script's first lambda made, against
    the zone records at the head of the cue file;
  * every cue — the assignments a cue's lambda made at its absolute time,
    against the record at that same time, field by field. `0.551` in a
    lambda and a u16 of 551 divided by 1000 on the device are the same
    number only because cue_file.py scales by the same factors gen_esphome
    printed with, and that is what is checked here.

    Field by field and to the field's own QUANTUM, not digit for digit, for
    one reason worth writing down: a script printed with Python's `format`,
    which rounds a tie to even, and cue_file.py's `_scaled` rounds a tie up
    (half-up is what round3 and the desk do). So a colour channel authored as
    exactly 0.975 went into a lambda as 0.97 and goes into a record as 0.98 —
    one part in 100 of one channel, 2.5/255 of the pixel, and the cue file is
    the side that is closer to the number in scenes.yaml. TOLERANCE names
    each field's quantum; one quantum is allowed and two is a failure, so a
    real drift cannot hide behind this.

The one difference it EXPECTS is PULSE_CAP. A script could afford 200 pulse
hits, so `thin_pulses` dropped the rest; a file in PSRAM pays nothing for
them and keeps every one. So the script's cues must be a subsequence of the
file's, and the surplus must be exactly the hits the cap used to drop.

The script emitter is gone, so the reference side is rebuilt here from
`pulse_dynamics.thin_pulses` and the emitters' own literal formats, which are
named in one place below. That is the part of this test that survives as the
permanent gate: `tests/test_generator_parity.py` holds the file against the
DESK, this holds it against the shape the device used to be told.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import cue_file
import gen_scene_cards
import pulse_dynamics as pd
import yaml
from effect_vocab import EFFECT_IDS, FLASH_MODE_IDS, OVERLAY_IDS, PALETTE_IDS
from pulse_expand import DEFAULT_DECAY, WHITE

#: The generated lambda's epoch bump. Not a number: the record path does the
#: same `(e + 1) % 1000` in C, so what is compared is that both sides bump it
#: for a strike and neither does for a `set`.
EPOCH = "bump"

#: How far apart the two sides may be, per zone global — the resolution
#: cue_file.py stores that field at, and no more. An int field is exact.
TOLERANCE = {
    "zone_effect": 0.0,
    "zone_flash_mode": 0.0,
    "zone_flash": 1e-3,  # intensity*1000 u16
    "zone_flash_target": 1e-3,
    "zone_flash_rise": 1e-3,  # derived from intensity and attack_ms
    "zone_flash_decay": 1e-4,  # decay*10000 u16
    "zone_level": 1e-2,  # level% u8
    "zone_flash_col": 1e-2,  # colour*100 u8
}


def script_writes(
    cue: dict[str, Any], zone_ids: list[str]
) -> dict[tuple[str, int], Any]:
    """What the DELETED `emit_scene` assigned for one cue, as {(global,
    index): value} — the numbers it printed into a lambda literal."""
    out: dict[tuple[str, int], Any] = {}
    if cue["op"] == "set":
        i = zone_ids.index(cue["zone"])
        out[("zone_effect", i)] = EFFECT_IDS[cue["effect"]]
        if "level" in cue:
            out[("zone_level", i)] = float(cue["level"])
        return out
    targets = (
        cue.get("targets") or ([cue["zone"]] if cue.get("zone") else None) or zone_ids
    )
    amt = float(cue.get("intensity", 1.0))
    col = cue.get("color", WHITE)
    dec = float(cue.get("decay", DEFAULT_DECAY))
    mode = FLASH_MODE_IDS.get(cue.get("pixels", "all"), 0)
    attack = int(cue.get("attack", 0))
    for z in targets:
        i = zone_ids.index(z)
        if attack > 0:
            out[("zone_flash_target", i)] = amt
            out[("zone_flash_rise", i)] = amt * 16.0 / attack
        else:
            out[("zone_flash", i)] = amt
            out[("zone_flash_target", i)] = 0.0
        out[("zone_flash_decay", i)] = dec
        out[("zone_flash_mode", i)] = mode
        out[("zone_flash_epoch", i)] = EPOCH
        for k in range(4):
            out[("zone_flash_col", i * 4 + k)] = float(col[k])
    return out


def record_writes(rec: dict[str, Any], zones: int) -> dict[tuple[str, int], Any]:
    """What castle_cues::apply writes for one decoded record — the same
    globals, computed the way the C does."""
    out: dict[tuple[str, int], Any] = {}
    for i in range(zones):
        if not rec["mask"] >> i & 1:
            continue
        if rec["op"] == "set":
            out[("zone_effect", i)] = rec["effect"]
            if rec["level"] is not None:
                out[("zone_level", i)] = rec["level"]
            continue
        amt = rec["intensity"]
        if rec["attack"] > 0:
            out[("zone_flash_target", i)] = amt
            out[("zone_flash_rise", i)] = amt * 16.0 / rec["attack"]
        else:
            out[("zone_flash", i)] = amt
            out[("zone_flash_target", i)] = 0.0
        out[("zone_flash_decay", i)] = rec["decay"]
        out[("zone_flash_mode", i)] = rec["mode"]
        out[("zone_flash_epoch", i)] = EPOCH
        for k in range(4):
            out[("zone_flash_col", i * 4 + k)] = rec["color"][k]
    return out


def close(want: dict[tuple[str, int], Any], got: dict[tuple[str, int], Any]) -> bool:
    """The same writes, each within its field's quantum (TOLERANCE)."""
    if want.keys() != got.keys():
        return False
    for key, a in want.items():
        b = got[key]
        if a == EPOCH or b == EPOCH:
            if a != b:
                return False
            continue
        if abs(float(a) - float(b)) > TOLERANCE[key[0]] + 1e-9:
            return False
    return True


def script_base(scene: dict[str, Any], zone_ids: list[str]) -> list[dict[str, Any]]:
    """The scene's base look as the deleted `zone_sets` printed it."""
    levels = scene.get("levels") or {}
    detail = scene.get("zones") or {}
    out = []
    for z in zone_ids:
        d = detail.get(z) or {}
        out.append(
            {"effect": EFFECT_IDS[scene["base"].get(z, "off")],
             "level": float(levels.get(z, 1.0)),
             "center": EFFECT_IDS[d["center"]] if d.get("center") else -1,
             "overlay": OVERLAY_IDS.get(d.get("overlay", "none"), 0),
             "palette": PALETTE_IDS.get(d.get("palette", "haunt"), 0),
             "phase": float(d.get("phase", 0.0))}
        )  # fmt: skip
    return out


def file_base(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"effect": z["effect"], "level": z["level"], "center": z["center"],
         "overlay": z["overlay"], "palette": z["palette"], "phase": z["phase"]}
        for z in doc["zones"]
    ]  # fmt: skip


def _split(scene: dict[str, Any], cues: list[Any]) -> tuple[list[Any], list[Any]]:
    """`gen_scene_cards.scene_cues` is authored-then-pulses, in that order —
    split it back rather than comparing dicts, which two identical hits would
    confuse."""
    n = len(scene.get("cues") or [])
    return list(cues[:n]), list(cues[n:])


class TestSceneCueEquivalence(unittest.TestCase):
    show: dict[str, Any]
    markers: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        cls.show = yaml.safe_load((ROOT / "scenes" / "scenes.yaml").read_text())
        mf = ROOT / "audio" / "markers.json"
        cls.markers = json.loads(mf.read_text()) if mf.exists() else {}

    def each(self) -> Any:
        zone_ids = [z["id"] for z in self.show["zones"]]
        for scene in self.show["scenes"]:
            cues = gen_scene_cards.scene_cues(scene, self.markers)
            blob = cue_file.encode(scene, cues, zone_ids)
            yield scene, zone_ids, cues, cue_file.decode(blob)

    def test_the_base_look_is_the_first_lambdas(self) -> None:
        for scene, zone_ids, _cues, doc in self.each():
            with self.subTest(scene=scene["id"]):
                want, got = script_base(scene, zone_ids), file_base(doc)
                self.assertEqual(len(want), len(got))
                for a, b in zip(want, got, strict=True):
                    self.assertEqual(
                        (a["effect"], a["center"], a["overlay"], a["palette"]),
                        (b["effect"], b["center"], b["overlay"], b["palette"]),
                    )
                    self.assertAlmostEqual(a["level"], b["level"], delta=1e-2)
                    self.assertAlmostEqual(a["phase"], b["phase"], delta=1e-2)

    def test_the_length_the_script_delayed_to_is_in_the_file(self) -> None:
        for scene, _z, _c, doc in self.each():
            with self.subTest(scene=scene["id"]):
                self.assertEqual(doc["duration_ms"], scene["duration_ms"])

    def test_every_cue_the_script_had_is_in_the_file_digit_for_digit(self) -> None:
        """The heart of it: same time, same zone globals, same digits."""
        zones = len(self.show["zones"])
        for scene, zone_ids, cues, doc in self.each():
            with self.subTest(scene=scene["id"]):
                # What the script was given: the authored cues plus the
                # THINNED pulses, in the stable time order it sorted them by.
                authored, pulses = _split(scene, cues)
                script = sorted(authored + pd.thin_pulses(pulses), key=lambda c: c["t"])
                want = [(c["t"], script_writes(c, zone_ids)) for c in script]
                got = [(r["t"], record_writes(r, zones)) for r in doc["records"]]
                # A subsequence, because the file keeps the pulses the cap
                # dropped. Same times, same writes, same order.
                at = 0
                for t, w in want:
                    while at < len(got) and not (
                        got[at][0] == t and close(w, got[at][1])
                    ):
                        at += 1
                    self.assertLess(
                        at,
                        len(got),
                        f"{scene['id']}: the cue at {t} ms is not in the file",
                    )
                    at += 1
                self.assertEqual(len(got), len(cues))

    def test_the_only_surplus_is_the_hits_pulse_cap_used_to_drop(self) -> None:
        for scene, _z, cues, doc in self.each():
            with self.subTest(scene=scene["id"]):
                authored, pulses = _split(scene, cues)
                kept = pd.thin_pulses(pulses)
                self.assertEqual(
                    len(doc["records"]) - (len(authored) + len(kept)),
                    len(pulses) - len(kept),
                )


if __name__ == "__main__":
    unittest.main()
