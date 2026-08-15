#!/usr/bin/env python3
"""The evening-playlist emitter (#19), imported by gen_esphome.py.

Its own module for the 500-line cap; the seam is honest — this reads the
top-level `show:` block and scene durations, nothing about cues.
"""

from __future__ import annotations


def emit_show_playlist(doc: dict) -> list[str]:
    """#19: the whole evening as one generated script.

    Each scene plays for its full length, then the castle goes quiet for the
    gap (scene_stop — a dark porch between songs reads as anticipation, not
    breakage), then the next starts. The script re-executes itself at the
    end, so one button press covers the night; the web SHOW action stops it.

    Motion-kind scenes are excluded: the PIR owns those, and a playlist that
    plays the jump-scare on schedule teaches the street to ignore it.
    """
    cfg = doc.get("show") or {}
    gap = int(cfg.get("gap_ms", 15000))
    by_id = {s["id"]: s for s in doc["scenes"]}
    order = cfg.get("order") or [s["id"] for s in doc["scenes"]
                                 if s.get("kind") != "motion"]
    out = ["  # ── The evening playlist (#19) ───────────────────"]
    out.append("  - id: show_playlist")
    out.append("    mode: restart")
    out.append("    then:")
    for sid in order:
        if sid not in by_id:
            raise SystemExit(f"show.order names unknown scene {sid!r}")
        out.append(f"      - script.execute: {{id: run_scene, scene: {sid}}}")
        out.append(f"      - delay: {int(by_id[sid]['duration_ms'])}ms")
        out.append("      - script.execute: scene_stop")
        out.append(f"      - delay: {gap}ms")
    out.append("      - script.execute: show_playlist")
    out.append("")
    return out


def emit_manifest_check(doc: dict) -> list[str]:
    """#29: stat() every scene audio file once after mount; missing
    names land in /api/status instead of being discovered as silence
    when the cue fires. Generated: the file list IS the scene list."""
    sd: list[str] = []
    # #29: the boot manifest check. Every audio file the show will ask for,
    # stat()ed once after mount; whatever is missing lands in /api/status
    # (and the remote's status line) instead of being discovered as silence
    # when the cue fires. Generated because the file list IS the scene list.
    sd += ["  - id: manifest_check",
           "    then:",
           "      - lambda: |-",
           "          if (!castle_sd::g_mounted) return;",
           "          std::string missing;",
           "          struct stat st;"]
    for i, scene in enumerate(doc["scenes"], start=1):
        fname = f"{i:02d}_{scene['id']}.mp3"
        sd.append(f"          if (stat(\"/sd/scenes/{fname}\", &st) != 0)"
                  f" missing += missing.empty() ? \"{fname}\" : \",{fname}\";")
    sd += ["          castle_web::set_missing(missing);",
           "          if (!missing.empty())",
           ("            ESP_LOGW(\"castle\", \"MISSING scene audio: %s\","
            " missing.c_str());"),
           ("          else ESP_LOGI(\"castle\", \"manifest: all %d scene files"
            f" present\", {len(doc['scenes'])});"), ""]
    return sd
