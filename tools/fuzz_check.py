#!/usr/bin/env python3
"""Python half of the cross-language dynamics fuzz.

web/test/fuzz_parity.mjs generates random pulse cases, computes the
TypeScript side's strikes, then pipes the same cases here. This runs them
through the REAL generators — gen_esphome.pulse_cues and
gen_previewer.to_previewer — and prints both answers as JSON for the Node
side to compare digit-for-digit.

Reads {"cases": [...]} on stdin; writes {"results": [...]} on stdout.
Anything the generators print (missing-marker notes) is diverted to stderr
so stdout stays parseable.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gen_esphome as ge  # noqa: E402
import gen_previewer as gp  # noqa: E402


def norm(c: dict) -> dict:
    """One spelling for a strike, whichever generator wrote it.

    gen_esphome always writes attack/pixels; gen_previewer omits absent
    fields — both mean the defaults, so the defaults are the spelling.
    """
    return {
        "t": c["t"],
        "targets": list(c.get("targets") or []),
        "ms": c.get("ms", 120),
        "intensity": c.get("intensity"),
        "color": [float(v) for v in c.get("color") or []],
        "decay": c.get("decay"),
        "attack": int(c.get("attack", 0)),
        "pixels": c.get("pixels") or "all",
    }


def run_case(case: dict) -> dict:
    scene = {
        "id": "fuzz", "name": "Fuzz", "kind": "custom",
        "duration_ms": case["dur_ms"], "volume": 0.7,
        "base": {"towerL": "candle", "towerR": "candle", "door": "candle"},
        "pulse": [case["cfg"]],
        "cues": case["gate_cues"],
    }
    markers = {"fuzz": {case["cfg"]["synth"]: case["hits"]}}
    esphome = [norm(c) for c in ge.pulse_cues(scene, markers)]
    prev = gp.to_previewer(scene, 1, "", markers)["cues"]
    previewer = [norm(c) for c in prev
                 if c["op"] == "strike" and "intensity" in c]
    return {"esphome": esphome, "previewer": previewer}


def main() -> None:
    data = json.load(sys.stdin)
    chatter = io.StringIO()
    with contextlib.redirect_stdout(chatter):
        results = [run_case(c) for c in data["cases"]]
    if chatter.getvalue():
        print(chatter.getvalue(), file=sys.stderr, end="")
    json.dump({"results": results}, sys.stdout)


if __name__ == "__main__":
    main()
