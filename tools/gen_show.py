#!/usr/bin/env python3
"""The show-level emitters, imported by gen_esphome.py.

What is here is every generated script that is about the show as a WHOLE
rather than one scene: the blackout, the dispatch-by-name `run_scene` every
caller with only a string goes through, the evening playlist (#19) and the
boot manifest check. Its own module for the 500-line cap; the seam is
honest — nothing here reads a cue.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

#: The YAML fragments the emitters below share with gen_esphome.py's cue
#: emitter, which imports them from here (they moved with `run_scene`).
#: The opener of every generated multi-line lambda action.
LAMBDA = "      - lambda: |-"
#: A script's two fixed lines. `restart` throughout: a scene re-fired while
#: it is running starts again rather than stacking a second copy on the
#: strips.
MODE_RESTART = "    mode: restart"
THEN = "    then:"
SCRIPT_HEAD = (MODE_RESTART, THEN)

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


def emit_dispatch(
    doc: Mapping[str, Any],
    zones: Sequence[Mapping[str, Any]],
    script_ids: Sequence[str],
) -> list[str]:
    """The three scripts that are about the show as a whole rather than one
    scene: `scene_stop`, `run_scene` (dispatch by name) and the evening
    playlist below it.

    Split out of gen_esphome.py in v5.62, which had grown to the 490-line
    pre-commit threshold, along the seam this module already had: that file
    answers "what does one scene DO, cue by cue", this one answers "what can
    the castle be told to do". Nothing here reads a cue.
    """
    out: list[str] = []
    # A single stop script, so "blackout" is one call from anywhere.
    #
    # It clears the whole per-zone texture, not just the base effect. The
    # render loop draws the centre pixel from zone_center whenever that is
    # set, and composites the overlay on top of whatever the base is — so a
    # stop that only zeroed zone_effect left vigil's centre embers burning
    # and the door still sparkling, on a castle that was supposed to be dark
    # (tests/cxx/render_check.cpp shows sparkle/meteor/chase all ADD light
    # to a black base). A pending strike swell is cancelled for the same
    # reason: it would otherwise climb, then fall, after the stop.
    out.append("  # ── Blackout ─────────────────────────────────────")
    out.append("  - id: scene_stop")
    out.extend(SCRIPT_HEAD)
    stop = " ".join(
        f"id(zone_effect)[{i}] = 0; id(zone_center)[{i}] = -1;"
        f" id(zone_overlay)[{i}] = 0; id(zone_flash)[{i}] = 0.0f;"
        f" id(zone_flash_target)[{i}] = 0.0f;"
        for i in range(len(zones))
    )
    out.append(f"      - lambda: '{stop}'")
    # And it stops the scene SCRIPTS, not only their output. Until v5.35 it
    # did not: a looping scene's pending delay survived the stop, re-fired
    # within 30 s, and Vigil walked back on — volume, lights and its wind
    # track — under whatever the operator was doing (every "quiet board"
    # test in docs/ISSUE-ring-flicker.md had it playing underneath).
    out.append(LAMBDA)
    out.extend(f"          id({sid})->stop();" for sid in script_ids)
    out.append("      - media_player.stop:")
    out.append("      - text_sensor.template.publish:")
    out.append("          id: current_scene")
    out.append("          state: 'stop'")
    out.append("      - text_sensor.template.publish:")
    out.append("          id: current_track")
    out.append("          state: ''")
    out.append("")

    # Scene dispatch by NAME, for every caller that only has a string: the
    # web server's /api/scene, the PIR's configurable scene select, tools.
    # Generated so a new scene is automatically reachable everywhere.
    out.append("  # ── Dispatch by name ─────────────────────────────")
    out.append("  - id: run_scene")
    out.append(MODE_RESTART)
    out.append("    parameters:")
    out.append("      scene: string")
    out.append(THEN)
    out.append(LAMBDA)
    # L3 (v5.62): the FIRST line of the dispatch, so every scene start is in
    # the ring whoever asked for it. record_action only ever saw the web
    # mailbox, so a scene the PIR fired, a button started or the evening
    # playlist rolled into was recorded by nothing at all — and those are
    # most of a real night. "stop" and "halt" come through here too and are
    # recorded as themselves: knowing the castle was told to halt at 21:03
    # is worth as much as knowing it was told to play.
    out.append("          castle_web::record_event(castle_web::EventKind::SCENE_START,")
    out.append("                                   scene, esp_timer_get_time());")
    out.append("          // Stop every scene script first. Without this a looping")
    out.append("          // scene's pending delay re-fires AFTER the new scene starts")
    out.append("          // and takes the stage back — two loops fighting forever.")
    out.append("          // Continuations too (cont_*): a scene is several short")
    out.append(
        "          // scripts, see CHUNK — the one mid-delay may be any of them."
    )
    out.extend(f"          id({sid})->stop();" for sid in script_ids)
    # A scene is meant to be seen: a colour, a bench pattern or "off" from the
    # desk takes the strips off the Show effect and nothing handed them back
    # before the next boot (2026-09-14: every scene ran dark on the S3 bring-up
    # after a channel test). "halt" is the one caller that must not relight.
    zs = ", ".join(f"id(zone_{z['id']})" for z in zones)
    out.append(f'          if (scene != "halt") for (auto *z : {{{zs}}}) {{')
    out.append("            if (!z->remote_values.is_on() ||")
    out.append('                z->get_effect_name() != "Show") {')
    out.append('              id(lights_override)->execute("show"); break; } }')
    for j, scene in enumerate(doc["scenes"]):
        kw = "if" if j == 0 else "else if"
        out.append(
            f'          {kw} (scene == "{scene["id"]}") '
            f"id(scene_{scene['id']})->execute();"
        )
    out.append('          else if (scene == "stop") id(scene_stop)->execute();')
    # "halt": the stops above and nothing else — lights keep their texture,
    # audio is untouched. /api/play runs it first so a looping scene's 30 s
    # re-fire cannot take the speakers back from the file the operator chose
    # (a song, the panel's speaker test). A branch here rather than its own
    # script: a script is a static object, and the S2's dram0 is on a diet.
    out.append('          else if (scene == "halt") {}')
    out.append(
        '          else ESP_LOGW("castle", "unknown scene \'%s\'", scene.c_str());'
    )
    out.append("")
    out.extend(emit_show_playlist(doc))
    return out
