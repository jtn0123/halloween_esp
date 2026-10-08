"""The show as card files — the emitter that replaced the scene scripts.

Imported by gen_esphome.py, which used to turn each scene into an ESPHome
script: publish current_scene, one lambda of base-state assignments, a volume
call, `sfx`, a wait for the speaker, and then a `delay:` and a lambda per cue,
chopped into `cont_<id>_N` continuations. Twelve of those were 744
LambdaActions, 573 DelayActions and 85 ScriptExecuteActions on the S3 build's
link map — about 23 KB of static internal RAM and 744 compiled lambdas in a
flash budget of 1,835,008 bytes that v5.66 had already spent 1,311,643 of.

What this writes instead, under `audio/card/scenes/`, for `sd_sync scenes` to
push into the card's `scenes/` directory beside the audio:

    show.man    the manifest (tools/scene_manifest.py): id, audio token,
                volume, length, loop flag — every per-scene literal a
                script carried — and, since v5.77, each scene's place in
                the evening playlist
    <id>.cue    that scene's base look and every cue, in the SAME format a
                raw imported song's `.cue` uses (tools/cue_file.py), so the
                firmware has one reader for both (firmware/castle_cues.h)

The device side is firmware/castle_scenes.h + castle_scenes.yaml: one generic
`scene_run`. A scene edit is a publish now, not a build and an OTA.

PULSE_CAP does not apply here. It was a fact about how many actions a script
could afford, so a long song's beat stream was thinned to its strongest 200
hits; a file in PSRAM pays nothing for the rest, and tools/gen_previewer.py
stopped thinning in the same commit so the desk still shows what the castle
plays (docs/PARITY.md).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import cue_file
import scene_manifest
import shipped_show
from pulse_expand import pulse_cues


def scene_cues(
    scene: Mapping[str, Any], markers: Mapping[str, Any]
) -> list[Mapping[str, Any]]:
    """Every cue this scene plays: the authored ones plus the expanded pulse
    streams, every hit of them. The list the deleted `emit_scene` walked,
    minus its thinning — tests/test_scene_cue_equivalence.py is what holds
    the two equal."""
    return list(scene.get("cues") or []) + list(pulse_cues(scene, markers))


def write(
    doc: Mapping[str, Any], out_dir: Path, markers: Mapping[str, Any]
) -> tuple[int, int]:
    """Write show.man and one .cue per scene under `out_dir`.

    Answers (scenes, total cues). Stale cue files from a renamed or deleted
    scene are removed: they would otherwise sit in the publish directory and
    be pushed to the card forever, where the runner cannot see them (nothing
    names them) but `/api/files` lists them and the card pays for them.
    """
    zone_ids: Sequence[str] = [z["id"] for z in doc["zones"]]
    out_dir.mkdir(parents=True, exist_ok=True)
    keep = {f"{s['id']}.cue" for s in doc["scenes"]}
    for stale in out_dir.glob("*.cue"):
        if stale.name not in keep:
            stale.unlink()
    total = 0
    for scene in doc["scenes"]:
        cues = scene_cues(scene, markers)
        (out_dir / f"{scene['id']}.cue").write_bytes(
            cue_file.encode(scene, cues, zone_ids)
        )
        total += len(cues)
    (out_dir / "show.man").write_bytes(
        scene_manifest.encode(doc["scenes"], scene_manifest.evening_order(doc))
    )
    return len(doc["scenes"]), total


def fallback_header(doc: Mapping[str, Any]) -> str:
    """generated/fallback_scenes.h — what the firmware knows WITHOUT a card.

    Three things, all tiny, all about the empty-card case (a fourth, the ids
    quoted for the v5.74 fallback page to splice in, went with that page in
    v5.75 — the owner's page reads them from /api/status):

      kFallbackSceneIdsCsv  the ids, for the boot seeding of
                            /api/scene's known-id list when there is no
                            manifest to read (castle_sd_common.yaml)
      kFallbackEveningCsv   the evening playlist with no manifest to read
                            it from (v5.77; the card's is in show.man)
      kFallbackZones        the FIRST scene's zone records, byte for byte as
                            cue_file.encode writes them — 24 bytes of flash,
                            applied through castle_cues::apply_zones, so the
                            look a cardless castle wears is provably the look
                            that scene's own cue file would have set

    One look, not twelve: this is "a Halloween decoration must still glow",
    not a second copy of the show.

    And only the scenes that SHIP (tools/shipped_show.py), since v5.77: a
    castle with no card cannot play an imported song anyway, and this file
    is compiled into every image, the one sold with a castle included — so
    the yard's show and the shipped show generate it byte for byte alike,
    and no song's name is in a binary that leaves the house.
    """
    keep = set(shipped_show.shipped_ids(doc))
    scenes = [s for s in doc["scenes"] if s["id"] in keep]
    ids = [str(s["id"]) for s in scenes]
    evening = [sid for sid in scene_manifest.evening_order(doc) if sid in keep]
    zone_ids = [z["id"] for z in doc["zones"]]
    # The zone block of the first scene's cue file: encode it and cut the
    # header off, so this cannot drift from what the device reads at run time.
    blob = cue_file.encode(scenes[0], [], zone_ids)
    at = cue_file.HEADER.size
    zones = blob[at : at + cue_file.ZONE.size * len(zone_ids)]
    return (
        "// GENERATED BY tools/gen_esphome.py — DO NOT EDIT\n"
        "#pragma once\n"
        "#include <cstdint>\n"
        f'inline constexpr const char kFallbackSceneIdsCsv[] = "{",".join(ids)}";\n'
        f'inline constexpr const char kFallbackEveningCsv[] = "{",".join(evening)}";\n'
        f"// {scenes[0]['id']}'s base look — {len(zone_ids)} packed"
        " cue_file.py Zone records.\n"
        "inline constexpr uint8_t kFallbackZones[] = {"
        + ", ".join(f"0x{b:02x}" for b in zones)
        + "};\n"
    )
