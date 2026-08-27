#!/usr/bin/env python3
"""Python half of the cross-language dynamics fuzz.

web/test/fuzz_parity.ts generates random pulse cases, computes the
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
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import gen_esphome as ge
import gen_previewer as gp

ROOT = Path(__file__).resolve().parent.parent
PULSE_DUMP = ROOT / "core" / "target" / "release" / "pulse_dump"


def _pc_line(
    cfg: dict[str, Any], gates: list[tuple[int, str]], beats: list[Any]
) -> str | None:
    """One stream as castle-core's `pc` protocol, or None when the case
    uses a shape the compact protocol cannot carry (non-integer times)."""
    parts = [f"synth={cfg['synth']}"]
    zones = cfg.get("zones") or ([cfg["zone"]] if cfg.get("zone") else None)
    if zones:
        parts.append("zones=" + "+".join(zones))
    for flag, key in (
        ("alternate", "alternate"),
        ("takeover", "takeover"),
        ("drift", "drift"),
        ("pbv", "pixels_by_vel"),
    ):
        if cfg.get(key):
            parts.append(f"{flag}=1")
    if cfg.get("boost_targets"):
        parts.append("boost_targets=" + "+".join(cfg["boost_targets"]))
    parts.extend(
        f"{key}={float(cfg[key])!r}"
        for key in ("boost_at", "intensity", "decay")
        if key in cfg
    )
    if "ms" in cfg:
        parts.append(f"ms={int(cfg['ms'])}")
    if "attack_ms" in cfg:
        parts.append(f"attack={int(cfg['attack_ms'])}")
    if "pixels" in cfg:
        parts.append(f"pixels={cfg['pixels']}")
    if cfg.get("color"):
        parts.append("color=" + ",".join(repr(float(v)) for v in cfg["color"]))
    if cfg.get("color_hot"):
        parts.append("hot=" + ",".join(repr(float(v)) for v in cfg["color_hot"]))
    if cfg.get("colors"):
        parts.append(
            "colors="
            + "|".join(",".join(repr(float(v)) for v in c) for c in cfg["colors"])
        )
    if any(float(b[0]) != int(b[0]) for b in beats):
        return None
    beat_arg = (
        ",".join(
            ":".join(
                [str(int(b[0])), repr(float(b[1]))]
                + ([repr(float(b[2]))] if len(b) > 2 else [])
            )
            for b in beats
        )
        or "-"
    )
    g_arg = ",".join(f"{t}:{n}" for t, n in gates) or "-"
    return f"pc {g_arg} {';'.join(parts)} {beat_arg}"


def rust_strikes(
    scene: dict[str, Any], markers: dict[str, Any]
) -> list[dict[str, Any]] | None:
    """The strikes castle-core computes, normalised — or None when the
    Rust binary is not around (no cargo on this machine) or a case shape
    falls outside the pc protocol. Present, it must agree exactly."""
    if not PULSE_DUMP.exists():
        if shutil.which("cargo") is None:
            return None
        subprocess.run(
            [
                "cargo",
                "build",
                "--release",
                "--quiet",
                "--manifest-path",
                str(ROOT / "core" / "Cargo.toml"),
            ],
            check=True,
            capture_output=True,
            timeout=300,
        )
    gates = ge.section_gates(scene)
    lines = []
    for cfg in scene.get("pulse") or []:
        line = _pc_line(cfg, gates, markers[scene["id"]].get(cfg["synth"], []))
        if line is None:
            return None
        lines.append(line)
    run = subprocess.run(
        [str(PULSE_DUMP)],
        input="\n".join(lines) + "\n",
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    out: list[dict[str, Any]] = []
    for ln in run.stdout.split("\n")[: len(lines)]:
        for cue in filter(None, ln.split("\x1e")):
            t, targets, ms, inten, color, decay, attack, pixels, _note = cue.split(
                "\x1f"
            )
            out.append(
                {
                    "t": int(t),
                    "targets": [] if targets == "-" else targets.split("+"),
                    "ms": int(ms),
                    "intensity": float(inten),
                    "color": [float(v) for v in color.split(",")],
                    "decay": float(decay),
                    "attack": int(attack),
                    "pixels": pixels,
                }
            )
    return out


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
    if "scene_yaml" in case:
        # D5: the DEFAULT styles, through the REAL handover. The random
        # cases pass cfg dicts built by a mirror of sceneYaml's field map,
        # which proves the arithmetic but not the map itself — a TS default
        # sceneYaml forgot to emit would slip through with a green fuzz.
        # Here the actual sceneYaml text is parsed exactly as scenes.yaml
        # would be.
        import yaml

        scene = yaml.safe_load(case["scene_yaml"])[0]
        scene.setdefault("cues", [])
        markers = {scene["id"]: case["hits_by_synth"]}
    else:
        scene = {
            "id": "fuzz",
            "name": "Fuzz",
            "kind": "custom",
            "duration_ms": case["dur_ms"],
            "volume": 0.7,
            "base": {"towerL": "candle", "towerR": "candle", "door": "candle"},
            "pulse": [case["cfg"]],
            "cues": case["gate_cues"],
        }
        markers = {"fuzz": {case["cfg"]["synth"]: case["hits"]}}
    esphome = [norm(c) for c in ge.pulse_cues(scene, markers)]
    prev = gp.to_previewer(scene, 1, "", markers)["cues"]
    previewer = [norm(c) for c in prev if c["op"] == "strike" and "intensity" in c]
    rust = rust_strikes(scene, markers)
    if rust is not None and rust != esphome:
        for i, (a, b) in enumerate(zip(rust, esphome)):
            assert a == b, f"castle-core strike {i} disagrees:\nrust {a}\npy   {b}"
        raise AssertionError(
            f"castle-core cue count {len(rust)} != python {len(esphome)}"
        )
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
