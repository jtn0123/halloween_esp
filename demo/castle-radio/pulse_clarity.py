"""Opt-in, offline pulse-clarity experiment. Never replaces a prepared show."""

import argparse
import copy
import json
from itertools import pairwise
from pathlib import Path
from typing import Any

from rich_show import ZONES, cue_file, preview_from_blob

MIN_GAP_MS = 64
DENSE_GAP_MS = 400
RECOVERY = 0.15


def clarify(preview):
    """Prioritize near-coincident hits per fixture, then shorten dense tails.

    Keep the strongest hit at its original time; ties favor the earlier hit.
    A 64 ms exclusion applies only within a fixture. Long authored swells
    (attack >= 160 ms) are protected. Sparse tails are never lengthened.
    """
    result = copy.deepcopy(preview)
    output = [c for c in result["cues"] if c["op"] != "strike"]
    for zone in ZONES:
        strikes = [
            dict(c, targets=[zone])
            for c in result["cues"]
            if c["op"] == "strike" and zone in c["targets"]
        ]
        # Time buckets bound the search; do not chain adjacent low-level hits
        # into a cluster that consumes an arbitrarily long musical passage.
        buckets: dict[int, list[dict[str, Any]]] = {}
        accepted = []
        for c in sorted(strikes, key=lambda c: (-c["intensity"], c["t"])):
            bucket = c["t"] // MIN_GAP_MS
            nearby = [
                v for b in range(bucket - 1, bucket + 2) for v in buckets.get(b, [])
            ]
            protected = c["attack"] >= 160
            if not protected and any(abs(v["t"] - c["t"]) < MIN_GAP_MS for v in nearby):
                continue
            accepted.append(c)
            buckets.setdefault(bucket, []).append(c)
        accepted.sort(key=lambda c: c["t"])
        for c, following in pairwise(accepted):
            gap = following["t"] - c["t"]
            if not MIN_GAP_MS <= gap < DENSE_GAP_MS or c["attack"] >= 160:
                continue
            c["attack"] = min(c["attack"], (gap // 4 // 16) * 16)
            # Reserve one tick for scheduler quantization and the peak frame.
            ticks = max(1, (gap - c["attack"]) // 16 - 1)
            c["decay"] = min(c["decay"], max(0.72, RECOVERY ** (1 / ticks)))
        output.extend(accepted)
    result["cues"] = sorted(output, key=lambda c: c["t"])
    return result


def encode_preview(preview):
    """Round-trip through the real card format before any comparison."""
    scene = dict(preview, duration_ms=preview["dur"])
    cues = [
        dict(c, effect=c["eff"]) if c["op"] == "set" else c for c in preview["cues"]
    ]
    return cue_file.encode(scene, cues, ZONES)


def experiment(source, destination):
    source, destination = Path(source), Path(destination)
    original = source.read_bytes()
    before = preview_from_blob(source.stem, original)
    blob = encode_preview(clarify(before))
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / (source.stem + ".clarity.cue")
    if target.resolve() == source.resolve():
        raise ValueError("An experiment cannot replace its baseline")
    target.write_bytes(blob)
    after = preview_from_blob(source.stem, blob)
    after["name"] = before["name"] + " — pulse clarity experiment"
    target.with_suffix(".show.json").write_text(json.dumps(after))
    assert source.read_bytes() == original
    return {
        "source": str(source),
        "candidate": str(target),
        "before_crc": before["cue_crc32"],
        "after_crc": after["cue_crc32"],
        "before_cues": len(before["cues"]),
        "after_cues": len(after["cues"]),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(experiment(args.source, args.destination), indent=2))
