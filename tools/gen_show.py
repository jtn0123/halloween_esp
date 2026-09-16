#!/usr/bin/env python3
"""The evening-playlist emitter (#19), imported by gen_esphome.py.

Its own module for the 500-line cap; the seam is honest — this reads the
top-level `show:` block and scene durations, nothing about cues.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: A scene script holds its first cue this long waiting for the speaker task
#: (gen_esphome.py re-exports it for the cue emitter). It lives here because
#: the playlist has to bill the same wait: `run_scene` spends up to this long
#: before its own `duration_ms` timeline starts.
SOUND_WAIT_MS = 1500


def emit_show_playlist(doc: Mapping[str, Any]) -> list[str]:
    """#19: the whole evening as one generated script.

    Each scene plays for its full length — SOUND_WAIT_MS of speaker wait
    plus the authored `duration_ms`, because `run_scene` does not start its
    timeline until the speaker runs — then the castle goes quiet for the
    gap (scene_stop — a dark porch between songs reads as anticipation, not
    breakage), then the next starts. The script re-executes itself at the
    end, so one button press covers the night; the web SHOW action stops it.

    Motion-kind scenes are excluded: the PIR owns those, and a playlist that
    plays the jump-scare on schedule teaches the street to ignore it.
    """
    cfg = doc.get("show") or {}
    gap = int(cfg.get("gap_ms", 15000))
    by_id = {s["id"]: s for s in doc["scenes"]}
    order = cfg.get("order") or [
        s["id"] for s in doc["scenes"] if s.get("kind") != "motion"
    ]
    out = ["  # ── The evening playlist (#19) ───────────────────"]
    out.append("  - id: show_playlist")
    out.append("    mode: restart")
    out.append("    then:")
    for sid in order:
        if sid not in by_id:
            raise SystemExit(f"show.order names unknown scene {sid!r}")
        out.append(f"      - script.execute: {{id: run_scene, scene: {sid}}}")
        # The wait is bounded (a dead speaker times out), so the playlist
        # can be at most SOUND_WAIT_MS long per scene, never short: stopping
        # early clipped the authored tail of every scene.
        hold = SOUND_WAIT_MS + int(by_id[sid]["duration_ms"])
        out.append(f"      - delay: {hold}ms")
        out.append("      - script.execute: scene_stop")
        out.append(f"      - delay: {gap}ms")
    out.append("      - script.execute: show_playlist")
    out.append("")
    return out


def emit_manifest_check(doc: Mapping[str, Any]) -> list[str]:
    """#29: stat() every scene audio file once after mount; missing
    names land in /api/status instead of being discovered as silence
    when the cue fires. Generated: the file list IS the scene list."""
    sd: list[str] = []
    # #29: the boot manifest check. Every audio file the show will ask for,
    # stat()ed once after mount; whatever is missing lands in /api/status
    # (and the remote's status line) instead of being discovered as silence
    # when the cue fires. Generated because the file list IS the scene list.
    sd += [
        "  - id: manifest_check",
        "    then:",
        "      - lambda: |-",
        "          if (!castle_sd::g_mounted) return;",
        "          std::string missing;",
        "          struct stat st;",
    ]
    for i, scene in enumerate(doc["scenes"], start=1):
        fname = f"{i:02d}_{scene['id']}.mp3"
        sd.append(
            f'          if (stat("/sd/scenes/{fname}", &st) != 0)'
            f' missing += missing.empty() ? "{fname}" : ",{fname}";'
        )
    sd += [
        "          castle_web::set_missing(missing);",
        "          if (!missing.empty())",
        ('            ESP_LOGW("castle", "MISSING scene audio: %s", missing.c_str());'),
        (
            '          else ESP_LOGI("castle", "manifest: all %d scene files'
            f' present", {len(doc["scenes"])});'
        ),
        "",
    ]
    return sd
