"""The card-loaded light show: one song's cues as a file, not as firmware.

A scene in scenes.yaml becomes an ESPHome script, and every script costs the
S2 about 9 KB of internal RAM it does not have — which is the whole reason
the show stops at twelve scenes and a long song keeps 200 of its hits
(PULSE_CAP). Neither limit is about the SHOW. The card has 31 GB and the
PSRAM has 2 MB free; what was scarce was only the place the cues were kept.

So a song's cues can be a file beside its audio: `<track>.cue`. The firmware
(firmware/castle_cues.h) loads it into PSRAM when the track is played and
walks it on the speaker's clock, writing the same zone globals a generated
script writes. Same numbers, same render loop, no script, no ceiling.

Layout, little-endian, fixed-width so the device needs no parser:

    header   16 B  "CCUE", version u8, zones u8, pad u16, count u32,
                   duration_ms u32
    zone     8 B   x zones: effect u8, level% u8, center i8, overlay u8,
                   palette u8, pad u8, phase*100 u16
    record  16 B   x count: t_ms u32, op|mode<<4 u8, zone mask u8, then
                   set:    effect u8, level% u8 (255 = leave), 8 B pad
                   strike: intensity*1000 u16, decay*10000 u16,
                           attack_ms u16, colour*100 u8 x4

Every scaled field carries exactly the digits gen_esphome.py prints into a
lambda (`:.3f`, the 4-digit tempo decay, `:.2f`), so the float the device
divides back out is the float the script literal would have been.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from effect_vocab import EFFECT_IDS, FLASH_MODE_IDS, OVERLAY_IDS, PALETTE_IDS
from pulse_expand import DEFAULT_DECAY, WHITE

MAGIC = b"CCUE"
VERSION = 1
HEADER = struct.Struct("<4sBBHII")
ZONE = struct.Struct("<BBbBBBH")
SET = struct.Struct("<IBBBB8x")
STRIKE = struct.Struct("<IBBHHH4B")
OP_SET, OP_STRIKE = 1, 2
LEVEL_KEEP = 255
#: What the device will load (castle_cues.h kMaxRecords): 512 KB of PSRAM.
MAX_RECORDS = 32768


def _scaled(x: float, by: int, top: int) -> int:
    """Half-up like round3 — Python's round() is half-even and the digits
    here must be the ones the generators print."""
    return max(0, min(top, math.floor(float(x) * by + 0.5)))


def _mask(zones: Sequence[str], zone_ids: Sequence[str], sid: str) -> int:
    out = 0
    for z in zones:
        if z not in zone_ids:
            raise SystemExit(f"scene {sid}: cue names unknown zone {z!r}")
        out |= 1 << zone_ids.index(z)
    return out


def _effect(name: str, sid: str) -> int:
    if name not in EFFECT_IDS:
        raise SystemExit(f"scene {sid}: unknown effect {name!r}")
    return EFFECT_IDS[name]


def _record(cue: Mapping[str, Any], zone_ids: Sequence[str], sid: str) -> bytes:
    t = int(cue["t"])
    if cue["op"] == "set":
        level = _scaled(cue["level"], 100, 100) if "level" in cue else LEVEL_KEEP
        return SET.pack(
            t, OP_SET, _mask([cue["zone"]], zone_ids, sid),
            _effect(cue["effect"], sid), level,
        )  # fmt: skip
    if cue["op"] != "strike":
        raise SystemExit(f"scene {sid}: unknown cue op {cue['op']!r}")
    targets = (
        cue.get("targets") or ([cue["zone"]] if cue.get("zone") else None) or zone_ids
    )
    mode = FLASH_MODE_IDS.get(cue.get("pixels", "all"), 0)
    col = cue.get("color", WHITE)
    return STRIKE.pack(
        t, OP_STRIKE | (mode << 4), _mask(targets, zone_ids, sid),
        _scaled(cue.get("intensity", 1.0), 1000, 65535),
        _scaled(cue.get("decay", DEFAULT_DECAY), 10000, 10000),
        max(0, min(65535, int(cue.get("attack", 0)))),
        *(_scaled(col[k], 100, 255) for k in range(4)),
    )  # fmt: skip


def encode(
    scene: Mapping[str, Any], cues: Sequence[Mapping[str, Any]], zone_ids: Sequence[str]
) -> bytes:
    """`scene`'s base state plus `cues` (authored + expanded pulses), sorted
    by time with the authored order kept inside a tie — the order
    gen_esphome.py's stable sort gives the script."""
    sid = scene["id"]
    ordered = sorted(cues, key=lambda c: c["t"])
    if len(ordered) > MAX_RECORDS:
        raise SystemExit(
            f"scene {sid}: {len(ordered)} cues, the device loads {MAX_RECORDS}"
        )
    levels = scene.get("levels") or {}
    detail = scene.get("zones") or {}
    out = [
        HEADER.pack(
            MAGIC, VERSION, len(zone_ids), 0, len(ordered), int(scene["duration_ms"])
        )
    ]
    for z in zone_ids:
        d = detail.get(z) or {}
        out.append(
            ZONE.pack(
                _effect((scene.get("base") or {}).get(z, "off"), sid),
                _scaled(levels.get(z, 1.0), 100, 100),
                _effect(d["center"], sid) if d.get("center") else -1,
                OVERLAY_IDS.get(d.get("overlay", "none"), 0),
                PALETTE_IDS.get(d.get("palette", "haunt"), 0),
                0,
                _scaled(d.get("phase", 0.0), 100, 65535),
            )
        )
    out += [_record(c, zone_ids, sid) for c in ordered]
    return b"".join(out)


def decode(blob: bytes) -> dict[str, Any]:
    """The file as plain numbers — what the tests and the before/after
    picture read, and the reference for what castle_cues.h must see."""
    magic, version, zones, _pad, count, duration = HEADER.unpack_from(blob, 0)
    if magic != MAGIC or version != VERSION:
        raise ValueError("not a version-1 castle cue file")
    at = HEADER.size
    if len(blob) != at + zones * ZONE.size + count * SET.size:
        raise ValueError("cue file length does not match its header")
    base = []
    for _ in range(zones):
        eff, level, center, overlay, palette, _p, phase = ZONE.unpack_from(blob, at)
        base.append(
            {"effect": eff, "level": level / 100, "center": center,
             "overlay": overlay, "palette": palette, "phase": phase / 100}
        )  # fmt: skip
        at += ZONE.size
    records: list[dict[str, Any]] = []
    for _ in range(count):
        t, op, mask = struct.unpack_from("<IBB", blob, at)
        if op & 15 == OP_SET:
            _t, _o, _m, eff, level = SET.unpack_from(blob, at)
            records.append(
                {"t": t, "op": "set", "mask": mask, "effect": eff,
                 "level": None if level == LEVEL_KEEP else level / 100}
            )  # fmt: skip
        else:
            _t, _o, _m, amt, decay, attack, *col = STRIKE.unpack_from(blob, at)
            records.append(
                {"t": t, "op": "strike", "mask": mask, "mode": op >> 4,
                 "intensity": amt / 1000, "decay": decay / 10000,
                 "attack": attack, "color": [c / 100 for c in col]}
            )  # fmt: skip
        at += SET.size
    return {"duration_ms": duration, "zones": base, "records": records}


def loaded_count(path: Any) -> int:
    """How many cues castle_cues.h load() would hold for the file at `path`
    — 0 when it would refuse it (no file, wrong version, wrong length). The
    emulator's /api/status says `cues` with this, so it cannot drift from
    the header without tests/test_cue_file_cxx.py going red first."""
    try:
        blob = Path(path).read_bytes()
        doc = decode(blob)
    except (OSError, ValueError, struct.error):
        return 0
    ok = len(doc["zones"]) == 3 and len(doc["records"]) <= MAX_RECORDS
    return len(doc["records"]) if ok else 0
