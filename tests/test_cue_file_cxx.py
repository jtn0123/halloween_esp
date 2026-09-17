"""The card cue file, held together: tools/cue_file.py writes it,
firmware/castle_cues.h reads it, and nothing but this test makes the two
agree. tests/cxx/cues_check.cpp runs the real header on the host over a
file this test wrote, and prints the zone globals after every cue; the same
trace is built here from cue_file.decode, and they must match line for line.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import cue_file

SRC = ROOT / "tests" / "cxx" / "cues_check.cpp"
COMPILER = shutil.which("clang++") or shutil.which("g++")
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


def trace(blob: bytes) -> list[str]:
    """What cues_check.cpp prints, from the decoded file."""
    doc = cue_file.decode(blob)
    z = [
        {"effect": b["effect"], "flash": 0.0, "target": 0.0, "rise": 0.0, "decay": 0.9,
         "level": b["level"], "center": b["center"], "overlay": b["overlay"],
         "palette": b["palette"], "phase": b["phase"], "mode": 0, "epoch": 0,
         "col": [1.0] * 4}
        for b in doc["zones"]
    ]  # fmt: skip

    def line(at: int) -> str:
        return f"{at}" + "".join(
            f" | {s['effect']} {s['flash']:.4f} {s['target']:.4f} {s['rise']:.4f} "
            f"{s['decay']:.4f} {s['level']:.2f} {s['center']} {s['overlay']} "
            f"{s['palette']} {s['phase']:.2f} {s['mode']} {s['epoch']} "
            + " ".join(f"{c:.2f}" for c in s["col"])
            for s in z
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
                    continue
                if r["attack"] > 0:
                    s["target"] = r["intensity"]
                    s["rise"] = r["intensity"] * 16.0 / r["attack"]
                else:
                    s["flash"], s["target"] = r["intensity"], 0.0
                s["decay"], s["mode"], s["col"] = r["decay"], r["mode"], r["color"]
                s["epoch"] = (s["epoch"] + 1) % 1000
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

    def test_a_file_that_is_not_this_format_is_refused_whole(self) -> None:
        blob = cue_file.encode(SCENE, CUES, ZONES)
        for bad in (b"", blob[:10], b"XCUE" + blob[4:], blob[:4] + b"\x02" + blob[5:],
                    blob[:-1], blob + b"\x00"):  # fmt: skip
            self.assertEqual(self.run_on(bad), ["refused"])

    def test_the_encoder_keeps_the_generators_digits(self) -> None:
        rec = cue_file.decode(cue_file.encode(SCENE, CUES, ZONES))["records"]
        self.assertEqual([r["t"] for r in rec], [c["t"] for c in CUES])
        self.assertEqual((rec[2]["intensity"], rec[2]["decay"]), (0.551, 0.8711))
        self.assertEqual(rec[2]["color"], [1.0, 0.31, 0.01, 0.07])
        self.assertIsNone(rec[1]["level"])
        self.assertEqual(rec[5]["mask"], 0b111)


if __name__ == "__main__":
    unittest.main()
