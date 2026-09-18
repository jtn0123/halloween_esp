#!/usr/bin/env python3
"""The show-level emitters, imported by gen_esphome.py.

What is here is every generated script that is about the show as a WHOLE
rather than one scene: the blackout, the dispatch-by-name `run_scene` every
caller with only a string goes through, and the evening playlist (#19). Its
own module for the 500-line cap; the seam is honest — nothing here reads a cue.

v5.67 took two things out of it. The per-scene scripts are gone from the
generator entirely (a scene is a cue file on the card now — tools/
gen_scene_cards.py), so `run_scene` names no scene and this file no longer
has to be regenerated when one is added. And the boot manifest check went
with them: its list of files to stat() WAS the scene list, so it is read from
the card's manifest instead, by a hand-written script in
firmware/castle_scenes.yaml.
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

#: The scene runner holds its first cue this long waiting for the speaker task
#: (firmware/castle_scenes.yaml's `wait_until … timeout:`, and castle_cues.h's
#: kSoundWaitUs). It lives here because the playlist has to bill the same
#: wait: a scene does not start its `duration_ms` timeline until the speaker
#: runs. Three copies of one number, held together by
#: tests/test_firmware_contract.py.
SOUND_WAIT_MS = 1500


def emit_show_playlist(doc: Mapping[str, Any]) -> list[str]:
    """#19: the whole evening as one generated script.

    Each scene plays for its full length — SOUND_WAIT_MS of speaker wait
    plus the running scene's `duration_ms`, read off the card at run time
    (J1) because `run_scene` does not start its
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
    out.append(THEN)
    for sid in order:
        if sid not in by_id:
            raise SystemExit(f"show.order names unknown scene {sid!r}")
        out.append(f"      - script.execute: {{id: run_scene, scene: {sid}}}")
        # The wait is bounded (a dead speaker times out), so the playlist
        # can be at most SOUND_WAIT_MS long per scene, never short: stopping
        # early clipped the authored tail of every scene.
        #
        # J1 (grade report 2026-09-17 pm): read at RUN time, not compiled.
        # `run_scene` dispatches to `scene_run`, whose first action is a
        # lambda — so castle_scenes::begin() has already taken this scene's
        # row off the card by the time this delay is evaluated, and the hold
        # is the length the CARD says. Compiled, a republished duration_ms
        # was cut short or left a gap until the next OTA, which is the one
        # thing "a scene edit is a publish" must not mean. A scene the card
        # cannot name has length 0 and holds only the speaker wait, then
        # scene_stop — the same as before, one gap earlier.
        # No duration in the emitted line, on purpose: an edit to
        # `duration_ms` alone must leave this file byte-identical, or "a
        # scene edit is a publish" would still be regenerating firmware.
        out.append(
            f"      - delay: !lambda 'return {SOUND_WAIT_MS} + "
            f"castle_scenes::length_ms();'   # {sid}"
        )
        out.append("      - script.execute: scene_stop")
        out.append(f"      - delay: {gap}ms")
    out.append("      - script.execute: show_playlist")
    out.append("")
    return out


def emit_dispatch(
    doc: Mapping[str, Any],
    zones: Sequence[Mapping[str, Any]],
) -> list[str]:
    """The three scripts that are about the show as a whole rather than one
    scene: `scene_stop`, `run_scene` (dispatch by name) and the evening
    playlist below it.

    Split out of gen_esphome.py in v5.62, which had grown to the 490-line
    pre-commit threshold. What is left of the seam after v5.67 is this file
    and the playlist: "what does one scene DO, cue by cue" is not generated
    at all any more — it is a cue file on the card, walked by the one generic
    `scene_run` (firmware/castle_scenes.yaml). These three still are, because
    the zone count, the playlist order and its gap are facts about the show
    that live in scenes.yaml.
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
    # And it stops the scene RUNNER, not only its output. Until v5.35 it did
    # not: a looping scene's pending delay survived the stop, re-fired within
    # 30 s, and Vigil walked back on — volume, lights and its wind track —
    # under whatever the operator was doing (every "quiet board" test in
    # docs/ISSUE-ring-flicker.md had it playing underneath).
    #
    # v5.67: one `stop()` where there used to be one per scene script AND one
    # per `cont_<id>_N` continuation — 85 of them — plus the line that gives
    # the running scene's cues back to the PSRAM they came from.
    out.append(LAMBDA)
    out.append("          id(scene_run)->stop();")
    out.append("          castle_scenes::stop();")
    out.append("          castle_web::g_cues.store(0);")
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
    #
    # It used to be an if/else chain over the twelve compiled scene scripts,
    # regenerated whenever the show changed. It no longer names a scene at
    # all: the name goes to `scene_run`, which asks the CARD what it means
    # (firmware/castle_scenes.h). A scene added by a publish is reachable
    # here without a rebuild — which is the whole of v5.67.
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
    out.append("          // Stop the runner first. Without this a looping")
    out.append("          // scene's pending re-run fires AFTER the new scene starts")
    out.append("          // and takes the stage back — two loops fighting forever.")
    out.append("          id(scene_run)->stop();")
    # A scene is meant to be seen: a colour, a bench pattern or "off" from the
    # desk takes the strips off the Show effect and nothing handed them back
    # before the next boot (2026-09-14: every scene ran dark on the S3 bring-up
    # after a channel test). "halt" is the one caller that must not relight.
    zs = ", ".join(f"id(zone_{z['id']})" for z in zones)
    out.append(f'          if (scene != "halt") for (auto *z : {{{zs}}}) {{')
    out.append("            if (!z->remote_values.is_on() ||")
    out.append('                z->get_effect_name() != "Show") {')
    out.append('              id(lights_override)->execute("show"); break; } }')
    out.append('          if (scene == "stop") { id(scene_stop)->execute(); return; }')
    # "halt": the stop above and nothing else — lights keep their texture,
    # audio is untouched. /api/play runs it first so a looping scene's 30 s
    # re-fire cannot take the speakers back from the file the operator chose
    # (a song, the panel's speaker test). The cues go with it, because the
    # file about to play may bring its own.
    out.append('          if (scene == "halt") { castle_scenes::stop(); return; }')
    # Anything else is a name the CARD has to recognise. scene_run logs the
    # ones it does not and wears the built-in look, so an unknown id is a
    # castle that still glows rather than a silent no-op — /api/scene has
    # already 404'd the ones the manifest does not list.
    out.append("          id(scene_run)->execute(scene);")
    out.append("")
    out.extend(emit_show_playlist(doc))
    return out
