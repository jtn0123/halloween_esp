"""The emulated castle's MAIN LOOP: the 200 ms tick, the mirror, `_apply`.

Split out of castle_emu.py at the 500-line cap, on the seam the firmware
itself has. castle_emu.py is the castle's STATE — the card directory, the
mirrored show state, the one-slot pending-action mailbox and the globals a
reply is built from; this is what the device's main loop DOES with that
state once every APPLY_DELAY_S:

  ticker              castle_sd_common.yaml's `interval: 200ms` — drain the
                      mailbox (the RESTART latch first), mirror, then run
                      the one action that was waiting
  mirror              the mirroring half: the dropped-frame line and the
                      audio clock's start/end transitions (mirror_audio)
  end_finished_scene  castle_scenes.yaml's `wait_until finished` else branch
  apply               the action itself — sd_web_state.h's ActionType switch,
                      one branch per verb, each one a firmware fact
  arm_clock           restart_audio_clock()
  scene_stop          `scene_stop` and the line beside it
  manifest_entry      castle_scenes::find, off the card

Nothing here holds state of its own: every function takes the castle it is
running on, the way castle_emu_status.py's two do. The clock arithmetic these
call is castle_emu_clock.py's, held to sd_web_state.h line by line.

castle_emu.py re-exports APPLY_DELAY_S, MAX_VOLUME_PCT and a `_apply` method,
because tests import them from the castle and the seam is an implementation
detail, not a new contract.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from castle_emu_clock import (
    BYTES_PER_S,
    SOUND_WAIT_S,
    SPEAKER_START_S,
    audio_state,
    silence_until,
)
from cue_file import loaded_count, loads

if TYPE_CHECKING:  # the state half; imported for typing only, so no cycle
    from castle_emu import CastleEmu, _State

#: The device applies queued actions on its main-loop interval.
APPLY_DELAY_S = 0.2
#: The firmware clamps /api/volume to rig.h's kMaxVolumePct — scenes.yaml
#: hardware.audio.max_volume — and so does this. test_castle_emu holds them equal.
MAX_VOLUME_PCT = 100


def arm_clock(st: _State, audio: Path | None) -> None:
    """restart_audio_clock(): a PLAY or a SCENE arms the clock, on state the
    caller locks.

    Armed means playing:true and position_ms:0 from the command itself,
    before any audio exists (firmware/sd_web_state.h:restart_audio_clock),
    and for kSoundWaitUs after it if the speaker is never heard from —
    then one `silent` line and idle. So the arming does NOT depend on the
    card (C3), and a file the card does not have simply never sounds (C4):
    it gets no duration to invent a `sound` event and a moving position_ms
    out of.
    """
    st.track_started = time.monotonic()
    # A file on the card plays for as long as its bytes last (96 kbps); one
    # that is not there has no duration at all, so it never sounds.
    dur = max(1, audio.stat().st_size // BYTES_PER_S) if audio is not None else 0
    st.track_ends = st.track_started + dur
    st.starting_until = st.track_started + SOUND_WAIT_S


def scene_stop(st: _State) -> None:
    """`scene_stop` and the one line beside it, on state the caller locks.

    The firmware publishes scene="stop" (not ""), silences the media player,
    and since v5.58 hands the strips back — a page light show drove them
    through lights_override and they held its colour through a stop.
    """
    st.starting_until = silence_until(
        st.track_started, st.track_ends, st.starting_until, time.monotonic()
    )
    st.scene, st.track, st.light, st.cues = "stop", "", "off", 0
    st.scene_ends, st.scene_loops = 0.0, False


def start_ticker(emu: CastleEmu) -> None:
    """Run the main loop in the daemon thread the castle boots with."""
    threading.Thread(
        target=ticker, args=(emu,), daemon=True, name="castle-emu-tick"
    ).start()


def ticker(emu: CastleEmu) -> None:
    while True:
        time.sleep(APPLY_DELAY_S)
        with emu.state.lock:
            # take_pending(): the restart latch drains first and leaves
            # the slot alone, so a command queued beside it still lands.
            taken: tuple[str, str] | None
            if emu._restart_pending:
                emu._restart_pending = False
                taken = ("RESTART", "")
            else:
                taken, emu._pending = emu._pending, None
        mirror(emu)
        if taken is not None:
            emu.events.record_action(*taken, emu.uptime_ms())
            try:
                apply(emu, *taken)
            finally:
                emu.applied.append(taken)


def mirror(emu: CastleEmu) -> None:
    """The interval's mirroring half: the dropped-frame line (at most one
    a second) and the audio clock's start/end transitions."""
    st = emu.state
    now = time.monotonic()
    with st.lock:
        playing, _pos = audio_state(
            st.track, st.track_started, st.track_ends, st.starting_until, now
        )
        sounding = bool(st.track) and (
            st.track_started + SPEAKER_START_S <= now < st.track_ends
        )
        track = st.track
    t_ms = emu.uptime_ms()
    emu.events.note_light_evictions(t_ms)
    # L10 (v5.62): the track rides along, so `sound` names what the
    # amplifier got and `silent` says how much of it played.
    # The song ended. Only a RAW file loses its name here:
    # castle_sd_common.yaml clears current_track on the tick mirror_audio
    # reports the END and only when current_scene is "stop", so an
    # authored scene keeps naming its track until scene_stop — and a
    # track whose sound never came keeps its name for the whole grace.
    if emu.events.note_audio(sounding, playing, t_ms, track):
        with st.lock:
            if st.scene == "stop":
                st.track = ""
    end_finished_scene(emu, now)


def end_finished_scene(emu: CastleEmu, now: float) -> None:
    """J2 (grade report 2026-09-17 pm): a scene that does not loop is
    OVER when its authored length runs out.

    castle_scenes.yaml's `wait_until finished` had no else branch until
    v5.69: `g_armed` stayed true, the PSRAM cue blob stayed allocated,
    /api/status kept reporting `cues` and naming the scene, and the desk
    and the radio went on suppressing their own light frames because of
    it. The else branch runs `cues_end` (which routes through
    castle_scenes::stop()) and publishes current_scene "stop"; this is
    the same two facts, in the same order, on the emulated castle.

    The zones are not darkened here for the same reason the firmware does
    not darken them here: the audio may still be sounding, and the v5.63
    auto-dark above takes the porch to black on the tick it ends.
    """
    st = emu.state
    with st.lock:
        if st.scene_ends <= 0.0 or st.scene_loops or now < st.scene_ends:
            return
        st.scene, st.cues, st.scene_ends = "stop", 0, 0.0


def manifest_entry(emu: CastleEmu, sid: str) -> dict[str, object] | None:
    """Scene `sid`'s row in the card's manifest, or None — the emulator's
    castle_scenes::find. Read from the card on every start, not cached,
    because the firmware re-reads it too: a scene start already touches the
    card for the audio stream, and nothing may touch it once a song is
    running (docs/ISSUE-ring-flicker.md)."""
    import scene_manifest

    try:
        man = scene_manifest.decode((emu.sd_dir / "scenes" / "show.man").read_bytes())
    except (OSError, ValueError):
        return None
    return next((e for e in man if e["id"] == sid), None)


def apply(emu: CastleEmu, action: str, arg: str) -> None:
    st = emu.state
    with st.lock:
        if action == "VOLUME":
            st.volume = min(int(arg), MAX_VOLUME_PCT)
        elif action == "PLAY":
            _apply_play(emu, st, arg)
        elif action == "SCENE":
            _apply_scene(emu, st, arg)
        elif action == "STOP":
            # Firmware STOP is `scene_stop` only: the evening playlist
            # keeps running and starts the next scene after the gap.
            # Ending the night is SHOW "0" (/api/show/stop), below.
            scene_stop(st)
        elif action == "BLACKOUT":
            # #25, the panic switch: playlist, scene and audio all off.
            scene_stop(st)
            st.show_on = False
        elif action == "SHOW":
            st.show_on = arg == "1"
            if not st.show_on:
                # Quiet means the playlist AND the scene it was mid-way
                # through — castle_sd_common.yaml runs scene_stop too.
                scene_stop(st)
        elif action == "LIGHT":
            st.light = arg
        elif action == "PIRCFG":
            _apply_pircfg(st, arg)
        elif action == "RESTART":
            st.boot = time.monotonic()
            st.scene, st.track, st.show_on = "", "", False
            st.starting_until = 0.0


def _apply_play(emu: CastleEmu, st: _State, arg: str) -> None:
    """The PLAY branch, on state the caller locks: a raw file off the card."""
    f = emu.sd_dir / arg
    st.track = arg
    # A raw file has no scene (v5.52: the firmware publishes
    # "stop" so a live light frame does not stop the file).
    st.scene = "stop"
    arm_clock(st, f if f.is_file() else None)
    # v5.63: cues_begin. A name with a slash has no cue file.
    cue = emu.sd_dir / (arg.rsplit(".", 1)[0] + ".cue")
    st.cues = 0 if "/" in arg or ".." in arg else loaded_count(cue)


def _apply_pircfg(st: _State, arg: str) -> None:
    """The PIRCFG branch: `armed|cooldown|scene`, an empty field untouched."""
    armed, cool, scene = [*arg.split("|"), "", "", ""][:3]
    if armed:
        st.pir["armed"] = armed == "1"
    if cool:
        st.pir["cooldown_s"] = int(cool)
    if scene:
        st.pir["scene"] = scene


def _apply_scene(emu: CastleEmu, st: _State, arg: str) -> None:
    """The SCENE branch of the switch, on state the caller locks — long
    enough to read on its own, and the only branch that touches the card."""
    # J3: not while flash is burning. scene_run's first lambda
    # returns at once when castle_sd::g_quiesce is set, so nothing
    # is published and no card file is opened — while "stop" and
    # "halt" are handled by `run_scene` ABOVE that gate and still
    # work, because stopping the show is what an OTA wants.
    if emu.quiesce and arg not in ("stop", "halt"):
        return
    st.scene, st.cues = arg, 0
    st.scene_ends, st.scene_loops = 0.0, False
    # run_scene hands the strips back to Show (gen_esphome.py);
    # only "halt" leaves whatever a test pattern set.
    if arg != "halt":
        st.light = "show"
    # v5.67: what the scene IS comes off the card. `scene_run`
    # asks the manifest for the audio token, then loads
    # /sd/scenes/<id>.cue through the same castle_cues machinery a
    # raw song uses — so `cues` is non-zero for a scene now, where
    # before it was only ever a raw track's number.
    entry = manifest_entry(emu, arg)
    audio = emu.sd_dir / "scenes" / f"{arg}.mp3"
    if entry is not None:
        audio = emu.sd_dir / "scenes" / f"{entry['audio']}.mp3"
        cue = emu.sd_dir / "scenes" / f"{arg}.cue"
        st.cues = loaded_count(cue)
        # J2: the length and the loop flag, off the card, so this
        # castle knows when the scene is over too. The timeline
        # starts when the speaker is heard on the device
        # (castle_scenes::start_clock after the `wait_until`), so
        # it is billed from the same instant here.
        st.scene_loops = bool(entry["loop"])
        length_ms = entry["duration_ms"]
        assert isinstance(length_ms, int)
        st.scene_ends = time.monotonic() + SPEAKER_START_S + length_ms / 1000.0
        # `loads`, not `st.cues > 0`: a valid file with no records
        # is a base-look-only scene and is not missing anything.
        if loads(cue):
            # v5.68: both halves answered, so a name a failed
            # start left behind is withdrawn.
            emu.heal_missing(arg)
            emu.heal_missing(f"{arg}.cue")
        else:
            emu.events.record(
                "scene_missing", f"{arg}.cue", int(time.monotonic() * 1000)
            )
            emu.note_missing(f"{arg}.cue")
    elif arg not in ("stop", "halt"):
        # The card cannot say what this scene is: the runner wears
        # the compiled-in fallback look and says why, in the ring
        # and in /api/status (castle_scenes.yaml).
        emu.events.record("scene_missing", arg, int(time.monotonic() * 1000))
        emu.note_missing(arg)
    if audio.is_file():
        st.track = audio.name
    # C3: the clock is armed for a scene whether or not its
    # track is on the card. restart_audio_clock() does not look
    # at the card at all — it publishes playing:true from the
    # command, and a scene whose audio failed to sync reads
    # "starting" on the device for the whole grace and then
    # ends once. Deriving `playing` from the file made the
    # emulator answer idle for the same request.
    arm_clock(st, audio if audio.is_file() else None)
