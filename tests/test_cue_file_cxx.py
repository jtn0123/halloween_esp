"""The card cue file, held together: tools/cue_file.py writes it,
firmware/castle_cues.h reads it, and nothing but this test makes the two
agree. tests/cxx/cues_check.cpp runs the real header on the host over a
file this test wrote, and prints the zone globals after every cue; the same
trace is built here from cue_file.decode, and they must match line for line.

Version 2 (v5.71) rides the same trace: each zone's castle_layers.h state —
the ornament layer, the train flags of the rate-aware soften, the overlay
clock a look record sets — is printed too, and mirrored below in float32
arithmetic, because that clock is the one value here that is computed on
the device rather than divided straight out of the file.
"""

from __future__ import annotations

import math
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))

import cue_file
import cxx_compiler

SRC = ROOT / "tests" / "cxx" / "cues_check.cpp"
COMPILER = cxx_compiler.COMPILER  # g++ first on Windows; see the module
FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
         "-I", str(ROOT / "tests" / "cxx" / "shim"), "-I", str(ROOT / "firmware")]  # fmt: skip
IN_CI = bool(os.environ.get("CI"))
ZONES = ["towerL", "towerR", "door"]

SCENE: dict[str, Any] = {
    "id": "song",
    "duration_ms": 9000,
    "base": {"towerL": "chill", "towerR": "chill", "door": "ember"},
    "levels": {"towerL": 0.4, "towerR": 0.4, "door": 0.5},
    "zones": {
        "towerL": {"center": "ember", "palette": "haunt"},
        "towerR": {"center": "ember", "palette": "moonlight", "phase": 1.3},
        "door": {"overlay": "sparkle", "palette": "ember"},
    },
}
CUES: list[dict[str, Any]] = [
    {"t": 0, "op": "set", "zone": "towerL", "effect": "seance", "level": 0.7},
    {"t": 0, "op": "set", "zone": "door", "effect": "furnace"},
    {"t": 708, "op": "strike", "zone": "door", "intensity": 0.551, "decay": 0.8711,
     "pixels": "center", "color": [1.0, 0.31, 0.01, 0.07]},
    # Two cues inside one 16 ms frame, the second overwriting the first.
    {"t": 712, "op": "strike", "targets": ["towerL", "towerR"], "intensity": 0.3,
     "decay": 0.945, "attack": 90, "pixels": "scatter", "color": [0.55, 0, 1, 0]},
    {"t": 713, "op": "strike", "targets": ["towerR"], "intensity": 0.999,
     "decay": 0.78, "pixels": "ring", "color": [0, 0.85, 1, 0.12]},
    {"t": 5000, "op": "strike", "intensity": 1.0},  # every zone, every default
    {"t": 8999, "op": "set", "zone": "towerR", "effect": "off", "level": 0.0},
]  # fmt: skip
#: Every version-2 feature, with the train boundary on both sides of 333 ms.
CUES_V2: list[dict[str, Any]] = [
    *CUES[:2],
    {"t": 100, "op": "look", "zone": "towerL", "overlay": "chase", "rate": 0.5},
    {"t": 300, "op": "strike", "zone": "door", "layer": 1, "intensity": 0.6,
     "decay": 0.8, "pixels": "left", "color": [1, 0.2, 0, 0], "attack": 48},
    {"t": 350, "op": "strike", "zone": "door", "intensity": 0.9, "pixels": "right"},
    {"t": 683, "op": "strike", "zone": "door", "layer": 1, "pixels": "top"},
    {"t": 1000, "op": "look", "zone": "towerL", "rate": 2.0},
    {"t": 1200, "op": "look", "targets": ["towerR", "door"], "overlay": "meteor",
     "palette": "moonlight", "center": "none", "rate": 1.0, "head": 0.25},
    {"t": 1210, "op": "look", "zone": "door", "center": "eyes", "head": 0.9},
    {"t": 1500, "op": "look", "zone": "towerL", "rate": 0},
    {"t": 1600, "op": "strike", "intensity": 0.7, "pixels": "bottom"},
    {"t": 1932, "op": "strike", "targets": ["towerL"], "pixels": "left"},
    {"t": 2000, "op": "look", "zone": "towerR"},
    # Arcs (masks 8-15) on both layers, stepping round like a spin.
    {"t": 2100, "op": "strike", "zone": "door", "pixels": "arc0", "intensity": 0.8},
    {"t": 2200, "op": "strike", "zone": "door", "layer": 1, "pixels": "arc3"},
    {"t": 2300, "op": "strike", "targets": ["towerL", "towerR"], "pixels": "arc7",
     "layer": 1, "decay": 0.9},
]  # fmt: skip


def f32(v: float) -> float:
    """One float32 rounding — every step castle_layers.h takes on the device."""
    return float(struct.unpack("<f", struct.pack("<f", v))[0])


def legacy_head(ov: int, tz: float, zi: int) -> float:
    if ov == 3:
        return math.fmod(f32(f32(tz / f32(2.6)) + f32(zi * f32(0.41))), 1.0)
    return math.fmod(f32(f32(tz * f32(0.45)) + f32(zi * f32(0.37))), 1.0)


def head_at(s: dict[str, Any], t: float) -> float:
    v = f32(s["head0"] + f32(s["rate"] * f32(t - s["t0"])))
    return v - math.floor(v)


def look(s: dict[str, Any], r: dict[str, Any], zi: int, clock: float) -> None:
    """castle_cues::apply_look + castle::set_motion."""
    for k in ("overlay", "palette", "center"):
        if r[k] is not None:
            s[k] = r[k]
    rate = -1.0 if r["rate"] is None else f32(r["rate"])
    head = -1.0 if r["head"] is None else f32(r["head"])
    nxt = s["rate"] if rate < 0 else rate
    if nxt > 0:
        tz = f32(clock + s["phase32"])
        now = head_at(s, clock) if s["rate"] > 0 else legacy_head(s["overlay"], tz, zi)
        s["head0"], s["t0"] = (now if head < 0 else head), clock
    s["rate"] = nxt


def strike(s: dict[str, Any], r: dict[str, Any]) -> None:
    """castle_cues::apply_strike, either layer, with the train decision."""
    if r["t"] != s["last"]:
        s["prev"], s["last"] = s["last"], r["t"]
    train = 0 <= s["prev"] <= r["t"] and r["t"] - s["prev"] < 333
    o = "o" if r.get("layer") else ""
    if r["attack"] > 0:
        s[o + "target"] = r["intensity"]
        s[o + "rise"] = r["intensity"] * 16.0 / r["attack"]
    else:
        s[o + "flash"], s[o + "target"] = r["intensity"], 0.0
    s[o + "decay"], s[o + "mode"], s[o + "col"] = r["decay"], r["mode"], r["color"]
    s[o + "epoch"] = (s[o + "epoch"] + 1) % 1000
    s["train1" if o else "train0"] = int(train)


def trace(blob: bytes) -> list[str]:
    """What cues_check.cpp prints, from the decoded file."""
    doc = cue_file.decode(blob)
    z = [
        {"effect": b["effect"], "flash": 0.0, "target": 0.0, "rise": 0.0, "decay": 0.9,
         "level": b["level"], "center": b["center"], "overlay": b["overlay"],
         "palette": b["palette"], "phase": b["phase"], "mode": 0, "epoch": 0,
         "col": [1.0] * 4, "phase32": f32(round(b["phase"] * 100) / 100),
         "oflash": 0.0, "otarget": 0.0, "orise": 0.0, "odecay": 0.9, "omode": 0,
         "oepoch": 0, "ocol": [1.0] * 4, "train0": 0, "train1": 0, "last": -1,
         "prev": -1, "rate": 0.0, "head0": 0.0, "t0": 0.0}
        for b in doc["zones"]
    ]  # fmt: skip

    def line(at: int) -> str:
        return (
            f"{at}"
            + "".join(
                f" | {s['effect']} {s['flash']:.4f} {s['target']:.4f} {s['rise']:.4f} "
                f"{s['decay']:.4f} {s['level']:.2f} {s['center']} {s['overlay']} "
                f"{s['palette']} {s['phase']:.2f} {s['mode']} {s['epoch']} "
                + " ".join(f"{c:.2f}" for c in s["col"])
                for s in z
            )
            + "".join(
                f" / {s['oflash']:.4f} {s['otarget']:.4f} {s['orise']:.4f} "
                f"{s['odecay']:.4f} {s['omode']} {s['oepoch']} "
                + " ".join(f"{c:.2f}" for c in s["ocol"])
                + f" {s['train0']} {s['train1']} {s['rate']:.3f} {s['head0']:.3f} "
                f"{s['t0']:.3f}"
                for s in z
            )
        )

    out = [f"loaded {len(doc['records'])}", line(-1)]
    frames: dict[int, list[dict[str, Any]]] = {}
    for r in doc["records"]:
        frames.setdefault(-(-r["t"] // 16) * 16, []).append(r)  # first frame at/after t
    for at in sorted(frames):
        for r in frames[at]:
            for i, s in enumerate(z):
                if not r["mask"] >> i & 1:
                    continue
                if r["op"] == "set":
                    s["effect"] = r["effect"]
                    if r["level"] is not None:
                        s["level"] = r["level"]
                elif r["op"] == "look":
                    # cues_check's clock: the speaker starts 320 ms in.
                    look(s, r, i, f32(f32(320 + r["t"]) / 1000))
                else:
                    strike(s, r)
        out.append(line(at))
    return [*out, "cues OK"]


@unittest.skipIf(COMPILER is None and not IN_CI, "no host C++ compiler")
class TestCueFileOnTheDevice(unittest.TestCase):
    exe: Path
    tmp: tempfile.TemporaryDirectory[str]

    @classmethod
    def setUpClass(cls) -> None:
        if COMPILER is None:
            raise AssertionError("CI is set and no host C++ compiler is on PATH")
        cls.tmp = tempfile.TemporaryDirectory()
        cls.exe = Path(cls.tmp.name) / "cues_check"
        built = subprocess.run([COMPILER, *FLAGS, str(SRC), "-o", str(cls.exe)],
                               capture_output=True, text=True, check=False)  # fmt: skip
        assert built.returncode == 0, built.stderr

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def run_on(self, blob: bytes) -> list[str]:
        with tempfile.TemporaryDirectory() as card:
            (Path(card) / "song.cue").write_bytes(blob)
            run = subprocess.run([str(self.exe), card, "song"],
                                 capture_output=True, text=True, check=False)  # fmt: skip
        self.assertEqual(run.returncode, 0, run.stdout)
        return run.stdout.splitlines()

    def test_the_header_applies_every_cue_as_python_wrote_it(self) -> None:
        blob = cue_file.encode(SCENE, CUES, ZONES)
        self.assertEqual(self.run_on(blob), trace(blob))

    def test_a_v2_file_applies_every_record_as_python_wrote_it(self) -> None:
        """Ornament strikes, half and arc masks, looks and the train boundary."""
        blob = cue_file.encode(SCENE, CUES_V2, ZONES)
        self.assertEqual(blob[4], cue_file.VERSION_2)
        self.assertEqual(self.run_on(blob), trace(blob))

    def test_a_v1_file_still_draws_an_unknown_mask_as_all(self) -> None:
        """A v1 file cannot name an arc (the encoder writes v2 for one), but
        a hand-edited high nibble must still mean "all" to this reader, as it
        did to every v1 reader — never an arc the author did not ask for."""
        blob = bytearray(cue_file.encode(SCENE, CUES, ZONES))
        self.assertEqual(blob[4], cue_file.VERSION)
        at = 16 + 3 * 8 + 2 * 16 + 4
        for mode in (4, 8, 11, 15):
            blob[at] = (blob[at] & 0x0F) | (mode << 4)
            self.assertEqual(cue_file.decode(bytes(blob))["records"][2]["mode"], 0)
            self.assertEqual(self.run_on(bytes(blob)), trace(bytes(blob)))

    def test_a_file_that_is_not_this_format_is_refused_whole(self) -> None:
        blob = cue_file.encode(SCENE, CUES, ZONES)
        for bad in (b"", blob[:10], b"XCUE" + blob[4:], blob[:4] + b"\x03" + blob[5:],
                    blob[:4] + b"\x00" + blob[5:], blob[:-1], blob + b"\x00"):  # fmt: skip
            self.assertEqual(self.run_on(bad), ["refused"])

    def test_a_v2_record_with_an_op_it_does_not_know_refuses_the_file(self) -> None:
        """A v1 file drew any op as a strike and still does; a v2 file that
        names an op this build does not know is refused whole, never guessed."""
        blob = bytearray(cue_file.encode(SCENE, CUES_V2, ZONES))
        at = 16 + 3 * 8 + 2 * 16 + 4  # the third record's op byte
        blob[at] = (blob[at] & 0xF0) | 4
        self.assertEqual(self.run_on(bytes(blob)), ["refused"])
        with self.assertRaises(ValueError):
            cue_file.decode(bytes(blob))
        v1 = bytearray(cue_file.encode(SCENE, CUES, ZONES))
        v1[16 + 3 * 8 + 2 * 16 + 4] = 4  # strike -> "op 4": still a strike in v1
        self.assertEqual(self.run_on(bytes(v1)), trace(bytes(v1)))

    def test_the_encoder_keeps_the_generators_digits(self) -> None:
        rec = cue_file.decode(cue_file.encode(SCENE, CUES, ZONES))["records"]
        self.assertEqual([r["t"] for r in rec], [c["t"] for c in CUES])
        self.assertEqual((rec[2]["intensity"], rec[2]["decay"]), (0.551, 0.8711))
        self.assertEqual(rec[2]["color"], [1.0, 0.31, 0.01, 0.07])
        self.assertIsNone(rec[1]["level"])
        self.assertEqual(rec[5]["mask"], 0b111)


if __name__ == "__main__":
    unittest.main()
