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
      op 1 set:    effect u8, level% u8 (255 = leave), 8 B pad
      op 2 strike: intensity*1000 u16, decay*10000 u16,
                   attack_ms u16, colour*100 u8 x4
      op 3 look:   overlay u8 (255 = keep), palette u8 (255 = keep),
           (v2)    center i8 (-128 = keep, -1 = no centre role), pad u8,
                   rate u16 (milli-turns/s; 0 = legacy clock; 65535 = keep),
                   head u16 (milli-turns 0..999; 65535 = continue), pad u16

Version 2 (firmware v5.71) is version 1 plus three things, and `encode`
writes a 2 ONLY when a record uses one of them — a show that uses none is
the same bytes it always was, which a v5.70 castle still plays:

  - `"layer": 1` on a strike sets bit 7 of its zone mask: the strike lands
    on the ornament layer, which the render ADDS to layer 0;
  - `"pixels"` left/right/top/bottom (masks 4-7) split the fixture by where
    each pixel is drawn, and arc0..arc7 (masks 8-15) light one patch of its
    loop — the high nibble of the op byte holds all sixteen;
  - `"op": "look"`, record op 3 — overlay, palette, centre role and the
    overlay clock (chase head / meteor drip) mid-song.

A v1-only reader (v5.70 and before) refuses a version-2 file WHOLE, as it
refuses any version it does not know: the song plays dark and /api/status
names it `missing`. `decode(blob, versions=(1,))` is that reader.

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
#: What a file that uses no version-2 feature is — every file before v5.71.
VERSION = 1
VERSION_2 = 2
#: What castle_cues.h reads (its kVersion is the last of these).
READ_VERSIONS = (1, 2)
HEADER = struct.Struct("<4sBBHII")
ZONE = struct.Struct("<BBbBBBH")
SET = struct.Struct("<IBBBB8x")
STRIKE = struct.Struct("<IBBHHH4B")
LOOK = struct.Struct("<IBBBBbxHH2x")
OP_SET, OP_STRIKE, OP_LOOK = 1, 2, 3
LEVEL_KEEP = 255
#: v2: a strike's zone mask with this bit lands on layer 1, the ornament.
LAYER_BIT = 0x80
#: v2 look "leave it alone" values (castle_cues.h kByteKeep etc.).
BYTE_KEEP, CENTER_KEEP, WORD_KEEP = 255, -128, 65535
#: Strike masks 0-3 are version 1; 4-15 (halves, arcs) need a v2 reader.
V1_MODES = 4
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


def _targets(cue: Mapping[str, Any], zone_ids: Sequence[str]) -> Sequence[str]:
    """`targets`, else one `zone`, else every zone — plus, for a look only,
    `zones` as another name for `targets` (a strike never read that key, and
    a v1 file must not start to)."""
    named = cue.get("targets") or (cue.get("zones") if cue["op"] == "look" else None)
    return named or ([cue["zone"]] if cue.get("zone") else None) or zone_ids


def _named(
    table: Mapping[str, int], kind: str, cue: Mapping[str, Any], sid: str
) -> int:
    if kind not in cue or cue[kind] is None:
        return BYTE_KEEP
    if cue[kind] not in table:
        raise SystemExit(f"scene {sid}: unknown {kind} {cue[kind]!r}")
    return table[cue[kind]]


def _look(cue: Mapping[str, Any], zone_ids: Sequence[str], sid: str) -> bytes:
    """A v2 look record. Every field is optional; absent means keep (and, for
    `head`, continue from wherever the head is). `rate` is turns per second
    (0 = the legacy clock), `head` turns 0..1, `center` an effect or "none"."""
    center = cue.get("center")
    if "center" not in cue:
        cid = CENTER_KEEP
    else:
        cid = -1 if center in (None, "none") else _effect(center, sid)
    rate = WORD_KEEP
    if cue.get("rate") is not None:
        r = float(cue["rate"])
        rate = 0 if r <= 0 else max(1, _scaled(r, 1000, WORD_KEEP - 1))
    head = WORD_KEEP
    if cue.get("head") is not None:
        head = math.floor(float(cue["head"]) * 1000 + 0.5) % 1000
    return LOOK.pack(
        int(cue["t"]), OP_LOOK, _mask(_targets(cue, zone_ids), zone_ids, sid),
        _named(OVERLAY_IDS, "overlay", cue, sid),
        _named(PALETTE_IDS, "palette", cue, sid), cid, rate, head,
    )  # fmt: skip


def _record(
    cue: Mapping[str, Any], zone_ids: Sequence[str], sid: str
) -> tuple[bytes, bool]:
    """One record, and whether it needs a version-2 reader."""
    t = int(cue["t"])
    if cue["op"] == "set":
        level = _scaled(cue["level"], 100, 100) if "level" in cue else LEVEL_KEEP
        return SET.pack(
            t, OP_SET, _mask([cue["zone"]], zone_ids, sid),
            _effect(cue["effect"], sid), level,
        ), False  # fmt: skip
    if cue["op"] == "look":
        return _look(cue, zone_ids, sid), True
    if cue["op"] != "strike":
        raise SystemExit(f"scene {sid}: unknown cue op {cue['op']!r}")
    mode = FLASH_MODE_IDS.get(cue.get("pixels", "all"), 0)
    layer = cue.get("layer", 0) or 0
    if layer not in (0, 1):
        raise SystemExit(f"scene {sid}: strike layer must be 0 or 1, got {layer!r}")
    col = cue.get("color", WHITE)
    return STRIKE.pack(
        t, OP_STRIKE | (mode << 4),
        _mask(_targets(cue, zone_ids), zone_ids, sid) | (LAYER_BIT if layer else 0),
        _scaled(cue.get("intensity", 1.0), 1000, 65535),
        _scaled(cue.get("decay", DEFAULT_DECAY), 10000, 10000),
        max(0, min(65535, int(cue.get("attack", 0)))),
        *(_scaled(col[k], 100, 255) for k in range(4)),
    ), bool(layer) or mode >= V1_MODES  # fmt: skip


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
    records = [_record(c, zone_ids, sid) for c in ordered]
    version = VERSION_2 if any(v2 for _r, v2 in records) else VERSION
    out = [
        HEADER.pack(
            MAGIC, version, len(zone_ids), 0, len(ordered), int(scene["duration_ms"])
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
    out += [r for r, _v2 in records]
    return b"".join(out)


def _decode_record(blob: bytes, at: int, v2: bool) -> dict[str, Any]:
    t, op, mask = struct.unpack_from("<IBB", blob, at)
    if op & 15 == OP_SET:
        _t, _o, _m, eff, level = SET.unpack_from(blob, at)
        return {"t": t, "op": "set", "mask": mask, "effect": eff,
                "level": None if level == LEVEL_KEEP else level / 100}  # fmt: skip
    if v2 and op & 15 == OP_LOOK:
        _t, _o, _m, ov, pal, center, rate, head = LOOK.unpack_from(blob, at)
        return {"t": t, "op": "look", "mask": mask,
                "overlay": None if ov == BYTE_KEEP else ov,
                "palette": None if pal == BYTE_KEEP else pal,
                "center": None if center == CENTER_KEEP else center,
                "rate": None if rate == WORD_KEEP else rate / 1000,
                "head": None if head == WORD_KEEP else head % 1000 / 1000}  # fmt: skip
    if v2 and op & 15 != OP_STRIKE:
        raise ValueError(f"version-2 record with unknown op {op & 15}")
    # A v1 reader drew any other op as a strike, any mask above 3 as "all",
    # and had no layer bit: a v1 file keeps meaning exactly that.
    mode = op >> 4
    if not v2 and mode >= V1_MODES:
        mode = 0
    _t, _o, _m, amt, decay, attack, *col = STRIKE.unpack_from(blob, at)
    return {"t": t, "op": "strike", "mask": mask & ~LAYER_BIT if v2 else mask,
            "layer": 1 if v2 and mask & LAYER_BIT else 0, "mode": mode,
            "intensity": amt / 1000, "decay": decay / 10000,
            "attack": attack, "color": [c / 100 for c in col]}  # fmt: skip


def decode(blob: bytes, versions: Sequence[int] = READ_VERSIONS) -> dict[str, Any]:
    """The file as plain numbers — what the tests and the before/after
    picture read, and the reference for what castle_cues.h must see.
    `versions` is what the reader reads: `(1,)` is a v5.70 castle."""
    magic, version, zones, _pad, count, duration = HEADER.unpack_from(blob, 0)
    if magic != MAGIC or version not in versions:
        raise ValueError(f"not a castle cue file this reader reads ({versions})")
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
        records.append(_decode_record(blob, at, version >= VERSION_2))
        at += SET.size
    return {"version": version, "duration_ms": duration, "zones": base,
            "records": records}  # fmt: skip


def loads(path: Any) -> bool:
    """Whether castle_cues.h load() would ACCEPT the file at `path` — which
    is not the same question as `loaded_count(path) > 0`, because a valid file
    with no records is a legal base-look-only show. The firmware distinguishes
    the two (a refused file is /api/status `missing`, an empty one is not), so
    the emulator has to as well."""
    try:
        doc = decode(Path(path).read_bytes())
    except (OSError, ValueError, struct.error):
        return False
    return len(doc["zones"]) == 3 and len(doc["records"]) <= MAX_RECORDS


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
