"""The scene manifest: the show's table of contents, on the card.

Its sibling is tools/cue_file.py, and the two answer different halves of one
question. A cue file is what ONE scene looks like — the base state and every
strike, on a clock. This is the list of scenes that exist at all, and the
handful of numbers a generated ESPHome script used to carry as literals:
which audio file to stream, how loud, how long the timeline runs, and whether
it re-runs itself at the end.

Until v5.67 all of that was compiled in. Twelve scenes came to 744
LambdaActions, 573 DelayActions and 85 ScriptExecuteActions — about 23 KB of
static internal RAM and 744 compiled lambdas' worth of flash, on a board
whose flash is the resource that binds (1,311,643 of a 1,835,008-byte OTA
slot at v5.66). The show did not need to be in the image; it needed to be
somewhere the firmware could reach it. The card is 31 GB.

So `/sd/scenes/show.man` names the show, `/sd/scenes/<id>.cue` holds each
scene's cues, and firmware/castle_scenes.h reads one entry at a time from a
96-byte stack buffer. Adding, retiming or re-levelling a scene is a publish,
not a build and an OTA.

Layout, little-endian, fixed-width so the device needs no parser and no heap:

    header 16 B  "CSMF", version u8, count u8, pad u16, entry_size u32,
                 reserved u32
    entry  96 B  x count: id char[40] (NUL-terminated), audio char[48]
                 (NUL-terminated, the `sfx` track token — no extension),
                 duration_ms u32, volume_pct u16, loops u8, flags u8

`entry_size` is in the header rather than implied so a future field is a
version bump the old firmware REFUSES rather than misreads: castle_scenes.h
checks magic, version, entry_size and the file's exact length, the same four
checks castle_cues::load makes. `count` is a u8 and capped at MAX_SCENES,
which is the same twelve tools/check_loc.py enforces on scenes.yaml.

volume_pct is whole percent because that is the precision /api/volume and
rig.h's kMaxVolumePct already work in; the float a script printed
(`0.45f`) round-trips through it exactly at two decimal places, which is all
scenes.yaml ever writes.
"""

from __future__ import annotations

import struct
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

MAGIC = b"CSMF"
VERSION = 1
HEADER = struct.Struct("<4sBBHII")
ENTRY = struct.Struct("<40s48sIHBB")
#: What the device will load (castle_scenes.h kMaxScenes) — and the same
#: twelve tools/check_loc.py's SCENE_LIMIT allows in scenes.yaml.
MAX_SCENES = 12
#: Room for the NUL. The longest id in the show today is 32 characters
#: ("the_ballad_of_the_witches__road_"), and its audio token is "10_" plus
#: that, so both fields are sized with a scene name's worth of slack.
ID_MAX = 39
AUDIO_MAX = 47

# The two numbers castle_scenes.h static_asserts. A struct format edited
# without the header is the one mistake that would go out silently.
if HEADER.size != 16 or ENTRY.size != 96:
    raise RuntimeError(
        "the manifest layout is firmware/castle_scenes.h's, byte for byte"
    )


def _fixed(value: str, limit: int, what: str) -> bytes:
    raw = value.encode()
    if not raw:
        raise SystemExit(f"scene manifest: empty {what}")
    if len(raw) > limit:
        raise SystemExit(
            f"scene manifest: {what} {value!r} is {len(raw)} bytes, "
            f"the device reads {limit}"
        )
    return raw


def audio_token(index: int, sid: str) -> str:
    """The `sfx` track name for scene `sid`, the NNth in the show.

    One definition, because three places used to spell it: the generated
    scene script, the boot manifest check's stat() list and render_audio's
    output name. `sfx` appends ".mp3" and the /sd/scenes/ prefix itself
    (firmware/generated/audio_sd.yaml).
    """
    return f"{index:02d}_{sid}"


def encode(scenes: Sequence[Mapping[str, Any]]) -> bytes:
    """`scenes` in show order, as the card file. Each mapping needs `id`,
    `duration_ms` and optionally `volume` (0..1, default 0.8) and `loop`."""
    if len(scenes) > MAX_SCENES:
        raise SystemExit(
            f"{len(scenes)} scenes, the device's manifest holds {MAX_SCENES} "
            "(SCENE_LIMIT — see scenes/scenes.yaml's header comment)"
        )
    out = [HEADER.pack(MAGIC, VERSION, len(scenes), 0, ENTRY.size, 0)]
    seen: set[str] = set()
    for i, scene in enumerate(scenes, start=1):
        sid = str(scene["id"])
        if sid in seen:
            raise SystemExit(f"scene manifest: {sid!r} twice")
        seen.add(sid)
        # Half-up to whole percent, the way the rest of the toolchain rounds
        # (cue_file._scaled): 0.455 is 46%, not 45%.
        vol = min(100, max(0, int(float(scene.get("volume", 0.8)) * 100 + 0.5)))
        out.append(
            ENTRY.pack(
                _fixed(sid, ID_MAX, "scene id"),
                _fixed(audio_token(i, sid), AUDIO_MAX, "audio name"),
                int(scene["duration_ms"]),
                vol,
                1 if scene.get("loop") else 0,
                0,
            )
        )
    return b"".join(out)


def decode(blob: bytes) -> list[dict[str, Any]]:
    """The manifest as plain numbers — what the tests, the emulator and the
    reference trace read. Raises ValueError on anything castle_scenes.h
    would refuse, so the two cannot disagree about what a valid file is."""
    if len(blob) < HEADER.size:
        raise ValueError("too short to be a scene manifest")
    magic, version, count, _pad, entry_size, _res = HEADER.unpack_from(blob, 0)
    if magic != MAGIC or version != VERSION:
        raise ValueError("not a version-1 castle scene manifest")
    if entry_size != ENTRY.size:
        raise ValueError("scene manifest entry size is not this build's")
    if count > MAX_SCENES:
        raise ValueError(f"scene manifest holds {count} scenes, the device reads 12")
    if len(blob) != HEADER.size + count * ENTRY.size:
        raise ValueError("scene manifest length does not match its header")
    out = []
    for i in range(count):
        sid, audio, dur, vol, loops, flags = ENTRY.unpack_from(
            blob, HEADER.size + i * ENTRY.size
        )
        out.append(
            {"id": _cstr(sid), "audio": _cstr(audio), "duration_ms": dur,
             "volume_pct": vol, "loop": bool(loops), "flags": flags}
        )  # fmt: skip
    return out


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode(errors="replace")


def scene_ids(path: Any) -> list[str]:
    """The ids in the manifest at `path`, or [] when there is none this build
    can read. /api/status's `scenes` is this list on both castles — the
    emulator calls it directly, the firmware's castle_scenes::ids_csv is the
    same walk in C, and tests/test_scene_manifest_cxx.py holds them equal."""
    try:
        return [e["id"] for e in decode(Path(path).read_bytes())]
    except (OSError, ValueError, struct.error):
        return []
